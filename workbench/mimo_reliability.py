"""Bounded, persistent MiMo delivery for HaloClu.

The primary request keeps native thinking. Output-limit and incomplete structured
deliveries are classified independently from transport success. The same MiMo
may perform one complete no-thinking regeneration and, only when necessary, up
to four bounded artifact requests. Primary evidence is never overwritten.
"""
from __future__ import annotations

import json
import threading
import time

import backend_registry as backends
import common as c
import complete_delivery
import production_reliability as reliability

STREAM_STALL_SECONDS = 600
SOCKET_READ_TIMEOUT = 660
ATTEMPT_TIMEOUT_SECONDS = 3600
WORKFLOW_TIMEOUT_SECONDS = 10800
PROGRESS_PERSIST_SECONDS = 1.0
MEMORY_SAMPLE_SECONDS = 5.0
RECOVERY_MIN_OUTPUT = 8192
RECOVERY_MAX_OUTPUT = 32768
PART_OUTPUT = 8192
MAX_PART_UNITS = 4
MAX_NATIVE_ATTEMPTS = 6
MAX_RESERVED_OUTPUT = 98304
MAX_ACCEPTED_CONTEXT_CHARS = 48000
MAX_CORRECTIONS_PER_FILE = 4


def _usage(packet: dict, current: dict) -> dict:
    usage = dict(current or {})
    if isinstance(packet.get("usage"), dict):
        usage.update(packet["usage"])
    timings = packet.get("timings")
    if isinstance(timings, dict):
        usage["timings"] = timings
        usage["cached_tokens"] = int(timings.get("cache_n") or 0)
        usage["prompt_tokens_per_second"] = timings.get("prompt_per_second")
        usage["completion_tokens_per_second"] = timings.get("predicted_per_second")
        accepted = timings.get("accepted_draft_tokens")
        rejected = timings.get("rejected_draft_tokens")
        usage["draft_tokens_accepted"] = accepted
        usage["draft_tokens_rejected"] = rejected
        if type(accepted) is int and type(rejected) is int:
            usage["draft_tokens"] = accepted + rejected
    return usage


def recovery_instruction(kind: str) -> str:
    common = (
        "MIMO COMPLETE DELIVERY PASS. The preceding attempt ended without a complete "
        "deliverable. Thinking is disabled for this request. Start immediately with the "
        "complete final answer. Do not emit planning notes, placeholders, TODOs, pseudo-tools, "
        "or instructions to continue later. Recreate every explicitly requested artifact in "
        "full, with unambiguous file headings and closed code fences. Do not append blindly to "
        "the previous fragment and do not claim tests were executed unless the response itself "
        "only states the requested NOT EXECUTED status. Preserve the original language, facts, "
        "contracts and safety constraints."
    )
    if kind in ("code", "node"):
        return common + (
            " Return only the complete exact-edits JSON object required by the original "
            "contract, with no Markdown fence or commentary."
        )
    if kind == "writing":
        return common + (
            " Return only the complete JSON object required by the active writing contract, "
            "with no Markdown fence or commentary."
        )
    return common


def recovery_request(primary: dict, project: dict) -> tuple[dict, dict]:
    source = json.loads(json.dumps(primary))
    messages = source.get("messages")
    if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
        raise ValueError("MiMo recovery requires the original user-terminated message sequence")
    messages[-1]["content"] = (
        str(messages[-1].get("content", "")).rstrip()
        + "\n\n"
        + recovery_instruction(project["kind"])
        + "\n"
    )
    primary_cap = int(source.get("max_completion_tokens") or source.get("max_tokens") or 4096)
    cap = min(RECOVERY_MAX_OUTPUT, max(RECOVERY_MIN_OUTPUT, primary_cap * 2))
    measured = backends.count(
        backends.MIMO,
        messages,
        cap,
        enable_thinking=False,
    )
    measured.update(
        reasoning_requested="none",
        reasoning_effective="disabled by native enable_thinking=false template",
        renderer_contract="mimo-kyojin-native-apply-template-final-only-v1",
    )
    body = backends.payload(
        backends.MIMO,
        messages,
        cap,
        project["kind"],
        enable_thinking=False,
    )
    body.update(temperature=0.2, top_p=0.95)
    return body, measured


def recovery_key(value: str) -> str:
    return value + "-recovery1"


def reserve_recovery(value: str, cap: int) -> str:
    key = recovery_key(value)
    existing = next((x for x in c.ledger().get("requests", []) if x.get("job") == key), None)
    if existing:
        if existing.get("state") == "IN_FLIGHT":
            raise ValueError("MiMo recovery reservation already exists and requires reconciliation")
        raise ValueError("The single MiMo final-only recovery was already consumed")
    c.reserve_request(key, cap, False, backends.MIMO)
    return key


def _consume_attempt(
    app,
    root,
    result: dict,
    body: dict,
    cancel: threading.Event,
    abort: threading.Event,
    control: dict,
    name: str,
    raw_name: str,
    phase: str,
    expected_input: int | None = None,
    publish_to_result: bool = True,
) -> dict:
    started = time.monotonic()
    response = None
    last_save = started
    local = {
        "name": name,
        "status": "IN_FLIGHT",
        "finish_reason": None,
        "usage": {},
        "ttft_ms": None,
        "first_reasoning_ms": None,
        "first_final_ms": None,
        "started_at": time.time(),
        "attempt_type": "primary" if name == "primary" else ("finalization" if name == "recovery" else ("revision" if name.startswith("correction-") else "artifact-part")),
        "thinking_requested": "none" if body.get("enable_thinking") is False else "native",
        "thinking_effective": "disabled by native enable_thinking=false template" if body.get("enable_thinking") is False else "native-on; no separate HIGH",
        "thinking_source": "request.enable_thinking=" + str(body.get("enable_thinking")).lower() if "enable_thinking" in body else "native-default",
        "sampling": {key: body.get(key) for key in ("temperature", "top_p") if key in body},
        "max_output": body.get("max_completion_tokens") or body.get("max_tokens"),
        "content": "",
        "reasoning": "",
        "raw": raw_name,
        "error": None,
        "transport": "UNKNOWN",
        "sse_done": False,
    }
    result["status"] = phase
    result["active_attempt"] = name
    result.pop("error", None)
    if name == "recovery" and publish_to_result:
        result["content"] = ""
        result["recovery_reasoning"] = ""
    c.save(root / "job.json", result)
    now = time.monotonic()
    control.update(
        active=True,
        last_progress=now,
        attempt_started=now,
        response=None,
        attempt=name,
    )
    unsupported_tool = False
    try:
        result["upstream_started"] = True
        result["upstream_started_at"] = time.time()
        result["upstream_attempt"] = name
        c.save(root / "job.json", result)
        response = backends.open_stream(
            backends.MIMO,
            "/v1/chat/completions",
            body,
            timeout=SOCKET_READ_TIMEOUT,
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
                if line.strip() == b"data: [DONE]":
                    local["sse_done"] = True
                    local["transport"] = "COMPLETE"
                    continue
                if not line.startswith(b"data: "):
                    continue
                packet = json.loads(line[6:])
                if packet.get("error"):
                    raise ValueError(str(packet["error"]))
                local["usage"] = _usage(packet, local["usage"])
                if local["usage"] and publish_to_result:
                    result["usage"] = local["usage"]
                for choice in packet.get("choices", []):
                    delta = choice.get("delta") or {}
                    if delta.get("tool_calls"):
                        unsupported_tool = True
                    thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    content = delta.get("content") or ""
                    if thought or content:
                        elapsed = (time.monotonic() - started) * 1000
                        if local["ttft_ms"] is None:
                            local["ttft_ms"] = elapsed
                        if thought and local["first_reasoning_ms"] is None:
                            local["first_reasoning_ms"] = elapsed
                        if content and local["first_final_ms"] is None:
                            local["first_final_ms"] = elapsed
                        local["reasoning"] += thought
                        local["content"] += content
                        if publish_to_result:
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
                                    "backend_id": backends.MIMO,
                                    "reasoning": thought,
                                    "content": content,
                                },
                            )
                            result["event_cursor"] = cursor
                    if choice.get("finish_reason"):
                        local["finish_reason"] = choice["finish_reason"]
                        if publish_to_result:
                            result["finish_reason"] = choice["finish_reason"]
                current = time.monotonic()
                if publish_to_result and current - last_save >= PROGRESS_PERSIST_SECONDS:
                    c.save(root / "job.json", result)
                    last_save = current
        if not local["sse_done"] and not cancel.is_set() and not abort.is_set():
            local["transport"] = "INTERRUPTED"
        if abort.is_set():
            local.update(
                status="INCOMPLETE",
                error=f"No upstream progress for {STREAM_STALL_SECONDS} seconds",
            )
        elif cancel.is_set():
            local["status"] = "CANCELLED"
        elif unsupported_tool:
            local.update(status="INCOMPLETE", error="UNSUPPORTED_TOOL_CALL_OUTPUT")
        elif local["finish_reason"] == "stop" and local["content"].strip():
            problem = reliability.final_problem(local["content"])
            if problem:
                local.update(status="INCOMPLETE", error=problem)
            else:
                local["status"] = "COMPLETE"
        else:
            local["status"] = "INCOMPLETE"
        native = local["usage"].get("prompt_tokens")
        if native is not None and expected_input is not None and native != expected_input:
            local.update(
                status="ERROR",
                error="Native MiMo prompt count differs from preflight renderer",
            )
    except Exception as exc:
        if abort.is_set():
            local.update(
                status="INCOMPLETE",
                error=f"No upstream progress for {STREAM_STALL_SECONDS} seconds",
            )
        elif cancel.is_set():
            local["status"] = "CANCELLED"
        else:
            local.update(status="ERROR", error=str(exc))
    finally:
        reliability.close_response(response)
        try:
            if response:
                response.close()
        except Exception:
            pass
        control.update(active=False, response=None, last_progress=time.monotonic())
        if result["id"] in app.ACTIVE:
            app.ACTIVE[result["id"]]["response"] = None
    local["wall_ms"] = (time.monotonic() - started) * 1000
    local["_content"] = local.pop("content")
    local["_reasoning"] = local.pop("reasoning")
    local["content_chars"] = len(local["_content"])
    local["reasoning_chars"] = len(local["_reasoning"])
    return local

def _public_attempt(attempt: dict) -> dict:
    return {key: value for key, value in attempt.items() if not key.startswith("_")}


def _public_inspection(inspection: dict) -> dict:
    return {key: value for key, value in inspection.items() if not key.startswith("_")}


def recovery_decision(attempt: dict, max_output: int | None = None) -> dict:
    return complete_delivery.classify_attempt(attempt, max_output)


def automatic_recovery(attempt: dict, max_output: int | None = None) -> bool:
    return recovery_decision(attempt, max_output)["recoverable"]


def _inspect_attempt(attempt: dict, content: str, manifest: dict) -> dict:
    inspection = complete_delivery.inspect_delivery(content, manifest)
    attempt["delivery"] = _public_inspection(inspection)
    if manifest.get("required") and attempt.get("status") == "COMPLETE" and not inspection["complete"]:
        attempt["status"] = "INCOMPLETE"
        attempt["error"] = "INCOMPLETE_DELIVERY"
    attempt["classification"] = recovery_decision(attempt)
    return inspection


def _candidate_rank(candidate: dict) -> tuple:
    inspection = candidate["_inspection"]
    issue_count = sum(
        len(inspection.get(key, []))
        for key in (
            "missing_files", "invalid_required_files", "invalid_files",
            "ambiguous_files", "missing_sections",
        )
    ) + int(bool(inspection.get("unclosed_fence")))
    valid_files = sum(
        1 for name, item in inspection.get("files", {}).items()
        if item.get("syntax_ok") and name not in inspection.get("ambiguous_files", [])
    )
    valid_sections = len(inspection.get("sections_present", []))
    return (
        int(bool(inspection.get("complete"))),
        valid_files + valid_sections,
        -issue_count,
        int(candidate.get("attempt_status") == "COMPLETE"),
        int(bool(candidate.get("_content", "").strip())),
        len(candidate.get("_content", "")),
    )


def _candidate(
    attempt_id: str,
    content: str,
    inspection: dict,
    attempt: dict | None = None,
    artifact_sources: dict | None = None,
    section_sources: dict | None = None,
) -> dict:
    attempt = dict(attempt or {})
    ambiguous = set(inspection.get("ambiguous_files", []))
    sources = dict(artifact_sources or {})
    for name, item in inspection.get("files", {}).items():
        if item.get("syntax_ok") and name not in ambiguous and name not in sources:
            sources[name] = {"attempt_id": attempt_id, "sha256": item.get("sha256")}
    sections = dict(section_sources or {})
    for code in inspection.get("sections_present", []):
        sections.setdefault(code, {"attempt_id": attempt_id})
    return {
        "schema": "haloclu-delivery-candidate-v1",
        "attempt_id": attempt_id,
        "attempt_status": attempt.get("status"),
        "finish_reason": attempt.get("finish_reason"),
        "usage": dict(attempt.get("usage") or {}),
        "content_sha256": complete_delivery.sha256_text(content),
        "content_chars": len(content),
        "complete": bool(inspection.get("complete")),
        "delivery_state": inspection.get("state"),
        "artifact_sources": sources,
        "section_sources": sections,
        "_content": content,
        "_inspection": inspection,
    }


def _public_candidate(candidate: dict) -> dict:
    return {key: value for key, value in candidate.items() if not key.startswith("_")}


def _accepted_components(candidates: list[dict], manifest: dict) -> tuple[dict, dict, dict, dict]:
    files = {}
    file_sources = {}
    sections = {}
    section_sources = {}
    for candidate in candidates:
        inspection = candidate["_inspection"]
        ambiguous = set(inspection.get("ambiguous_files", []))
        for name, value in inspection.get("_file_contents", {}).items():
            item = inspection.get("files", {}).get(name, {})
            if name in files or name in ambiguous or not item.get("syntax_ok"):
                continue
            files[name] = value
            file_sources[name] = dict(candidate.get("artifact_sources", {}).get(name) or {
                "attempt_id": candidate["attempt_id"],
                "sha256": complete_delivery.sha256_text(value),
            })
        for item in manifest.get("required_sections", []):
            code = item["code"]
            if code == "C" or code in sections:
                continue
            section = complete_delivery.extract_section(candidate["_content"], code, manifest)
            if section:
                sections[code] = section
                section_sources[code] = dict(candidate.get("section_sources", {}).get(code) or {
                    "attempt_id": candidate["attempt_id"],
                })
    return files, file_sources, sections, section_sources


def _missing_component_units(manifest: dict, files: dict, sections: dict) -> list[str]:
    values = [name for name in manifest.get("required_files", []) if name not in files]
    values.extend(
        f"section:{item['code']}"
        for item in manifest.get("required_sections", [])
        if item["code"] != "C" and item["code"] not in sections
    )
    return values[: int(manifest.get("max_part_units") or MAX_PART_UNITS)]


def part_key(value: str, index: int) -> str:
    return f"{value}-part{index}"


def reserve_part(value: str, index: int, cap: int) -> str:
    key = part_key(value, index)
    existing = next((x for x in c.ledger().get("requests", []) if x.get("job") == key), None)
    if existing:
        if existing.get("state") == "IN_FLIGHT":
            raise ValueError("MiMo artifact reservation already exists and requires reconciliation")
        raise ValueError("MiMo artifact request was already consumed")
    c.reserve_request(key, cap, False, backends.MIMO)
    return key


def reserve_correction(value: str, index: int, cap: int) -> str:
    key = f"{value}-correction{index}"
    existing = next((x for x in c.ledger().get("requests", []) if x.get("job") == key), None)
    if existing:
        if existing.get("state") == "IN_FLIGHT":
            raise ValueError("MiMo correction reservation already exists and requires reconciliation")
        raise ValueError("MiMo correction request was already consumed")
    c.reserve_request(key, cap, False, backends.MIMO)
    return key


def part_request(
    primary: dict,
    project: dict,
    unit: str,
    accepted: dict[str, str],
    feedback: str = "",
) -> tuple[dict, dict]:
    source = json.loads(json.dumps(primary))
    messages = source.get("messages")
    if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
        raise ValueError("MiMo artifact completion requires the original user message sequence")
    accepted_rows = []
    accepted_bytes = 0
    for name, content in sorted(accepted.items()):
        if name == unit:
            continue
        header = f"\n### ACCEPTED {name} · sha256 {complete_delivery.sha256_text(content)}\n"
        remaining = MAX_ACCEPTED_CONTEXT_CHARS - accepted_bytes - len(header)
        if remaining <= 0:
            accepted_rows.append(f"- {name}: sha256 {complete_delivery.sha256_text(content)}")
            continue
        excerpt = content[:remaining]
        accepted_rows.append(header + excerpt)
        accepted_bytes += len(header) + len(excerpt)
    coherence = ("\nAlready accepted artifacts. Keep the new unit interface-compatible with these exact bytes:\n" + "\n".join(accepted_rows)) if accepted_rows else ""
    if unit.startswith("section:"):
        code = unit.split(":", 1)[1]
        instruction = (
            f"SECTION COMPLETION PASS. Return exactly section {code} from the original required answer, "
            f"starting with the heading '{code}.'. Return that section only, with no code files, planning, "
            "TODO, ellipsis or commentary outside the section. Preserve the original evidence limits and "
            "do not claim tests were executed."
        )
    else:
        instruction = (
            "ARTIFACT COMPLETION PASS. Return exactly one complete file named " + unit
            + " in one closed code fence headed by that exact filename. Return no other file, "
              "no planning, no TODO, no ellipsis and no prose outside the heading and fence. "
              "Implement the original contract in full. Do not claim to have executed tests."
        )
    feedback_text = ("\nExternal validation feedback. Correct the implementation, not the tests:\n" + feedback.strip()) if feedback.strip() else ""
    messages[-1]["content"] = (
        str(messages[-1].get("content", "")).rstrip()
        + "\n\n" + instruction + feedback_text + coherence + "\n"
    )
    measured = backends.count(
        backends.MIMO,
        messages,
        PART_OUTPUT,
        enable_thinking=False,
    )
    measured.update(
        reasoning_requested="none",
        reasoning_effective="disabled by native enable_thinking=false template",
        renderer_contract="mimo-kyojin-native-artifact-finalization-v1",
        artifact=unit,
    )
    body = backends.payload(
        backends.MIMO,
        messages,
        PART_OUTPUT,
        project["kind"],
        enable_thinking=False,
    )
    body.update(temperature=0.2, top_p=0.95)
    return body, measured


def worker(app, root, project, result, primary_body, cancel, run_primary: bool = True):
    workflow_started = time.monotonic()
    prior_workflow_wall_ms = result.get("workflow_wall_ms")
    done = threading.Event()
    abort = threading.Event()
    control = {
        "active": False,
        "last_progress": workflow_started,
        "attempt_started": None,
        "response": None,
        "attempt": None,
    }
    reason = {
        "memory": False,
        "workflow_timeout": False,
        "attempt_timeout": False,
        "stall": False,
        "drain_failed": False,
    }
    attempts = list(result.get("attempts") or [])
    if not attempts and not run_primary:
        attempts = [reliability.legacy_attempt(result)]
    workflow_kind = str(project.get("kind") or "chat")
    manifest = complete_delivery.workflow_manifest(
        workflow_kind, primary_body.get("messages", [])
    )
    if workflow_kind == "chat" and result.get("delivery_manifest"):
        manifest = result["delivery_manifest"]
    elif result.get("delivery_manifest", {}).get("required"):
        result["superseded_delivery_manifest"] = result["delivery_manifest"]
    result["delivery_manifest"] = manifest
    primary_cap = int(
        (result.get("measured") or {}).get("max_output")
        or primary_body.get("max_completion_tokens")
        or primary_body.get("max_tokens")
        or 4096
    )
    reserved_output = int(result.get("reserved_output_tokens") or primary_cap)
    candidates = []
    selected = None
    selected_reason = None
    primary_content = result.get("content", "")
    primary_reasoning = result.get("reasoning", "")
    recovery_content = ""
    primary_inspection = None
    recovery_inspection = None
    final_inspection = None

    def public_inspection(inspection):
        return _public_inspection(inspection)

    def save_inspection(label, inspection):
        c.save(root / f"{label}-delivery-inspection.json", public_inspection(inspection))

    def selection_event(candidate, decision, why, extra=None):
        entry = {
            "schema": "haloclu-delivery-selection-event-v1",
            "decision": decision,
            "reason": why,
            "candidate": _public_candidate(candidate),
        }
        if extra:
            entry.update(extra)
        result.setdefault("selection_history", []).append(entry)

    def choose(candidate, why):
        nonlocal selected, selected_reason
        selected = candidate
        selected_reason = why
        result["content"] = candidate["_content"]
        result["selected_attempt"] = candidate["attempt_id"]
        result["selected_content_sha256"] = candidate["content_sha256"]
        result["selected_artifacts"] = dict(candidate.get("artifact_sources") or {})
        result["selected_sections"] = dict(candidate.get("section_sources") or {})
        result["selected_candidate"] = _public_candidate(candidate)
        result["selection_reason"] = why
        selection_event(candidate, "SELECTED", why)
        return candidate

    def consider(candidate, why):
        candidates.append(candidate)
        if selected is None or _candidate_rank(candidate) > _candidate_rank(selected):
            return choose(candidate, why)
        selection_event(
            candidate,
            "RETAINED_PREVIOUS",
            why,
            {
                "retained_attempt": selected["attempt_id"],
                "retained_content_sha256": selected["content_sha256"],
            },
        )
        result["content"] = selected["_content"]
        return selected

    def ordered_candidates():
        if selected is None:
            return list(candidates)
        return [selected] + [item for item in candidates if item is not selected]

    def watch():
        last_memory = 0.0
        while not done.wait(2):
            now = time.monotonic()
            if now - last_memory >= MEMORY_SAMPLE_SECONDS:
                sample = {"elapsed_s": now - workflow_started, "backend_id": backends.MIMO}
                try:
                    sample["available_bytes"] = backends.available_bytes(backends.MIMO)
                except Exception as exc:
                    sample["available_bytes"] = None
                    sample["telemetry_error"] = str(exc)
                result.setdefault("memory_samples", []).append(sample)
                if (
                    type(sample.get("available_bytes")) is int
                    and sample["available_bytes"] < backends.MINIMUM_AVAILABLE_BYTES
                ):
                    reason["memory"] = True
                    cancel.set()
                last_memory = now
            if now - workflow_started > WORKFLOW_TIMEOUT_SECONDS:
                reason["workflow_timeout"] = True
                cancel.set()
            if control["active"] and control.get("attempt_started") is not None:
                if now - control["attempt_started"] > ATTEMPT_TIMEOUT_SECONDS:
                    reason["attempt_timeout"] = True
                    abort.set()
                    reliability.close_response(control.get("response"))
            if (
                control["active"]
                and not abort.is_set()
                and now - control["last_progress"] > STREAM_STALL_SECONDS
            ):
                reason["stall"] = True
                abort.set()
                reliability.close_response(control.get("response"))
            if cancel.is_set():
                reliability.close_response(control.get("response"))
                return

    threading.Thread(target=watch, daemon=True).start()
    try:
        if run_primary:
            result.update(status="IN_FLIGHT", active_attempt="primary")
            primary_raw = _consume_attempt(
                app, root, result, primary_body, cancel, abort, control,
                "primary", "native.sse", "IN_FLIGHT",
                result.get("measured", {}).get("input_tokens"),
            )
            primary_content = primary_raw.pop("_content")
            primary_reasoning = primary_raw.pop("_reasoning")
            result["reasoning"] = primary_reasoning
            (root / "primary-final.txt").write_text(primary_content)
            (root / "primary-reasoning.txt").write_text(primary_reasoning)
            primary_inspection = _inspect_attempt(primary_raw, primary_content, manifest)
            primary_raw["classification"] = recovery_decision(primary_raw, primary_cap)
            save_inspection("primary", primary_inspection)
            primary = _public_attempt(primary_raw)
            attempts.append(primary)
            result["attempts"] = attempts
            result["primary_classification"] = primary["classification"]
            c.settle_request(
                result["id"], primary.get("usage") or {},
                "CANCELLED_MEMORY" if reason["memory"] else (
                    "ERROR" if reason["workflow_timeout"] or reason["attempt_timeout"]
                    else primary["status"]
                ),
            )
            consider(
                _candidate("primary", primary_content, primary_inspection, primary),
                "PRIMARY_ATTEMPT_RECEIVED",
            )
        else:
            primary = next(
                (item for item in attempts if item.get("name") == "primary"),
                attempts[0],
            )
            primary_content = (
                (root / "primary-final.txt").read_text()
                if (root / "primary-final.txt").is_file()
                else result.get("content", "")
            )
            primary_reasoning = (
                (root / "primary-reasoning.txt").read_text()
                if (root / "primary-reasoning.txt").is_file()
                else result.get("reasoning", "")
            )
            if not (root / "primary-final.txt").exists():
                (root / "primary-final.txt").write_text(primary_content)
            if not (root / "primary-reasoning.txt").exists():
                (root / "primary-reasoning.txt").write_text(primary_reasoning)
            primary_inspection = complete_delivery.inspect_delivery(primary_content, manifest)
            primary = dict(primary, delivery=public_inspection(primary_inspection))
            primary["classification"] = recovery_decision(primary, primary_cap)
            for index, item in enumerate(attempts):
                if item.get("name") == primary.get("name"):
                    attempts[index] = primary
                    break
            save_inspection("primary", primary_inspection)
            result["attempts"] = attempts
            result["primary_classification"] = primary["classification"]
            primary_candidate = _candidate(
                "primary", primary_content, primary_inspection, primary
            )
            candidates.append(primary_candidate)
            selected_path = root / "selected-final.txt"
            stored_selection = c.load(root / "selected-delivery.json", {})
            current_content = (
                selected_path.read_text()
                if selected_path.is_file()
                else result.get("content", primary_content)
            )
            current_id = str(
                result.get("selected_attempt")
                or stored_selection.get("selected_attempt")
                or stored_selection.get("attempt_id")
                or ""
            )
            if current_content == primary_content and current_id in {"", "primary"}:
                choose(primary_candidate, "RESUMED_PRIMARY")
            else:
                current_inspection = complete_delivery.inspect_delivery(current_content, manifest)
                current_attempt = next(
                    (item for item in reversed(attempts) if item.get("name") == current_id),
                    {
                        "status": result.get("status"),
                        "finish_reason": result.get("finish_reason"),
                        "usage": result.get("usage") or {},
                    },
                )
                current_candidate = _candidate(
                    current_id or "existing-selected", current_content,
                    current_inspection, current_attempt,
                    result.get("selected_artifacts")
                    or stored_selection.get("selected_artifacts")
                    or stored_selection.get("artifact_sources")
                    or {},
                    result.get("selected_sections")
                    or stored_selection.get("selected_sections")
                    or stored_selection.get("section_sources")
                    or {},
                )
                candidates.append(current_candidate)
                choose(current_candidate, "RESUMED_SELECTED_CANDIDATE")
            recovery_attempt = next(
                (item for item in attempts if item.get("name") == "recovery"), None
            )
            if recovery_attempt and (root / "recovery-final.txt").is_file():
                recovery_content = (root / "recovery-final.txt").read_text()
                recovery_inspection = complete_delivery.inspect_delivery(
                    recovery_content, manifest
                )
                recovery_candidate = _candidate(
                    "recovery", recovery_content, recovery_inspection,
                    recovery_attempt,
                )
                if all(item["attempt_id"] != "recovery" for item in candidates):
                    consider(recovery_candidate, "RESUMED_RECOVERY_CANDIDATE")

        decision = recovery_decision(primary, primary_cap)
        result["recovery_eligible"] = decision["recoverable"]
        result["recovery_reason"] = decision
        do_recovery = (
            not cancel.is_set()
            and decision["recoverable"]
            and not any(item.get("name") == "recovery" for item in attempts)
        )
        if do_recovery:
            result["recovery"] = {
                "attempted": True,
                "state": "RECOVERY_PENDING",
                "trigger": decision["native_conclusion"],
                "partial_final_preserved": bool(primary_content),
                "enable_thinking": False,
            }
            result["status"] = "RECOVERING"
            c.save(root / "job.json", result)
            cursor = app.event(
                root,
                {
                    "type": "recovery",
                    "backend_id": backends.MIMO,
                    "state": "RECOVERY_PENDING",
                    "trigger": decision["native_conclusion"],
                    "partial_final_preserved": bool(primary_content),
                    "message": "Primaria terminata per limite output. Finale parziale conservato; sto completando la consegna con MiMo.",
                },
            )
            result["event_cursor"] = cursor
            if not backends.wait_idle(backends.MIMO, 120):
                reason["drain_failed"] = True
                raise RuntimeError("Primary MiMo attempt ended but native drain was not confirmed")
            if not (root / "pre-recovery-job.json").exists():
                c.save(root / "pre-recovery-job.json", result)
            abort.clear()
            recovery_body, measured = recovery_request(primary_body, project)
            if len(attempts) + 1 > MAX_NATIVE_ATTEMPTS or reserved_output + measured["max_output"] > MAX_RESERVED_OUTPUT:
                raise RuntimeError("MiMo workflow recovery budget exhausted before regeneration")
            key = reserve_recovery(result["id"], measured["max_output"])
            reserved_output += measured["max_output"]
            c.save(root / "recovery-request.json", recovery_body)
            result["recovery_measured"] = measured
            result["recovery"].update(
                state="RUNNING",
                max_output=measured["max_output"],
                ledger_key=key,
            )
            result["active_attempt"] = "recovery"
            result["finish_reason"] = None
            c.save(root / "job.json", result)
            recovery_raw = _consume_attempt(
                app, root, result, recovery_body, cancel, abort, control,
                "recovery", "native-recovery.sse", "RECOVERING",
                measured.get("input_tokens"),
            )
            recovery_content = recovery_raw.pop("_content")
            recovery_reasoning = recovery_raw.pop("_reasoning")
            result["recovery_reasoning"] = recovery_reasoning
            (root / "recovery-final.txt").write_text(recovery_content)
            recovery_inspection = _inspect_attempt(
                recovery_raw, recovery_content, manifest
            )
            recovery_raw["classification"] = recovery_decision(
                recovery_raw, measured["max_output"]
            )
            save_inspection("recovery", recovery_inspection)
            recovery = _public_attempt(recovery_raw)
            attempts.append(recovery)
            result["attempts"] = attempts
            c.settle_request(
                key,
                recovery.get("usage") or {},
                "CANCELLED_MEMORY" if reason["memory"] else (
                    "ERROR" if reason["workflow_timeout"] or reason["attempt_timeout"]
                    else recovery["status"]
                ),
            )
            result["recovery"].update(
                state=recovery["status"],
                finish_reason=recovery.get("finish_reason"),
                wall_ms=recovery.get("wall_ms"),
                error=recovery.get("error"),
                reasoning_chars=recovery.get("reasoning_chars", 0),
            )
            consider(
                _candidate(
                    "recovery", recovery_content, recovery_inspection, recovery
                ),
                "RECOVERY_ATTEMPT_RECEIVED",
            )

        accepted_files, accepted_file_sources, accepted_sections, accepted_section_sources = _accepted_components(
            ordered_candidates(), manifest
        )
        if manifest.get("required") and selected and not selected["complete"]:
            assembled = complete_delivery.assemble_structured(
                selected["_content"], manifest, accepted_files, accepted_sections
            )
            if assembled:
                assembled_inspection = complete_delivery.inspect_delivery(
                    assembled, manifest
                )
                assembled_candidate = _candidate(
                    "assembled-existing",
                    assembled,
                    assembled_inspection,
                    {
                        "status": "COMPLETE" if assembled_inspection["complete"] else "INCOMPLETE",
                        "finish_reason": selected.get("finish_reason"),
                        "usage": selected.get("usage") or {},
                    },
                    accepted_file_sources,
                    accepted_section_sources,
                )
                candidates.append(assembled_candidate)
                if _candidate_rank(assembled_candidate) > _candidate_rank(selected):
                    choose(assembled_candidate, "ASSEMBLED_FROM_EXISTING_ATTEMPTS")
                    (root / "assembled-final.md").write_text(assembled)

        units = (
            _missing_component_units(manifest, accepted_files, accepted_sections)
            if manifest.get("required") and selected and not selected["complete"]
            else []
        )
        if units and not cancel.is_set():
            result.setdefault("recovery", {"attempted": True})
            result["recovery"].update(state="PARTS_PENDING", missing_units=units)
            c.save(root / "job.json", result)
            for index, unit in enumerate(units[:MAX_PART_UNITS], start=1):
                if cancel.is_set():
                    break
                if len(attempts) + 1 > MAX_NATIVE_ATTEMPTS or reserved_output + PART_OUTPUT > MAX_RESERVED_OUTPUT:
                    result["recovery"]["parts_limit"] = "WORKFLOW_BUDGET_EXHAUSTED"
                    break
                if not backends.wait_idle(backends.MIMO, 120):
                    reason["drain_failed"] = True
                    raise RuntimeError("MiMo drain was not confirmed before artifact completion")
                abort.clear()
                body, measured = part_request(
                    primary_body, project, unit, accepted_files
                )
                key = reserve_part(result["id"], index, measured["max_output"])
                reserved_output += measured["max_output"]
                c.save(root / f"part-{index}-request.json", body)
                result["recovery"].update(state="PARTS_RUNNING", active_unit=unit)
                cursor = app.event(
                    root,
                    {
                        "type": "recovery",
                        "backend_id": backends.MIMO,
                        "state": "PARTS_RUNNING",
                        "trigger": "INCOMPLETE_ARTIFACT",
                        "artifact": unit,
                        "message": f"Completamento dell'unità {unit} con MiMo.",
                    },
                )
                result["event_cursor"] = cursor
                c.save(root / "job.json", result)
                part_raw = _consume_attempt(
                    app, root, result, body, cancel, abort, control,
                    f"part-{index}", f"native-part-{index}.sse", "RECOVERING",
                    measured.get("input_tokens"), publish_to_result=False,
                )
                part_content = part_raw.pop("_content")
                part_raw.pop("_reasoning")
                (root / f"part-{index}-final.txt").write_text(part_content)
                if unit.startswith("section:"):
                    code = unit.split(":", 1)[1]
                    unit_manifest = {
                        "schema": "haloclu-delivery-manifest-v1",
                        "required": True,
                        "required_files": [],
                        "required_sections": [{"code": code, "label": code}],
                    }
                else:
                    unit_manifest = {
                        "schema": "haloclu-delivery-manifest-v1",
                        "required": True,
                        "required_files": [unit],
                        "required_sections": [],
                    }
                unit_inspection = complete_delivery.inspect_delivery(
                    part_content, unit_manifest
                )
                part_raw["delivery"] = public_inspection(unit_inspection)
                part_raw["classification"] = recovery_decision(
                    part_raw, measured["max_output"]
                )
                part_raw["artifact"] = unit
                if unit_inspection["complete"]:
                    if unit.startswith("section:"):
                        code = unit.split(":", 1)[1]
                        accepted_sections[code] = complete_delivery.extract_section(
                            part_content, code, unit_manifest
                        )
                        accepted_section_sources[code] = {
                            "attempt_id": f"part-{index}"
                        }
                    else:
                        value = unit_inspection["_file_contents"][unit]
                        accepted_files[unit] = value
                        accepted_file_sources[unit] = {
                            "attempt_id": f"part-{index}",
                            "sha256": complete_delivery.sha256_text(value),
                        }
                    part_raw["promotion"] = {"status": "ACCEPTED_COMPONENT"}
                else:
                    part_raw["promotion"] = {
                        "status": "REJECTED_INCOMPLETE_COMPONENT"
                    }
                attempts.append(_public_attempt(part_raw))
                result["attempts"] = attempts
                c.settle_request(key, part_raw.get("usage") or {}, part_raw["status"])
                c.save(
                    root / f"part-{index}-delivery-inspection.json",
                    public_inspection(unit_inspection),
                )

            assembled = complete_delivery.assemble_structured(
                selected["_content"] if selected else "",
                manifest,
                accepted_files,
                accepted_sections,
            )
            if assembled:
                assembled_inspection = complete_delivery.inspect_delivery(
                    assembled, manifest
                )
                assembled_candidate = _candidate(
                    "assembled-parts",
                    assembled,
                    assembled_inspection,
                    {
                        "status": "COMPLETE" if assembled_inspection["complete"] else "INCOMPLETE",
                        "finish_reason": selected.get("finish_reason") if selected else None,
                        "usage": selected.get("usage") if selected else {},
                    },
                    accepted_file_sources,
                    accepted_section_sources,
                )
                candidates.append(assembled_candidate)
                if selected is None or _candidate_rank(assembled_candidate) > _candidate_rank(selected):
                    choose(assembled_candidate, "ASSEMBLED_AFTER_MISSING_COMPONENTS")
                    (root / "assembled-final.md").write_text(assembled)
                else:
                    selection_event(
                        assembled_candidate,
                        "RETAINED_PREVIOUS",
                        "ASSEMBLED_CANDIDATE_NOT_BETTER",
                    )

        if selected is None:
            raise RuntimeError("No readable MiMo delivery candidate was preserved")
        final_content = selected["_content"]
        final_inspection = complete_delivery.persist_delivery(
            root, final_content, manifest, "final"
        )
        selected["_inspection"] = final_inspection
        result["delivery"] = public_inspection(final_inspection)
        result["content"] = final_content
        independent = c.ROOT / "docs/implementation/mimo-complete-delivery-001/independent/test_independent.py"
        if final_inspection["complete"] and {"secure_gateway.py", "test_secure_gateway.py"}.issubset(
            final_inspection.get("_file_contents", {})
        ):
            result["verification"] = complete_delivery.validate_python_delivery(
                root,
                final_inspection,
                independent if independent.is_file() else None,
            )
        else:
            result["verification"] = {
                "status": (
                    "NOT_RUN"
                    if final_inspection.get("complete") or not manifest.get("required")
                    else "NOT_RUN_INCOMPLETE_DELIVERY"
                ),
                "pass": False,
                "results": [],
            }
        c.save(root / "verification.json", result["verification"])

        correction_counts = {}
        correction_index = 0
        for item in attempts:
            name = str(item.get("name") or "")
            if name.startswith("correction-"):
                try:
                    correction_index = max(
                        correction_index, int(name.split("-", 1)[1])
                    )
                except ValueError:
                    pass
                artifact = item.get("artifact")
                if artifact:
                    correction_counts[artifact] = correction_counts.get(artifact, 0) + 1
        while (
            result["verification"].get("status") == "FAIL"
            and not cancel.is_set()
            and len(attempts) < MAX_NATIVE_ATTEMPTS
            and reserved_output + PART_OUTPUT <= MAX_RESERVED_OUTPUT
        ):
            failed_ids = {
                item.get("id")
                for item in result["verification"].get("results", [])
                if not item.get("pass")
            }
            if "independent" in failed_ids:
                unit = "secure_gateway.py"
            elif "candidate_unittest" in failed_ids:
                unit = "test_secure_gateway.py"
            else:
                break
            if unit not in accepted_files:
                break
            correction_counts[unit] = correction_counts.get(unit, 0) + 1
            if correction_counts[unit] > MAX_CORRECTIONS_PER_FILE:
                break
            correction_index += 1
            if not backends.wait_idle(backends.MIMO, 120):
                reason["drain_failed"] = True
                raise RuntimeError("MiMo drain was not confirmed before semantic correction")
            abort.clear()
            result_id = (
                "independent" if unit == "secure_gateway.py"
                else "candidate_unittest"
            )
            names = complete_delivery.failing_test_names(
                result["verification"], result_id
            )
            observed = complete_delivery.failing_test_feedback(
                result["verification"], result_id
            )
            if unit == "secure_gateway.py":
                contract = (
                    "Original contract reminders: MAC, expiry, missing account and non-current epoch are 401 before scope checks; "
                    "both read and export scopes are required; the live ACL is checked before serving cache; cache identity includes tenant, document and version. "
                    "Fix the implementation only. Do not change or weaken tests."
                )
            else:
                contract = (
                    "Fix the candidate suite only. Its fixtures and unpacking must address the exact intended identities, and its expected results must follow "
                    "the original authentication/scope/document/ACL precedence. Do not weaken coverage or alter the implementation to satisfy an incorrect test."
                )
            feedback = (
                "Failing external check names: "
                + (", ".join(names) if names else "not exposed")
                + ".\nBounded observed assertions:\n"
                + (observed or "No assertion summary was available.")
                + "\n"
                + contract
            )
            body, measured = part_request(
                primary_body, project, unit, accepted_files, feedback
            )
            key = reserve_correction(
                result["id"], correction_index, measured["max_output"]
            )
            reserved_output += measured["max_output"]
            c.save(root / f"correction-{correction_index}-request.json", body)
            result.setdefault("recovery", {"attempted": True})
            result["recovery"].update(
                state="CORRECTION_RUNNING",
                active_unit=unit,
                correction=correction_index,
                feedback_test_names=names,
            )
            cursor = app.event(
                root,
                {
                    "type": "recovery",
                    "backend_id": backends.MIMO,
                    "state": "CORRECTION_RUNNING",
                    "trigger": "FAIL_TESTS",
                    "artifact": unit,
                    "message": f"Correzione verificata dell'artefatto {unit} con MiMo.",
                },
            )
            result["event_cursor"] = cursor
            c.save(root / "job.json", result)
            raw = _consume_attempt(
                app, root, result, body, cancel, abort, control,
                f"correction-{correction_index}",
                f"native-correction-{correction_index}.sse",
                "RECOVERING", measured.get("input_tokens"),
                publish_to_result=False,
            )
            correction_content = raw.pop("_content")
            raw.pop("_reasoning")
            (root / f"correction-{correction_index}-final.txt").write_text(
                correction_content
            )
            unit_manifest = {
                "schema": "haloclu-delivery-manifest-v1",
                "required": True,
                "required_files": [unit],
                "required_sections": [],
            }
            inspection = complete_delivery.inspect_delivery(
                correction_content, unit_manifest
            )
            raw["artifact"] = unit
            raw["correction_index"] = correction_index
            raw["delivery"] = public_inspection(inspection)
            raw["classification"] = recovery_decision(
                raw, measured["max_output"]
            )
            promotion = {"status": "REJECTED_MALFORMED"}
            candidate_verification = None
            if inspection["complete"]:
                replacement = inspection["_file_contents"][unit]
                previous = accepted_files[unit]
                previous_hash = complete_delivery.sha256_text(previous)
                replacement_hash = complete_delivery.sha256_text(replacement)
                if replacement_hash == previous_hash:
                    promotion = {
                        "status": "NO_PROGRESS",
                        "reason": "IDENTICAL_ARTIFACT_BYTES",
                        "previous_sha256": previous_hash,
                        "candidate_sha256": replacement_hash,
                    }
                else:
                    candidate_files = dict(accepted_files)
                    candidate_sources = dict(accepted_file_sources)
                    candidate_files[unit] = replacement
                    candidate_sources[unit] = {
                        "attempt_id": f"correction-{correction_index}",
                        "sha256": replacement_hash,
                    }
                    assembled = complete_delivery.assemble_structured(
                        selected["_content"],
                        manifest,
                        candidate_files,
                        accepted_sections,
                    )
                    if assembled:
                        candidate_inspection = complete_delivery.inspect_delivery(
                            assembled, manifest
                        )
                        if candidate_inspection["complete"]:
                            candidate_verification = complete_delivery.validate_python_delivery(
                                root,
                                candidate_inspection,
                                independent if independent.is_file() else None,
                                work_name=f"python-validation-correction-{correction_index}",
                            )
                            comparison = complete_delivery.compare_verifications(
                                result["verification"], candidate_verification
                            )
                            promotion = {
                                "status": comparison["decision"],
                                "previous_sha256": previous_hash,
                                "candidate_sha256": replacement_hash,
                                "comparison": comparison,
                            }
                            if comparison["decision"] == "PROMOTE":
                                accepted_files = candidate_files
                                accepted_file_sources = candidate_sources
                                final_inspection = complete_delivery.persist_delivery(
                                    root, assembled, manifest, "final"
                                )
                                correction_candidate = _candidate(
                                    f"correction-{correction_index}",
                                    assembled,
                                    final_inspection,
                                    raw,
                                    accepted_file_sources,
                                    accepted_section_sources,
                                )
                                candidates.append(correction_candidate)
                                choose(
                                    correction_candidate,
                                    "SEMANTIC_CORRECTION_STRICTLY_IMPROVED",
                                )
                                result["delivery"] = public_inspection(
                                    final_inspection
                                )
                                result["verification"] = candidate_verification
                                c.save(root / "verification.json", result["verification"])
                        else:
                            promotion = {
                                "status": "REJECTED_MALFORMED",
                                "reason": "ASSEMBLED_DELIVERY_INCOMPLETE",
                            }
                    else:
                        promotion = {
                            "status": "REJECTED_MALFORMED",
                            "reason": "ASSEMBLY_FAILED",
                        }
            raw["promotion"] = promotion
            attempts.append(_public_attempt(raw))
            result["attempts"] = attempts
            c.settle_request(key, raw.get("usage") or {}, raw["status"])
            c.save(
                root / f"correction-{correction_index}-delivery-inspection.json",
                public_inspection(inspection),
            )
            if candidate_verification is not None:
                c.save(
                    root / f"correction-{correction_index}-verification.json",
                    candidate_verification,
                )
            if promotion["status"] == "PROMOTE":
                continue
            result["correction_terminal"] = promotion
            if result.get("recovery"):
                result["recovery"]["correction_terminal"] = promotion
            break

        final_content = selected["_content"]
        final_inspection = selected["_inspection"]
        result["content"] = final_content
        result["delivery"] = public_inspection(final_inspection)
        result["attempts"] = attempts
        result["usage_total"] = reliability.aggregate_usage(attempts)
        last_attempt = attempts[-1]
        result["last_attempt"] = {
            key: last_attempt.get(key)
            for key in (
                "name", "status", "finish_reason", "usage", "wall_ms",
                "artifact", "promotion",
            )
            if key in last_attempt
        }
        result["usage"] = dict(selected.get("usage") or {})
        result["finish_reason"] = selected.get("finish_reason")
        result["selected_finish_reason"] = selected.get("finish_reason")
        result["reserved_output_tokens"] = reserved_output
        result["native_attempt_count"] = len(attempts)
        if cancel.is_set():
            result["status"] = (
                "CANCELLED_MEMORY" if reason["memory"] else (
                    "ERROR" if reason["workflow_timeout"] or reason["attempt_timeout"]
                    else "CANCELLED"
                )
            )
        elif manifest.get("required"):
            result["status"] = (
                "COMPLETE" if final_inspection["complete"] else "INCOMPLETE"
            )
            if not final_inspection["complete"]:
                result["error"] = "INCOMPLETE_DELIVERY"
            else:
                result.pop("error", None)
        elif selected.get("attempt_status") == "COMPLETE" and final_content.strip():
            result["status"] = "COMPLETE"
            result.pop("error", None)
        elif selected.get("attempt_status") == "ERROR":
            result["status"] = "ERROR"
            result["error"] = "Selected MiMo attempt failed"
        else:
            result["status"] = "INCOMPLETE"
        if result.get("recovery", {}).get("attempted"):
            result["recovery"].update(
                state=result["status"],
                delivery_complete=bool(result.get("delivery", {}).get("complete")),
                verification=result.get("verification", {}).get("status", "NOT_RUN"),
                native_attempts=len(attempts),
            )
    except Exception as exc:
        result["status"] = (
            "CANCELLED_MEMORY" if reason["memory"] else (
                "CANCELLED"
                if cancel.is_set() and not reason["workflow_timeout"] and not reason["attempt_timeout"]
                else "ERROR"
            )
        )
        result["error"] = str(exc)
    finally:
        done.set()
        reliability.close_response(control.get("response"))
        if not backends.wait_idle(backends.MIMO, 120):
            result["status"] = "ERROR"
            result["error"] = "MiMo drain not confirmed; service was preserved"
            reason["drain_failed"] = True
        invocation_wall_ms = (time.monotonic() - workflow_started) * 1000
        result["wall_ms"] = invocation_wall_ms
        result["worker_invocation_wall_ms"] = invocation_wall_ms
        known_attempt_wall_ms = sum(
            float(item.get("wall_ms") or 0) for item in attempts
        )
        result["attempt_wall_ms_total"] = known_attempt_wall_ms
        result["terminal_at"] = time.time()
        if run_primary:
            result["workflow_wall_ms"] = invocation_wall_ms
            result["workflow_wall_basis"] = "CURRENT_WORKER_INVOCATION"
        else:
            if prior_workflow_wall_ms is not None:
                result["previous_workflow_wall_ms"] = prior_workflow_wall_ms
            result["workflow_wall_ms"] = known_attempt_wall_ms or invocation_wall_ms
            result["workflow_wall_basis"] = "SUM_KNOWN_NATIVE_ATTEMPTS"
        if result.get("created"):
            result["workflow_calendar_ms"] = max(
                0.0, (result["terminal_at"] - float(result["created"])) * 1000
            )
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
        if selected is not None and final_inspection is not None:
            selection = _public_candidate(selected)
            selection.update(
                {
                    "schema": "haloclu-selected-delivery-v1",
                    "selection_reason": selected_reason,
                    "selected_attempt": selected["attempt_id"],
                    "selected_content_sha256": selected["content_sha256"],
                    "selected_artifacts": dict(selected.get("artifact_sources") or {}),
                    "selected_sections": dict(selected.get("section_sources") or {}),
                    "assessment": result.get("assessment") or {},
                    "historical_job_preserved": True,
                }
            )
            result["selected_candidate"] = selection
            complete_delivery.persist_selected_delivery(
                root,
                selected["_content"],
                final_inspection,
                selection,
                result.get("verification") or {
                    "status": "NOT_RUN", "pass": False, "results": []
                },
            )
        if reason["memory"]:
            result["resident_action"] = "MIMO_PRESERVED_AFTER_MEMORY_GUARD"
        result["recovery_eligible"] = False
        c.save(root / "job.json", result)
        with app.JOBS_LOCK:
            app.ACTIVE.pop(result["id"], None)
        cursor = app.event(
            root, {"type": "terminal", "job": app.visible_job(result["id"])}
        )
        result["event_cursor"] = cursor
        c.save(root / "job.json", result)

def generate(app, root, project, result, body, cancel):
    worker(app, root, project, result, body, cancel, True)


def recover(app, root, project, result, body, cancel):
    worker(app, root, project, result, body, cancel, False)
