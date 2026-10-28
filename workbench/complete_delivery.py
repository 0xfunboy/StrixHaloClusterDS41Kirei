"""Structured delivery inspection and isolated validation for HaloClu.

This module never repairs generated code. It extracts only complete, explicitly
labelled artifacts, records provenance, and validates them outside the model.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

PYTHON_VALIDATOR = (
    "docker.io/library/python@sha256:"
    "922f47525757de33aff59f24cdfc85f412ac4a06aa8af7c7e9028d584b7bcdeb"
)
ALLOWED_EXTENSIONS = {
    ".py", ".go", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".html", ".css", ".json", ".md", ".yaml", ".yml", ".toml",
}
FILE_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}/){0,4}"
    r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}"
    r"\.(?:py|go|js|mjs|cjs|ts|tsx|jsx|html|css|json|md|yaml|yml|toml))"
)
FENCE_RE = re.compile(r"^\s*(```|~~~)([A-Za-z0-9_+.-]*)\s*$")
SECTION_LABELS = {
    "A": "Diagnosi",
    "B": "Ricostruzione",
    "C": "Artefatti",
    "D": "Evidenze e stato della verifica",
}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_name(value: str) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or len(value) > 320:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or len(path.parts) > 5:
        return None
    if any(part in {"", ".", ".."} or part.startswith(".") for part in path.parts):
        return None
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return None
    return path.as_posix()


def derive_manifest(messages: list[dict]) -> dict:
    users = [str(item.get("content", "")) for item in messages if item.get("role") == "user"]
    source = users[-1] if users else ""
    names: list[str] = []
    for match in FILE_RE.finditer(source):
        name = _safe_name(match.group(1))
        if name and name not in names:
            names.append(name)
    names = names[:4]
    explicit = bool(
        re.search(
            r"(?i)\b(?:file completi|struttura file|consegna obbligatoria|"
            r"complete files?|deliverables?|artefatti consegnati)\b",
            source,
        )
    )
    if len(names) >= 2:
        explicit = True
    sections = []
    for code, label in SECTION_LABELS.items():
        if re.search(rf"(?mi)^\s*(?:#+\s*)?{code}[.)]\s+", source):
            sections.append({"code": code, "label": label})
    required = bool(explicit and names) or len(sections) >= 3
    return {
        "schema": "haloclu-delivery-manifest-v1",
        "required": required,
        "required_files": names if required else [],
        "required_sections": sections if required else [],
        "source_sha256": sha256_text(source),
        "source_chars": len(source),
        "max_part_units": 4,
    }



def workflow_manifest(kind: str, messages: list[dict]) -> dict:
    """Return the delivery contract owned by the selected HaloClu workflow.

    Multi-file Chat answers need the generic artifact delivery layer. Coding,
    Node and Writing already have stricter native contracts (exact edits or the
    writing JSON schema) and must never be reinterpreted as full-file Chat
    deliveries merely because their prompts mention source filenames.
    """
    if kind == "chat":
        return derive_manifest(messages)
    users = [str(item.get("content", "")) for item in messages if item.get("role") == "user"]
    source = users[-1] if users else ""
    return {
        "schema": "haloclu-delivery-manifest-v1",
        "required": False,
        "required_files": [],
        "required_sections": [],
        "source_sha256": sha256_text(source),
        "source_chars": len(source),
        "max_part_units": 0,
        "workflow_kind": kind,
        "contract_owner": "workflow-native",
    }

def classify_attempt(attempt: dict, max_output: int | None = None) -> dict:
    status = str(attempt.get("status") or "UNKNOWN")
    finish = attempt.get("finish_reason")
    usage = attempt.get("usage") if isinstance(attempt.get("usage"), dict) else {}
    completion = usage.get("completion_tokens")
    content_chars = int(attempt.get("content_chars") or 0)
    transport = attempt.get("transport") or (
        "COMPLETE" if attempt.get("sse_done") is True else "UNKNOWN"
    )
    native = "UNKNOWN"
    if finish == "length":
        native = "OUTPUT_LIMIT"
    elif finish == "stop":
        native = "EOS_OR_EXPLICIT_STOP"
        if (
            type(max_output) is int
            and max_output > 0
            and type(completion) is int
            and completion >= max_output - 1
        ):
            native = "OUTPUT_LIMIT_STOP_COMPAT"
    elif finish:
        native = str(finish).upper()
    delivery = "ABSENT" if content_chars == 0 else "PARTIAL"
    if status == "COMPLETE" and content_chars:
        delivery = "COMPLETE_CANDIDATE"
    if isinstance(attempt.get("delivery"), dict):
        delivery = attempt["delivery"].get("state", delivery)
    recoverable = status == "INCOMPLETE" and (
        native in {"OUTPUT_LIMIT", "OUTPUT_LIMIT_STOP_COMPAT"}
        or attempt.get("error") in {"INCOMPLETE_DELIVERY", "NO_FINAL_CONTENT"}
        or delivery in {"ABSENT", "PARTIAL", "INCOMPLETE"}
        and bool(attempt.get("delivery", {}).get("required"))
    )
    return {
        "schema": "haloclu-attempt-classification-v1",
        "native_conclusion": native,
        "native_finish_reason": finish,
        "transport": transport,
        "service": "ACTIVE_OR_DRAINED" if status not in {"ERROR", "CANCELLED_MEMORY"} else "UNKNOWN",
        "delivery": delivery,
        "verification": attempt.get("verification", {}).get("status", "NOT_RUN"),
        "provenance": attempt.get("name") or "unknown",
        "completion_tokens": completion if type(completion) is int else None,
        "requested_output_cap": max_output if type(max_output) is int and max_output > 0 else None,
        "recoverable": recoverable,
    }


def _fenced_blocks(content: str) -> tuple[list[dict], bool]:
    lines = content.splitlines(keepends=True)
    blocks: list[dict] = []
    open_block = None
    char_offset = 0
    line_offsets = []
    for line in lines:
        line_offsets.append(char_offset)
        char_offset += len(line)
    for index, line in enumerate(lines):
        match = FENCE_RE.match(line.rstrip("\r\n"))
        if not match:
            continue
        marker, language = match.groups()
        if open_block is None:
            open_block = {
                "marker": marker,
                "language": language.lower(),
                "open_line": index,
                "content_start": line_offsets[index] + len(line),
            }
        elif marker == open_block["marker"]:
            start = open_block["content_start"]
            end = line_offsets[index]
            context_lines = lines[max(0, open_block["open_line"] - 4):open_block["open_line"]]
            blocks.append(
                {
                    **open_block,
                    "close_line": index,
                    "start": start,
                    "end": end,
                    "content": content[start:end],
                    "context": "".join(context_lines),
                }
            )
            open_block = None
    return blocks, open_block is not None


def _block_filename(block: dict, required: list[str]) -> str | None:
    probe = block.get("context", "") + "\n" + "\n".join(block.get("content", "").splitlines()[:3])
    matches = [name for name in required if re.search(rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])", probe)]
    return matches[0] if len(matches) == 1 else None


def _syntax(name: str, content: str) -> tuple[bool, str | None]:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(content, filename=name)
        except SyntaxError as exc:
            return False, f"{exc.msg} at line {exc.lineno}, column {exc.offset}"
    elif suffix == ".json":
        try:
            json.loads(content)
        except Exception as exc:
            return False, str(exc)
    if not content.strip():
        return False, "empty artifact"
    return True, None


def _section_positions(content: str, manifest: dict) -> dict[str, tuple[int, int]]:
    found: list[tuple[str, int]] = []
    for item in manifest.get("required_sections", []):
        code = item["code"]
        match = re.search(
            rf"(?mi)^\s*(?:#{{1,6}}\s*)?(?:\*\*)?{code}(?:[.)]|:)\s*[^\n]*",
            content,
        )
        if match:
            found.append((code, match.start()))
    found.sort(key=lambda item: item[1])
    positions = {}
    for index, (code, start) in enumerate(found):
        end = found[index + 1][1] if index + 1 < len(found) else len(content)
        positions[code] = (start, end)
    return positions


def inspect_delivery(content: str, manifest: dict) -> dict:
    declared_files = list(manifest.get("required_files", []))
    invalid_required_files = [name for name in declared_files if _safe_name(name) is None]
    required_files = [name for name in declared_files if _safe_name(name) is not None]
    blocks, unclosed = _fenced_blocks(content)
    files: dict[str, dict] = {}
    ambiguous: list[str] = []
    for block in blocks:
        name = _block_filename(block, required_files)
        if not name:
            continue
        if name in files:
            ambiguous.append(name)
            continue
        ok, error = _syntax(name, block["content"])
        files[name] = {
            "name": name,
            "language": block["language"],
            "start": block["start"],
            "end": block["end"],
            "chars": len(block["content"]),
            "sha256": sha256_text(block["content"]),
            "syntax_ok": ok,
            "syntax_error": error,
            "content": block["content"],
        }
    missing = [name for name in required_files if name not in files]
    invalid = [name for name, item in files.items() if not item["syntax_ok"]]
    section_positions = _section_positions(content, manifest)
    required_section_codes = [item["code"] for item in manifest.get("required_sections", [])]
    missing_sections = [code for code in required_section_codes if code not in section_positions]
    complete = bool(manifest.get("required")) and not (
        invalid_required_files or missing or invalid or ambiguous or missing_sections or unclosed
    )
    if not manifest.get("required"):
        complete = bool(content.strip()) and not unclosed
    state = "COMPLETE" if complete else ("ABSENT" if not content.strip() else "PARTIAL")
    public_files = {name: {k: v for k, v in item.items() if k != "content"} for name, item in files.items()}
    return {
        "schema": "haloclu-delivery-inspection-v1",
        "required": bool(manifest.get("required")),
        "state": state,
        "complete": complete,
        "content_chars": len(content),
        "content_sha256": sha256_text(content),
        "unclosed_fence": unclosed,
        "files": public_files,
        "missing_files": missing,
        "invalid_required_files": invalid_required_files,
        "invalid_files": invalid,
        "ambiguous_files": sorted(set(ambiguous)),
        "sections_present": sorted(section_positions),
        "missing_sections": missing_sections,
        "_file_contents": {name: item["content"] for name, item in files.items()},
        "_section_positions": section_positions,
    }


def persist_delivery(root: Path, content: str, manifest: dict, label: str = "final") -> dict:
    inspection = inspect_delivery(content, manifest)
    public = {k: v for k, v in inspection.items() if not k.startswith("_")}
    (root / "delivery-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    (root / f"{label}-delivery-inspection.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n")
    delivered = root / "delivered"
    if delivered.exists():
        shutil.rmtree(delivered)
    delivered.mkdir(mode=0o700)
    for name, value in inspection["_file_contents"].items():
        safe = _safe_name(name)
        if safe is None:
            raise ValueError("Unsafe delivered artifact path")
        target = delivered / safe
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(value)
        target.chmod(0o600)
    return inspection


def _sandbox_command(work: Path, args: list[str]) -> list[str]:
    return [
        "podman", "run", "--rm", "--pull=never", "--network", "none",
        "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "256", "--memory", "1g", "--memory-swap", "1g", "--cpus", "2",
        "--userns", "keep-id", "--user", f"{os.getuid()}:{os.getgid()}", "--unsetenv-all",
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=536870912,mode=1777",
        "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "PYTHONPYCACHEPREFIX=/tmp/pycache", "-e", "PYTHONPATH=/work",
        "-e", "PATH=/usr/local/bin:/usr/bin:/bin",
        "-v", f"{work}:/work:ro", "-w", "/work",
        "--entrypoint", "/usr/local/bin/python", PYTHON_VALIDATOR, *args,
    ]


def _run(command: list[str], timeout: int = 180) -> dict:
    try:
        result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        return {"pass": result.returncode == 0, "returncode": result.returncode, "log": result.stdout, "command": command}
    except subprocess.TimeoutExpired as exc:
        return {"pass": False, "returncode": 124, "log": (exc.stdout or "") + "\nSandbox timed out", "command": command}


def failing_test_names(verification: dict, result_id: str | None = None) -> list[str]:
    names = []
    for result in verification.get("results", []):
        if result.get("pass") or (result_id is not None and result.get("id") != result_id):
            continue
        for match in re.finditer(r"^(?:FAIL|ERROR):\s+([^\n]+)", result.get("log", ""), re.M):
            value = match.group(1).strip()
            if value not in names:
                names.append(value)
    return names[:24]


def failing_test_feedback(verification: dict, result_id: str, limit: int = 3200) -> str:
    """Return bounded test feedback without exposing suite source code."""
    rows = []
    for result in verification.get("results", []):
        if result.get("id") != result_id or result.get("pass"):
            continue
        log = str(result.get("log") or "")
        for block in re.split(r"\n={20,}\n", log):
            match = re.search(r"^(?:FAIL|ERROR):\s+([^\n]+)", block, re.M)
            if not match:
                continue
            name = match.group(1).strip()
            expected = re.search(r"Expected[^\n]*|AssertionError:[^\n]*", block)
            first = f"- {name}"
            if expected:
                first += ": " + expected.group(0).strip()
            if first not in rows:
                rows.append(first)
        if not rows:
            rows.extend(f"- {name}" for name in failing_test_names(verification, result_id))
    return "\n".join(rows)[:limit]


def validate_python_delivery(
    root: Path,
    inspection: dict,
    independent_suite: Path | None = None,
    work_name: str = "python-validation",
) -> dict:
    required = {"secure_gateway.py", "test_secure_gateway.py"}
    contents = inspection.get("_file_contents", {})
    if not required.issubset(contents):
        return {"status": "NOT_RUN_MISSING_FILES", "pass": False, "results": []}
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", work_name):
        raise ValueError("Unsafe validation workspace name")
    work = root / work_name
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(mode=0o700)
    for name in sorted(required):
        (work / name).write_text(contents[name])
    results = []
    results.append({"id": "py_compile", **_run(_sandbox_command(work, ["-m", "py_compile", *sorted(required)]))})
    if results[-1]["pass"]:
        results.append({"id": "candidate_unittest", **_run(_sandbox_command(work, ["-m", "unittest", "-v", "test_secure_gateway.py"]), 240)})
    if independent_suite and independent_suite.is_file():
        shutil.copyfile(independent_suite, work / "test_independent.py")
        results.append({"id": "independent", **_run(_sandbox_command(work, ["-m", "unittest", "-v", "test_independent.py"]), 300)})
    passed = bool(results) and all(item["pass"] for item in results)
    return {"status": "PASS" if passed else "FAIL", "pass": passed, "results": results}


def verification_outcomes(verification: dict) -> dict[str, dict]:
    """Normalize executable checks for strict non-regression comparisons."""
    outcomes = {}
    for result in verification.get("results", []):
        identifier = str(result.get("id") or "unknown")
        passed = bool(result.get("pass"))
        failures = [] if passed else failing_test_names({"results": [result]}, identifier)
        if not passed and not failures:
            log = str(result.get("log") or "")
            failures = [
                "opaque-failure:"
                + str(result.get("returncode"))
                + ":"
                + sha256_text(log)[:16]
            ]
        outcomes[identifier] = {
            "pass": passed,
            "failures": sorted(set(failures)),
        }
    return outcomes


def compare_verifications(previous: dict, candidate: dict) -> dict:
    """Promote only strict improvements and reject newly introduced failures."""
    before = verification_outcomes(previous)
    after = verification_outcomes(candidate)
    fixed = []
    introduced = []
    regressed_checks = []
    for identifier in sorted(set(before) | set(after)):
        old = before.get(identifier)
        new = after.get(identifier)
        if old is None:
            if new and not new["pass"]:
                introduced.extend(f"{identifier}:{name}" for name in new["failures"])
            continue
        if new is None:
            regressed_checks.append(identifier + ":missing-result")
            continue
        if old["pass"] and not new["pass"]:
            regressed_checks.append(identifier)
            introduced.extend(f"{identifier}:{name}" for name in new["failures"])
            continue
        if not old["pass"] and new["pass"]:
            fixed.extend(f"{identifier}:{name}" for name in old["failures"])
            continue
        if old["pass"] and new["pass"]:
            continue
        old_failures = set(old["failures"])
        new_failures = set(new["failures"])
        fixed.extend(f"{identifier}:{name}" for name in sorted(old_failures - new_failures))
        introduced.extend(f"{identifier}:{name}" for name in sorted(new_failures - old_failures))
    if regressed_checks or introduced:
        decision = "REJECT_REGRESSION"
    elif candidate.get("status") == "PASS" and previous.get("status") != "PASS":
        decision = "PROMOTE"
    elif fixed:
        decision = "PROMOTE"
    else:
        decision = "NO_PROGRESS"
    return {
        "schema": "haloclu-verification-comparison-v1",
        "decision": decision,
        "fixed": fixed,
        "introduced": introduced,
        "regressed_checks": regressed_checks,
        "previous_status": previous.get("status"),
        "candidate_status": candidate.get("status"),
        "previous_outcomes": before,
        "candidate_outcomes": after,
    }


def persist_selected_delivery(
    root: Path,
    content: str,
    inspection: dict,
    selection: dict,
    verification: dict,
) -> None:
    """Persist the displayed candidate without rewriting historical attempts."""
    public = {key: value for key, value in inspection.items() if not key.startswith("_")}
    (root / "selected-final.txt").write_text(content)
    (root / "selected-delivery-inspection.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2) + "\n"
    )
    (root / "selected-verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n"
    )
    selected = root / "selected-delivered"
    if selected.exists():
        shutil.rmtree(selected)
    selected.mkdir(mode=0o700)
    for name, value in inspection.get("_file_contents", {}).items():
        safe = _safe_name(name)
        if safe is None:
            raise ValueError("Unsafe selected artifact path")
        target = selected / safe
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.write_text(value)
        target.chmod(0o600)
    payload = dict(selection)
    payload["delivery"] = public
    payload["verification_status"] = verification.get("status", "NOT_RUN")
    (root / "selected-delivery.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )


def missing_units(inspection: dict) -> list[str]:
    values = list(inspection.get("missing_files", []))
    values.extend(name for name in inspection.get("invalid_files", []) if name not in values)
    values.extend(
        f"section:{code}"
        for code in inspection.get("missing_sections", [])
        if code != "C" and f"section:{code}" not in values
    )
    return values[:4]


def extract_section(content: str, code: str, manifest: dict) -> str | None:
    positions = _section_positions(content, manifest)
    bounds = positions.get(code)
    return content[bounds[0]:bounds[1]].strip() if bounds else None


def assemble_structured(
    base_content: str,
    manifest: dict,
    file_contents: dict[str, str],
    section_contents: dict[str, str] | None = None,
) -> str | None:
    codes = [item["code"] for item in manifest.get("required_sections", [])]
    if not {"A", "B", "C", "D"}.issubset(codes):
        return None
    section_contents = dict(section_contents or {})
    a = section_contents.get("A") or extract_section(base_content, "A", manifest)
    b = section_contents.get("B") or extract_section(base_content, "B", manifest)
    d = section_contents.get("D") or extract_section(base_content, "D", manifest)
    if not a or not b or not d:
        return None
    parts = [a, b, "C. Due file completi"]
    for name in manifest.get("required_files", []):
        if name not in file_contents:
            return None
        language = "python" if name.endswith(".py") else PurePosixPath(name).suffix.lstrip(".")
        parts.append(f"### {name}\n\n```{language}\n{file_contents[name].rstrip()}\n```")
    parts.append(d)
    return "\n\n".join(parts).strip() + "\n"
