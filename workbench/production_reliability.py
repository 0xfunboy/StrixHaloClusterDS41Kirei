"""Bounded production recovery and durable streaming for the Gufo workbench.

The primary xhigh attempt remains authoritative evidence. If it reaches the
output cap or its stream stalls before completing a final, exactly one
final-only attempt is allowed. Hidden reasoning is never replayed.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import threading
import time
import urllib.request

import backend_registry as backends
import common as c
import tokens

RECOVERY_MAX_OUTPUT = 16384
STREAM_STALL_SECONDS = 180
SOCKET_READ_TIMEOUT = 240
PROGRESS_PERSIST_SECONDS = 1.0


def event_count(root):
    path = root / "events.jsonl"
    if not path.is_file():
        return 0
    with path.open("rb") as stream:
        return sum(1 for _ in stream)


def close_response(response):
    if response:
        try:
            response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except (AttributeError, OSError):
            pass


def wait_idle(backend_id, timeout=120):
    return backends.wait_idle(backend_id, timeout)


def recovery_instruction(kind):
    common = (
        "PRODUCTION FINALIZATION PASS. The preceding attempt exhausted its "
        "output budget or stalled before delivering a complete final. Do not "
        "repeat planning, analysis, chain of thought, or a file-by-file wish "
        "list. Produce the complete final deliverable now and keep it within "
        "the available response. Use the same language and requirements. If "
        "the entire requested artifact cannot physically fit, say that plainly "
        "and provide one complete, runnable first deliverable plus exact "
        "continuation steps; never end in private planning and never claim "
        "tools or files were inspected when they were not. This workbench Chat "
        "has no filesystem or tool execution. Do not emit tool_call, function, "
        "read_file, list_files, shell, or similar pseudo-tool markup. Answer "
        "directly in the final response."
    )
    if kind in ("code", "node"):
        return common + (
            " Return ONLY the exact edits JSON object required by the original "
            "contract, with no Markdown fence or commentary. The immutable "
            "original files, allowlist and operator-approved checks remain "
            "authoritative."
        )
    if kind == "writing":
        return common + (
            " Return ONLY the exact JSON object required by the active writing "
            "or targeted-revision contract, with no Markdown fence or commentary. "
            "Preserve the source and citation constraints."
        )
    return common


def recovery_request(primary, project, backend_id):
    body = json.loads(json.dumps(primary))
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
        raise ValueError("Recovery requires the original user-terminated message sequence")
    messages[-1]["content"] = (
        messages[-1]["content"].rstrip()
        + "\n\n"
        + recovery_instruction(project["kind"])
        + "\n"
    )
    primary_cap = int(primary.get("max_tokens", 4096))
    cap = min(RECOVERY_MAX_OUTPUT, max(8192, primary_cap * 2))
    capacity = backends.effective_capacity(backend_id)
    if backend_id == backends.DEEPSEEK_KIREI:
        measured = backends.count(backend_id, messages, cap)
        measured.update(
            reasoning_requested="none",
            reasoning_effective="none",
        )
        body = backends.payload(backend_id, messages, cap, project["kind"])
    else:
        measured = tokens.count(messages, cap, capacity)
        measured.update(
            reasoning_requested="none",
            reasoning_effective="none",
            renderer_contract="qwen38-v080-final-only-recovery-v1",
            note=(
                "Conservative xhigh offline count used only as a context upper bound; "
                "native recovery uses reasoning_effort none."
            ),
        )
        body.update(
            messages=messages,
            max_tokens=cap,
            reasoning_effort="none",
            preserve_thinking=False,
            temperature=0.2,
            top_p=0.95,
            top_k=20,
            min_p=0,
            repeat_penalty=1,
            presence_penalty=0,
            frequency_penalty=0,
            seed=int(primary.get("seed", 64004)) + 101,
            cache_prompt=True,
            stream=True,
            return_progress=True,
            stream_options={"include_usage": True},
        )
    return body, measured


def legacy_attempt(result):
    return {
        "name": "primary",
        "status": result.get("status"),
        "finish_reason": result.get("finish_reason"),
        "usage": result.get("usage", {}),
        "ttft_ms": result.get("ttft_ms"),
        "first_final_ms": result.get("first_final_ms"),
        "wall_ms": result.get("wall_ms"),
        "content_chars": len(result.get("content", "")),
        "reasoning_chars": len(result.get("reasoning", "")),
        "raw": "native.sse",
        "legacy": True,
    }


def aggregate_usage(attempts):
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    known = False
    for attempt in attempts:
        usage = attempt.get("usage") or {}
        for key in tuple(total):
            value = usage.get(key)
            if type(value) is int:
                total[key] += value
                known = True
    return total if known else {}


def final_problem(content):
    value = str(content or "")
    if not value.strip():
        return "NO_FINAL_CONTENT"
    lowered = value.lower()
    markers = ("<tool_call>", "</tool_call>", "<function=", "<parameter=", "<tool_response>")
    if any(marker in lowered for marker in markers):
        return "UNSUPPORTED_PSEUDO_TOOL_OUTPUT"
    return None


def consume_attempt(app, root, result, body, cancel, abort, control, name, raw_name, phase, expected_input=None):
    started = time.monotonic()
    response = None
    last_save = started
    local = {
        "name": name,
        "status": "IN_FLIGHT",
        "finish_reason": None,
        "usage": {},
        "ttft_ms": None,
        "first_final_ms": None,
        "content": "",
        "reasoning": "",
        "stream_chunks": 0,
        "prompt_progress": None,
        "raw": raw_name,
        "error": None,
    }
    result["status"] = phase
    result["active_attempt"] = name
    if name == "recovery":
        result["content"] = ""
        result["recovery_reasoning"] = ""
    c.save(root / "job.json", result)
    control.update(active=True, last_progress=time.monotonic(), response=None)
    try:
        # Persist admission before the network effect. After a workbench crash,
        # an uncertain native request is reconciled and never redispatched.
        result["upstream_started"] = True
        result["upstream_started_at"] = time.time()
        result["upstream_attempt"] = name
        c.save(root / "job.json", result)
        response = backends.open_stream(
            result["backend_id"],
            "/v1/chat/completions",
            body,
            SOCKET_READ_TIMEOUT,
        )
        control["response"] = response
        app.ACTIVE[result["id"]]["response"] = response
        with response, (root / raw_name).open("wb") as raw:
            for line in response:
                control["last_progress"] = time.monotonic()
                raw.write(line)
                raw.flush()
                if cancel.is_set() or abort.is_set():
                    break
                if not line.startswith(b"data: ") or line.strip() == b"data: [DONE]":
                    continue
                packet = json.loads(line[6:])
                if packet.get("error"):
                    raise ValueError(str(packet["error"]))
                if packet.get("usage"):
                    local["usage"] = packet["usage"]
                    result["usage"] = local["usage"]
                if packet.get("prompt_progress"):
                    local["prompt_progress"] = packet["prompt_progress"]
                    cursor = app.event(
                        root,
                        {"type": "prefill", "attempt": name, "data": packet["prompt_progress"]},
                    )
                    result["event_cursor"] = cursor
                for choice in packet.get("choices", []):
                    delta = choice.get("delta", {})
                    thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    content = delta.get("content") or ""
                    if thought or content:
                        elapsed = (time.monotonic() - started) * 1000
                        local["stream_chunks"] += 1
                        if local["ttft_ms"] is None:
                            local["ttft_ms"] = elapsed
                        if content and local["first_final_ms"] is None:
                            local["first_final_ms"] = elapsed
                        local["reasoning"] += thought
                        local["content"] += content
                        if name == "primary":
                            result["reasoning"] = local["reasoning"]
                            result["content"] = local["content"]
                        else:
                            result["recovery_reasoning"] = local["reasoning"]
                            result["content"] = local["content"]
                        result["ttft_ms"] = local["ttft_ms"]
                        result["first_final_ms"] = local["first_final_ms"]
                        cursor = app.event(
                            root,
                            {
                                "type": "delta",
                                "attempt": name,
                                "reasoning": thought,
                                "content": content,
                                "metrics": {
                                    "elapsed_ms": elapsed,
                                    "ttft_ms": local["ttft_ms"],
                                    "first_final_ms": local["first_final_ms"],
                                    # One non-empty OpenAI delta normally corresponds to one
                                    # emitted token. The UI labels this live rate approximate;
                                    # terminal usage replaces it with native exact metrics.
                                    "stream_chunks": local["stream_chunks"],
                                },
                            },
                        )
                        result["event_cursor"] = cursor
                    if choice.get("finish_reason"):
                        local["finish_reason"] = choice["finish_reason"]
                        result["finish_reason"] = choice["finish_reason"]
                now = time.monotonic()
                if now - last_save >= PROGRESS_PERSIST_SECONDS:
                    c.save(root / "job.json", result)
                    last_save = now
        if abort.is_set():
            local["status"] = "STALLED"
            local["error"] = f"No upstream progress for {STREAM_STALL_SECONDS} seconds"
        elif cancel.is_set():
            local["status"] = "CANCELLED"
        elif local["finish_reason"] == "stop" and local["content"].strip():
            problem = final_problem(local["content"])
            if problem:
                local["status"] = "UNUSABLE_FINAL"
                local["error"] = problem
            else:
                local["status"] = "COMPLETE"
        else:
            local["status"] = "INCOMPLETE"
        native = local["usage"].get("prompt_tokens")
        if expected_input is not None and native is not None and native != expected_input and result.get("backend_id") == backends.QWEN:
            local["status"] = "ERROR"
            local["error"] = "Native prompt count differs from preflight renderer"
    except Exception as exc:
        if abort.is_set():
            local["status"] = "STALLED"
            local["error"] = f"No upstream progress for {STREAM_STALL_SECONDS} seconds"
        elif cancel.is_set():
            local["status"] = "CANCELLED"
        else:
            local["status"] = "ERROR"
            local["error"] = str(exc)
    finally:
        close_response(response)
        try:
            if response:
                response.close()
        except Exception:
            pass
        control.update(active=False, response=None, last_progress=time.monotonic())
        if result["id"] in app.ACTIVE:
            app.ACTIVE[result["id"]]["response"] = None
    local["wall_ms"] = (time.monotonic() - started) * 1000
    local["content_chars"] = len(local.pop("content"))
    local["reasoning_chars"] = len(local.pop("reasoning"))
    return local


def recoverable_attempt(attempt):
    return attempt.get("finish_reason") == "length" or attempt.get("status") in {"STALLED", "UNUSABLE_FINAL"}


def recovery_key(value):
    return value + "-recovery1"


def reserve_recovery(value, cap, backend_id):
    key = recovery_key(value)
    existing = next((x for x in c.ledger().get("requests", []) if x.get("job") == key), None)
    if existing:
        if existing.get("state") == "IN_FLIGHT":
            raise ValueError("Recovery reservation already exists and requires reconciliation")
        raise ValueError("The single production recovery was already consumed")
    c.reserve_request(key, cap, False, backend_id)
    return key


def worker(app, root, project, result, primary_body, cancel, run_primary=True):
    started = time.monotonic()
    done = threading.Event()
    abort = threading.Event()
    control = {"active": False, "last_progress": started, "response": None}
    reason = {
        "memory": False,
        "timeout": False,
        "stalls": 0,
        "technical": False,
        "drain_failed": False,
    }
    attempts = list(result.get("attempts") or [])
    if not attempts and not run_primary:
        attempts = [legacy_attempt(result)]

    def watch():
        timeout = 1200 if result.get("revision") else 1800
        while not done.wait(2):
            now = time.monotonic()
            sample = {
                "elapsed_s": now - started,
                "backend_id": result["backend_id"],
                "available_bytes": backends.available_bytes(result["backend_id"]),
            }
            result.setdefault("memory_samples", []).append(sample)
            if sample["available_bytes"] < backends.MINIMUM_AVAILABLE_BYTES:
                reason["memory"] = True
                cancel.set()
            if sample["elapsed_s"] > timeout:
                reason["timeout"] = True
                cancel.set()
            if (
                control["active"]
                and not abort.is_set()
                and now - control["last_progress"] > STREAM_STALL_SECONDS
            ):
                reason["stalls"] += 1
                abort.set()
                close_response(control["response"])
            if cancel.is_set():
                close_response(control["response"])
                return

    threading.Thread(target=watch, daemon=True).start()
    try:
        primary = None
        if run_primary:
            result.update(status="IN_FLIGHT", active_attempt="primary")
            primary = consume_attempt(
                app,
                root,
                result,
                primary_body,
                cancel,
                abort,
                control,
                "primary",
                "native.sse",
                "IN_FLIGHT",
                result["measured"]["input_tokens"],
            )
            attempts.append(primary)
            result["attempts"] = attempts
            c.settle_request(
                result["id"],
                primary.get("usage") or {},
                "CANCELLED_MEMORY"
                if reason["memory"]
                else ("ERROR" if reason["timeout"] else primary["status"]),
            )
        else:
            primary = attempts[-1]

        do_recovery = (
            not cancel.is_set()
            and recoverable_attempt(primary)
            and not any(x.get("name") == "recovery" for x in attempts)
        )
        if do_recovery:
            if not wait_idle(result["backend_id"]):
                reason["drain_failed"] = True
                raise RuntimeError("Primary attempt ended but model drain was not confirmed")
            if not (root / "pre-recovery-job.json").exists():
                c.save(root / "pre-recovery-job.json", result)
            abort.clear()
            recovery_body, measured = recovery_request(
                primary_body, project, result["backend_id"]
            )
            key = reserve_recovery(
                result["id"], measured["max_output"], result["backend_id"]
            )
            c.save(root / "recovery-request.json", recovery_body)
            result["recovery_measured"] = measured
            result["recovery"] = {
                "attempted": True,
                "state": "IN_FLIGHT",
                "trigger": primary.get("finish_reason") or primary.get("status"),
                "reasoning_effort": "none",
                "max_output": measured["max_output"],
                "ledger_key": key,
            }
            result["status"] = "RECOVERING"
            result["active_attempt"] = "recovery"
            result["finish_reason"] = None
            cursor = app.event(
                root,
                {
                    "type": "recovery",
                    "state": "STARTED",
                    "trigger": result["recovery"]["trigger"],
                    "max_output": measured["max_output"],
                },
            )
            result["event_cursor"] = cursor
            c.save(root / "job.json", result)
            recovery = consume_attempt(
                app,
                root,
                result,
                recovery_body,
                cancel,
                abort,
                control,
                "recovery",
                "native-recovery.sse",
                "RECOVERING",
            )
            attempts.append(recovery)
            result["attempts"] = attempts
            c.settle_request(
                key,
                recovery.get("usage") or {},
                "CANCELLED_MEMORY"
                if reason["memory"]
                else ("ERROR" if reason["timeout"] else recovery["status"]),
            )
            result["recovery"].update(
                state=recovery["status"],
                finish_reason=recovery.get("finish_reason"),
                wall_ms=recovery.get("wall_ms"),
                error=recovery.get("error"),
            )
            if recovery["status"] != "COMPLETE" and not result.get("content", "").strip():
                snapshot = c.load(root / "pre-recovery-job.json")
                if snapshot and snapshot.get("content", "").strip():
                    result["content"] = snapshot["content"]

        final_attempt = attempts[-1]
        result["attempts"] = attempts
        result["usage_total"] = aggregate_usage(attempts)
        if cancel.is_set():
            result["status"] = (
                "CANCELLED_MEMORY"
                if reason["memory"]
                else ("ERROR" if reason["timeout"] else "CANCELLED")
            )
        elif final_attempt["status"] == "COMPLETE" and result.get("content", "").strip():
            result["status"] = "COMPLETE"
        elif final_attempt["status"] == "ERROR":
            result["status"] = "ERROR"
            result["error"] = final_attempt.get("error") or "Upstream attempt failed"
        else:
            result["status"] = "INCOMPLETE"
            if final_attempt.get("error"):
                result["error"] = final_attempt["error"]
    except Exception as exc:
        result["status"] = (
            "CANCELLED_MEMORY"
            if reason["memory"]
            else ("CANCELLED" if cancel.is_set() and not reason["timeout"] else "ERROR")
        )
        result["error"] = str(exc)
        reason["technical"] = True
    finally:
        done.set()
        close_response(control.get("response"))
        if not wait_idle(result["backend_id"]):
            result["status"] = "ERROR"
            result["error"] = "Drain not confirmed; model service must not be stopped"
            reason["drain_failed"] = True
        result["wall_ms"] = (time.monotonic() - started) * 1000
        result["workflow_wall_ms"] = result["wall_ms"]
        result["active_attempt"] = None
        (root / "original-final.txt").write_text(result.get("content", ""))
        (root / "reasoning.txt").write_text(result.get("reasoning", ""))
        try:
            result["assessment"] = app.assess(root, project, result)
        except Exception as exc:
            fallback = {
                "code": "CODE_NEEDS_REVIEW",
                "node": "NODE_NEEDS_REVIEW",
                "writing": "WRITING_NEEDS_REVIEW",
                "chat": "CHAT_NOT_COMPLETE",
            }[project["kind"]]
            result["assessment"] = {"status": fallback, "feedback": str(exc)}
        c.save(root / "job.json", result)
        with app.JOBS_LOCK:
            app.ACTIVE.pop(result["id"], None)
        if reason["memory"] and not reason.get("drain_failed"):
            if result["backend_id"] == backends.QWEN:
                spec = importlib.util.spec_from_file_location(
                    "gufo_lifecycle", c.ROOT / "runtime/gufo/workbench/operator.py"
                )
                lifecycle = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(lifecycle)
                try:
                    result["rollback"] = lifecycle.start("short", True)
                except Exception as exc:
                    result["rollback_error"] = str(exc)
            else:
                result["resident_action"] = (
                    "REMOTE_CANDIDATE_MEMORY_GUARD_OWNS_STOP; MIMO_ROLLBACK_REQUIRED"
                )
            c.save(root / "job.json", result)
        elif reason.get("technical"):
            result["resident_action"] = "PRESERVED_FOR_SYSTEMD_RECOVERY"
            c.save(root / "job.json", result)
        cursor = app.event(root, {"type": "terminal", "job": app.visible_job(result["id"])})
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)


def generate(app, root, project, result, body, cancel):
    worker(app, root, project, result, body, cancel, True)


def recover_job(app, value):
    value = app.identifier(value)
    root = app.folder("jobs", value)
    with c.transaction(), app.JOBS_LOCK:
        if app.ACTIVE or not c.idle():
            raise ValueError("One request at a time; Gufo or this client is occupied")
        result = app.job(value)
        if result.get("status") != "INCOMPLETE":
            raise ValueError("Only a terminal incomplete job can be recovered")
        if result.get("recovery", {}).get("attempted") or any(
            x.get("name") == "recovery" for x in result.get("attempts", [])
        ):
            raise ValueError("The single production recovery was already used")
        if not (result.get("finish_reason") == "length" or not result.get("content", "").strip()):
            raise ValueError("This incomplete job has no supported finalization trigger")
        primary_body = c.load(root / "request.json")
        if not primary_body:
            raise ValueError("Original request is missing")
        if not (root / "pre-recovery-job.json").exists():
            c.save(root / "pre-recovery-job.json", result)
        result.setdefault("attempts", [legacy_attempt(result)])
        result["event_cursor"] = event_count(root)
        app.EVENT_CURSORS[str(root)] = result["event_cursor"]
        result["status"] = "RECOVERING"
        result["active_attempt"] = "recovery"
        result["recovery"] = {
            "attempted": True,
            "state": "QUEUED",
            "trigger": result.get("finish_reason") or "NO_FINAL",
        }
        c.save(root / "job.json", result)
        cursor = app.event(
            root,
            {"type": "recovery", "state": "QUEUED", "trigger": result["recovery"]["trigger"]},
        )
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)
        cancel = threading.Event()
        app.ACTIVE[value] = {"cancel": cancel, "response": None}
        threading.Thread(
            target=worker,
            args=(app, root, app.project(result["project"]), result, primary_body, cancel, False),
            daemon=True,
        ).start()
        return app.visible_job(value)
