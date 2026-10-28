"""Per-backend admission, persistence, idempotency and execution for HaloClu."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time

import backend_registry as backends
import common as c
import complete_delivery
import mimo_reliability
import production_reliability as reliability

CLIENT_KEY = re.compile(r"[A-Za-z0-9_.:-]{8,128}")


def _idempotency_path():
    return c.STATE / "idempotency.json"


def _idempotency():
    return c.load(_idempotency_path(), {}) or {}


def _digest(data: dict, backend_id: str) -> str:
    selected = {
        "project": data.get("project"),
        "instruction": data.get("instruction", ""),
        "max_output": data.get("max_output"),
        "previous": data.get("previous"),
        "revision_mode": data.get("revision_mode"),
        "backend_id": backend_id,
        "quality_profile": data.get("quality_profile") or backends.MIMO_DEFAULT_PROFILE,
        "reasoning_effort": (
            backends.qwen_reasoning_effort(data.get("reasoning_effort"))
            if backends.uses_qwen_adapter(backend_id) else None
        ),
    }
    return hashlib.sha256(
        json.dumps(selected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _client_request_id(data: dict) -> str:
    value = data.get("client_request_id")
    if value is None:
        return "server-" + secrets.token_hex(16)
    if not isinstance(value, str) or not CLIENT_KEY.fullmatch(value):
        raise ValueError("Invalid client request id")
    return value


def _ledger_record(value: str):
    return next((x for x in c.ledger().get("requests", []) if x.get("job") == value), None)


def _settle_if_needed(value: str, status: str):
    if not value:
        return
    record = _ledger_record(value)
    if record and record.get("state") == "IN_FLIGHT":
        c.settle_request(value, {}, status)


def dispatch(app, data: dict):
    c.disk_guard()
    p = app.project(data["project"])
    backend_id = p["backend_id"]
    client_request_id = _client_request_id(data)
    digest = _digest(data, backend_id)
    with c.transaction():
        prior = _idempotency().get(p["id"] + ":" + client_request_id)
        if prior:
            if prior.get("digest") != digest or prior.get("backend_id") != backend_id:
                raise ValueError("Idempotency key already belongs to a different payload")
            return app.visible_job(prior["job_id"])
    expected_version = data.get("project_version")
    if expected_version is not None and (
        type(expected_version) is not int or expected_version != p["version"]
    ):
        raise ValueError("Conversation changed; refresh before sending")
    measured, messages, revision, p = app.meter(data)
    available = backends.available_bytes(backend_id)
    if available < backends.MINIMUM_AVAILABLE_BYTES:
        raise ValueError(
            f"8GiB available RAM reserve missing on {backends.spec(backend_id).node}"
        )
    profile = app.quality_profile(p, data)
    effort = measured.get("reasoning_effort")
    body = backends.payload(
        backend_id, messages, measured["max_output"], p["kind"],
        enable_thinking=True if backends.uses_mimo_adapter(backend_id) else None,
        reasoning_effort=effort,
    )
    with c.transaction(), app.JOBS_LOCK:
        p = app.project(p["id"])
        if expected_version is not None and expected_version != p["version"]:
            raise ValueError("Conversation changed; refresh before sending")
        idem = _idempotency()
        key = p["id"] + ":" + client_request_id
        prior = idem.get(key)
        if prior:
            if prior.get("digest") != digest or prior.get("backend_id") != backend_id:
                raise ValueError("Idempotency key already belongs to a different payload")
            return app.visible_job(prior["job_id"])
        if len(app.BACKEND_QUEUES[backend_id]) >= app.MAX_BACKEND_QUEUE:
            raise ValueError("Backend queue limit reached")

        value = secrets.token_hex(12)
        root = app.folder("jobs", value)
        root.mkdir(parents=True, mode=0o700)
        c.reserve_request(
            value,
            measured["max_output"],
            bool(data.get("smoke", False)) or measured["max_output"] <= 512,
            backend_id,
        )
        snapshot = backends.spec(backend_id).public()
        result = {
            "id": value,
            "project": p["id"],
            "conversation_id": p["id"],
            "conversation_version": p["version"] + 1,
            "backend_id": backend_id,
            "upstream_model_id": snapshot["model_id"],
            "backend_snapshot": snapshot,
            "client_request_id": client_request_id,
            "request_digest": digest,
            "instruction": str(data.get("instruction", "")),
            "quality_profile": profile,
            "reasoning_effort": effort,
            "generation_profile": {
                "schema": "haloclu-generation-profile-v1",
                "quality_profile": profile,
                "attempt_type": "primary",
                "thinking_requested": measured.get("reasoning_requested"),
                "thinking_effective": measured.get("reasoning_effective"),
                "thinking_source": (
                    "request.enable_thinking=true" if backends.uses_mimo_adapter(backend_id)
                    else f"request.reasoning_effort={effort}"
                ),
                "sampling": {key: body.get(key) for key in ("temperature","top_p","top_k","min_p","seed") if key in body},
                "max_output": measured["max_output"],
                "renderer_contract": measured.get("renderer_contract"),
            },
            "revision": revision,
            "status": "QUEUED",
            "queue_mode": "primary",
            "upstream_started": False,
            "measured": measured,
            "content": "",
            "reasoning": "",
            "usage": {},
            "created": time.time(),
            "finish_reason": None,
            "ttft_ms": None,
            "first_final_ms": None,
            "wall_ms": None,
            "memory_samples": [],
            "event_cursor": 0,
            "attempts": [],
            "delivery_manifest": (
                complete_delivery.workflow_manifest(p["kind"], messages)
                if backends.uses_mimo_adapter(backend_id)
                else {"schema": "haloclu-delivery-manifest-v1", "required": False,
                      "required_files": [], "required_sections": []}
            ),
        }
        if data.get("previous"):
            result.update(
                previous=data["previous"],
                revision_mode=data.get("revision_mode", "full"),
            )
            if result["revision_mode"] == "targeted":
                result["parent_chapter_sha256"] = app.w.sha(
                    app.writing_parent(data["previous"])["chapter"]
                )
                result["targeted_revisions"] = (
                    app.job(data["previous"]).get("targeted_revisions", 0) + 1
                )
        c.save(root / "request.json", body)
        c.save(root / "job.json", result)
        p["jobs"].append(value)
        p["version"] += 1
        p["updated"] = time.time()
        c.save(app.folder("projects", p["id"]) / "project.json", p)
        idem[key] = {
            "job_id": value,
            "digest": digest,
            "backend_id": backend_id,
            "created": time.time(),
        }
        c.save(_idempotency_path(), idem)
        app.BACKEND_QUEUES[backend_id].append(value)
        cursor = app.event(
            root,
            {
                "type": "queued",
                "backend_id": backend_id,
                "position": len(app.BACKEND_QUEUES[backend_id]),
            },
        )
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)
    app.SCHEDULER_WAKE.set()
    return app.visible_job(value)


def _terminalize_interrupted(app, value: str, message: str):
    result = app.job(value)
    if result["status"] in app.TERMINAL:
        return
    prior = result["status"]
    result.update(status="INTERRUPTED", active_attempt=None, error=message)
    c.save(app.folder("jobs", value) / "job.json", result)
    if prior == "RECOVERING":
        _settle_if_needed(result.get("recovery", {}).get("ledger_key", ""), "INTERRUPTED")
    _settle_if_needed(value, "INTERRUPTED")
    cursor = app.event(
        app.folder("jobs", value), {"type": "terminal", "job": app.visible_job(value)}
    )
    result["event_cursor"] = cursor
    c.save(app.folder("jobs", value) / "job.json", result)


def reconcile_startup(app):
    records=[]
    for path in (c.STATE / "jobs").glob("*/job.json"):
        item=c.load(path)
        if item and item.get("status") not in app.TERMINAL:
            records.append((float(item.get("created",0)),path,item))
    for _,path,item in sorted(records,key=lambda row:(row[0],row[2].get("id",""))):
        value = item["id"]
        backend_id = backends.validate_id(item.get("backend_id") or backends.DEFAULT)
        item["backend_id"] = backend_id
        # The worker persists upstream_started before opening the native HTTP request.
        # Only a never-started queued item is safe to re-admit after a workbench restart.
        if not item.get("upstream_started", False) and item.get("status") in {
            "QUEUED",
            "IN_FLIGHT",
        }:
            item["status"] = "QUEUED"
            item["upstream_model_id"] = backends.spec(backend_id).model_id
            item["backend_snapshot"] = backends.spec(backend_id).public()
            c.save(path, item)
            if value not in app.BACKEND_QUEUES[backend_id]:
                app.BACKEND_QUEUES[backend_id].append(value)
            continue
        try:
            native_idle = backends.idle(backend_id)
        except Exception:
            native_idle = False
        if native_idle:
            _terminalize_interrupted(
                app,
                value,
                "Workbench restarted after the native request drained; uncertain work was not replayed",
            )
        else:
            item["status"] = "RECONCILING"
            item["error"] = (
                "Workbench restarted while backend ownership was uncertain; prompt was not replayed"
            )
            c.save(path, item)
            app.RECONCILING.add(value)


def _reconcile_pending(app):
    for value in list(app.RECONCILING):
        result = app.job(value)
        try:
            if not backends.idle(result["backend_id"]):
                continue
        except Exception:
            continue
        _terminalize_interrupted(
            app,
            value,
            "Uncertain upstream request drained after workbench restart; prompt was not replayed",
        )
        app.RECONCILING.discard(value)


def _launch_one(app, backend_id: str):
    with app.JOBS_LOCK:
        if app.BACKEND_ACTIVE[backend_id] or not app.BACKEND_QUEUES[backend_id]:
            return
        value = app.BACKEND_QUEUES[backend_id][0]
        result = app.job(value)
        if result["status"] != "QUEUED":
            app.BACKEND_QUEUES[backend_id].popleft()
            return
    try:
        if not backends.idle(backend_id):
            return
        available = backends.available_bytes(backend_id)
        if available < backends.MINIMUM_AVAILABLE_BYTES:
            result["backend_wait_reason"] = "MEMORY_RESERVE"
            c.save(app.folder("jobs", value) / "job.json", result)
            return
    except Exception as exc:
        result["backend_wait_reason"] = "UNAVAILABLE: " + str(exc)
        c.save(app.folder("jobs", value) / "job.json", result)
        return

    with app.JOBS_LOCK:
        if (
            app.BACKEND_ACTIVE[backend_id]
            or not app.BACKEND_QUEUES[backend_id]
            or app.BACKEND_QUEUES[backend_id][0] != value
        ):
            return
        app.BACKEND_QUEUES[backend_id].popleft()
        result = app.job(value)
        if result["status"] != "QUEUED":
            return
        result.pop("backend_wait_reason", None)
        result["status"] = (
            "IN_FLIGHT" if result.get("queue_mode") == "primary" else "RECOVERING"
        )
        c.save(app.folder("jobs", value) / "job.json", result)
        cancel = threading.Event()
        app.ACTIVE[value] = {
            "cancel": cancel,
            "response": None,
            "backend_id": backend_id,
        }
        app.BACKEND_ACTIVE[backend_id] = value
    threading.Thread(target=_run_job, args=(app, value), daemon=True).start()


def _run_job(app, value: str):
    result = app.job(value)
    backend_id = result["backend_id"]
    root = app.folder("jobs", value)
    project = app.project(result["project"])
    body = c.load(root / "request.json")
    cancel = app.ACTIVE[value]["cancel"]
    try:
        if backends.uses_mimo_adapter(backend_id):
            if result.get("queue_mode") in {"recovery", "correction"}:
                mimo_reliability.recover(app, root, project, result, body, cancel)
            else:
                mimo_reliability.generate(app, root, project, result, body, cancel)
        else:
            if result.get("queue_mode") == "recovery":
                reliability.worker(app, root, project, result, body, cancel, False)
            else:
                reliability.generate(app, root, project, result, body, cancel)
    except Exception as exc:
        result = app.job(value)
        result.update(status="ERROR", error=str(exc), active_attempt=None)
        _settle_if_needed(value, "ERROR")
        c.save(root / "job.json", result)
        with app.JOBS_LOCK:
            app.ACTIVE.pop(value, None)
        cursor = app.event(root, {"type": "terminal", "job": app.visible_job(value)})
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)
    finally:
        with app.JOBS_LOCK:
            app.ACTIVE.pop(value, None)
            if app.BACKEND_ACTIVE.get(backend_id) == value:
                app.BACKEND_ACTIVE[backend_id] = None
        app.SCHEDULER_WAKE.set()


def scheduler_loop(app):
    while not app.SCHEDULER_STOP.is_set():
        _reconcile_pending(app)
        for backend_id in backends.SPECS:
            _launch_one(app, backend_id)
        app.SCHEDULER_WAKE.wait(1)
        app.SCHEDULER_WAKE.clear()


def start_scheduler(app):
    reconcile_startup(app)
    thread = threading.Thread(
        target=scheduler_loop,
        args=(app,),
        daemon=True,
        name="haloclu-backend-scheduler",
    )
    thread.start()
    app.SCHEDULER_WAKE.set()
    return thread


def cancel_job(app, value: str):
    value = app.identifier(value)
    result = app.job(value)
    backend_id = result["backend_id"]
    queued_cancel=False
    with app.JOBS_LOCK:
        if result["status"] == "QUEUED":
            try:
                app.BACKEND_QUEUES[backend_id].remove(value)
            except ValueError:
                pass
            result.update(
                status="CANCELLED",
                active_attempt=None,
                error="Cancelled before upstream dispatch",
            )
            c.save(app.folder("jobs", value) / "job.json", result)
            queued_cancel=True
        else:
            active = app.ACTIVE.get(value)
            if active:
                result["status"]="DRAINING"
                c.save(app.folder("jobs",value)/"job.json",result)
                active["cancel"].set()
                reliability.close_response(active.get("response"))
                return {"id": value, "status": "DRAINING", "backend_id": backend_id}
    if queued_cancel:
        _settle_if_needed(value, "CANCELLED")
        cursor = app.event(
            app.folder("jobs", value),
            {"type": "terminal", "job": app.visible_job(value)},
        )
        result["event_cursor"] = cursor
        c.save(app.folder("jobs", value) / "job.json", result)
        return app.visible_job(value)
    return app.visible_job(value)


def recover_job(app, value: str):
    value = app.identifier(value)
    root = app.folder("jobs", value)
    with c.transaction(), app.JOBS_LOCK:
        result = app.job(value)
        backend_id = result["backend_id"]
        label = backends.spec(backend_id).label
        attempts = list(result.get("attempts") or [])
        selected = c.load(root / "selected-delivery.json", {})
        effective_delivery = selected.get("delivery") or result.get("delivery") or {}
        effective_verification = c.load(
            root / "selected-verification.json", result.get("verification") or {}
        )
        correction = (
            backends.uses_mimo_adapter(backend_id)
            and result.get("status") == "COMPLETE"
            and effective_delivery.get("complete") is True
            and effective_verification.get("status") == "FAIL"
        )
        if correction:
            corrections = sum(
                1 for item in attempts
                if str(item.get("name") or "").startswith("correction-")
            )
            if len(attempts) >= mimo_reliability.MAX_NATIVE_ATTEMPTS or corrections >= 4:
                raise ValueError("The bounded MiMo correction budget is exhausted")
            result["recovery_eligible"] = True
            result["recovery_reason"] = {
                "schema": "haloclu-attempt-classification-v1",
                "native_conclusion": "FAIL_TESTS",
                "delivery": "COMPLETE",
                "verification": "FAIL",
                "recoverable": True,
                "corrections_used": corrections,
            }
            queue_mode = "correction"
            trigger = "FAIL_TESTS"
        else:
            if result.get("status") != "INCOMPLETE":
                raise ValueError(f"Only a terminal incomplete {label} job or a failed verified MiMo delivery can be recovered")
            if result.get("recovery", {}).get("attempted") or any(
                x.get("name") == "recovery" for x in attempts
            ):
                raise ValueError(f"The single {label} final-only recovery was already used")
            if backends.uses_mimo_adapter(backend_id):
                body = c.load(root / "request.json", {})
                workflow_kind = app.project(result["project"])["kind"]
                manifest = complete_delivery.workflow_manifest(workflow_kind, body.get("messages", []))
                if workflow_kind == "chat" and result.get("delivery_manifest"):
                    manifest = result["delivery_manifest"]
                elif result.get("delivery_manifest", {}).get("required"):
                    result["superseded_delivery_manifest"] = result["delivery_manifest"]
                result["delivery_manifest"] = manifest
                attempts = attempts or [reliability.legacy_attempt(result)]
                primary = next(
                    (item for item in attempts if item.get("name") == "primary"),
                    attempts[0],
                )
                primary_content = (
                    (root / "primary-final.txt").read_text()
                    if (root / "primary-final.txt").is_file()
                    else result.get("content", "")
                )
                if not primary.get("delivery") and manifest.get("required"):
                    inspected = complete_delivery.inspect_delivery(primary_content, manifest)
                    primary["delivery"] = {
                        key: val for key, val in inspected.items() if not key.startswith("_")
                    }
                decision = complete_delivery.classify_attempt(
                    primary,
                    (result.get("measured") or {}).get("max_output"),
                )
                if not decision["recoverable"]:
                    raise ValueError("This incomplete MiMo job has no supported finalization trigger")
                result["attempts"] = attempts
                result["recovery_eligible"] = True
                result["recovery_reason"] = decision
                trigger = decision["native_conclusion"]
            else:
                if not (
                    result.get("finish_reason") == "length"
                    or not result.get("content", "").strip()
                ):
                    raise ValueError("This incomplete job has no supported finalization trigger")
                trigger = result.get("finish_reason") or "NO_FINAL"
            queue_mode = "recovery"
        if not (root / "request.json").is_file():
            raise ValueError("Original request is missing")
        if not attempts:
            attempts = [reliability.legacy_attempt(result)]
        result["attempts"] = attempts
        result.update(
            status="QUEUED",
            queue_mode=queue_mode,
            upstream_started=False,
            active_attempt=None,
        )
        recovery = dict(result.get("recovery") or {})
        recovery.update(
            attempted=True,
            state="CORRECTION_QUEUED" if correction else "QUEUED",
            backend_id=backend_id,
            trigger=trigger,
        )
        result["recovery"] = recovery
        c.save(root / "job.json", result)
        app.BACKEND_QUEUES[backend_id].append(value)
        cursor = app.event(
            root,
            {
                "type": "recovery",
                "backend_id": backend_id,
                "state": recovery["state"],
                "trigger": trigger,
                "message": (
                    "Consegna completa ma test falliti. MiMo sta correggendo gli artefatti entro il budget."
                    if correction
                    else "Finalizzazione MiMo accodata. Il tentativo originale resta conservato."
                ),
            },
        )
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)
    app.SCHEDULER_WAKE.set()
    return app.visible_job(value)

def statuses(app) -> list[dict]:
    rows = []
    for backend_id in backends.SPECS:
        row = backends.status(backend_id)
        with app.JOBS_LOCK:
            row["active_job"] = app.BACKEND_ACTIVE.get(backend_id)
            row["queue_depth"] = len(app.BACKEND_QUEUES[backend_id])
            row["reconciling"] = [
                value
                for value in app.RECONCILING
                if app.job(value)["backend_id"] == backend_id
            ]
        if row.get("active_job") or row.get("native_busy") is True:
            row["state"] = "busy"
        elif row.get("queue_depth"):
            row["state"] = "queued"
        elif not row.get("available"):
            row["state"] = "offline"
        else:
            row["state"] = "ready"
        rows.append(row)
    return rows
