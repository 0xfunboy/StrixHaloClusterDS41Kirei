"""Allowlisted resident backends for HaloClu.

The browser sends only a stable backend_id.  URLs, model IDs, tokenizers,
resource telemetry and supported parameters are resolved here on NODE01.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

from tokenizers import Tokenizer

import common as c
import tokens as qwen_tokens

DEEPSEEK_KIREI = "deepseek-kirei"
QWEN = "qwen-gufo"
MIMO = "mimo-kyojin"
QWEN_UNCENSORED = "qwen-uncensored-gufo"
DEFAULT = DEEPSEEK_KIREI
DEEPSEEK_KIREI_ADAPTER = "deepseek-kirei"
GUFO_QWEN_ADAPTER = "gufo-qwen"
MIMO_KYOJIN_ADAPTER = "mimo-kyojin"
DEEPSEEK_TOKENIZER_PATH = Path("/home/funboy/models/ds41/kirei/tokenizer.json")
MARGIN_TOKENS = 1024
QWEN_UNCENSORED_MODEL_REVISION = "c706902043613af036f996682ce25b7c12270d16"
QWEN_UNCENSORED_GATE = Path(
    "/home/funboy/.local/state/strixhaloqw38fn/backend-metadata/"
    "qwen-uncensored/enabled.json"
)
QWEN_DEFAULT_REASONING_EFFORT = "xhigh"
# The resident Qwen chat template accepts exactly these effective levels.
# Gufo also accepts the legacy alias "high", but the model template maps it
# to xhigh; HaloClu normalizes that alias instead of exposing a duplicate.
QWEN_REASONING_EFFORTS = ("low", "medium", "xhigh")
MINIMUM_AVAILABLE_BYTES = 8 * 1024**3
MIMO_TOKENIZER_SHA256 = "ff15eb925890d6b71b5160de4b846fbd13178438ab463b38ecc953e8cd1dcb3e"
MIMO_TOKENIZER = Path("/home/funboy/.local/state/strixhaloqw38fn/backend-metadata/mimo/tokenizer.json")
MIMO_DEFAULT_PROFILE = "standard"
MIMO_ACCURATE_PROFILE = "accurate-v1"
MIMO_ACCURATE_ENABLED = True  # limited promotion: 2/3 PASS vs 1/3, no pass regression
MIMO_ACCURATE_INSTRUCTION = (
    "Privilegia la correttezza verificabile e il rispetto integrale della richiesta. "
    "Prima di finalizzare, controlla requisiti, casi limite, calcoli e coerenza interna. "
    "Per il codice, controlla che implementazione, fixture e asserzioni dei test siano compatibili, "
    "che ogni test abbia stato isolato e che i casi negativi non compromettano quelli legittimi. "
    "Per documenti e dati, distingui fatti supportati, inferenze e informazioni mancanti; non inventare citazioni. "
    "Se trovi una contraddizione risolvibile nei dati, correggila; se manca una premessa necessaria, dichiaralo. "
    "Consegna tutti gli artefatti richiesti e motivazioni sintetiche verificabili, senza inventare esecuzioni "
    "o diluire la risposta con ripetizioni. Il contratto di output del workflow resta valido."
)


@dataclass(frozen=True)
class BackendSpec:
    id: str
    label: str
    node: str
    engine: str
    base: str
    model_id: str
    capacity: int
    concurrency: int
    thinking_requested: str
    thinking_effective: str
    speculative: str
    recovery: str
    adapter: str
    short_label: str

    def public(self) -> dict:
        default_output = 4096 if self.capacity <= 16384 else (8192 if self.capacity <= 32768 else 32768)
        max_output = min(32768, max(1024, self.capacity - 1024 - 1024))
        value = {
            "id": self.id,
            "label": self.label,
            "node": self.node,
            "engine": self.engine,
            "model_id": self.model_id,
            "capacity": self.capacity,
            "concurrency": self.concurrency,
            "thinking_requested": self.thinking_requested,
            "thinking_effective": self.thinking_effective,
            "speculative": self.speculative,
            "recovery": self.recovery,
            "adapter": self.adapter,
            "short_label": self.short_label,
            "default_output": default_output,
            "max_output": max_output,
        }
        if self.adapter == GUFO_QWEN_ADAPTER:
            value.update(
                reasoning_effort_default=QWEN_DEFAULT_REASONING_EFFORT,
                reasoning_efforts=[
                    {"id": "low", "label": "Low", "available": True},
                    {"id": "medium", "label": "Medium", "available": True},
                    {"id": "xhigh", "label": "XHigh", "available": True},
                ],
            )
        if self.adapter == MIMO_KYOJIN_ADAPTER:
            value.update(
                quality_profile_default=MIMO_DEFAULT_PROFILE,
                quality_profiles=[
                    {"id": MIMO_DEFAULT_PROFILE, "label": "Standard", "available": True,
                     "description": "Native thinking, unmodified application instruction."},
                    {"id": MIMO_ACCURATE_PROFILE, "label": "Accurato · verifica limitata", "available": MIMO_ACCURATE_ENABLED,
                     "description": "Limited 3-task qualification: improved code/fixture coherence; not a general intelligence claim."},
                ],
            )
        return value


def _uncensored_gate() -> dict | None:
    """Admit the optional NODE02 candidate only after a local deployment receipt.

    Preparing code, a forward, or model metadata must never expose an unusable
    backend in HaloClu.  The final installer writes this small receipt only
    after target hashes, runtime, sidecar, tokenizer/template compatibility and
    the live context have all passed.
    """
    try:
        value = json.loads(QWEN_UNCENSORED_GATE.read_text())
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return None
    required = {
        "status": "PASS",
        "backend_id": QWEN_UNCENSORED,
        "model_id": "qwen38-uncensored-gufo",
        "model_revision": QWEN_UNCENSORED_MODEL_REVISION,
        "tokenizer_sha256": qwen_tokens.SHA,
        "template_compatible": True,
        "candidate_template_sha256": qwen_tokens.UNCENSORED_TEMPLATE_SHA256,
        "renderer_contract": qwen_tokens.UNCENSORED_RENDERER_CONTRACT,
        "runtime_version": "0.8.0",
        "runtime_revision": "e03bb9115abc6be469927fed76fddaf1ef0682b3",
        "sidecar_sha256": "5ff54097406a905cf3a724c709124ceb0e3e10235ee862298969e91c96fa96e6",
    }
    if any(value.get(key) != expected for key, expected in required.items()):
        return None
    if value.get("capacity") not in (131072, 196608):
        return None
    return value


_UNCENSORED_GATE_VALUE = _uncensored_gate()

ALL_SPECS = {
    DEEPSEEK_KIREI: BackendSpec(
        id=DEEPSEEK_KIREI,
        label="DeepSeek V4.1 Flash (Dual Strix)",
        node="Dual Node (USB4)",
        engine="Kirei HIP gfx1151",
        base="http://127.0.0.1:18231",
        model_id="deepseek-v4.1-flash",
        capacity=32768,
        concurrency=8,
        thinking_requested="auto",
        thinking_effective="standard",
        speculative="DSpark MXFP4",
        recovery="bounded-final-only",
        adapter=DEEPSEEK_KIREI_ADAPTER,
        short_label="DeepSeek",
    ),
    QWEN: BackendSpec(
        id=QWEN,
        label="Qwen3.8-Flash-Next Uncensored",
        node="NODE01",
        engine="Gufo v0.8.0",
        base="http://127.0.0.1:18561",
        model_id="qwen38-uncensored-gufo",
        capacity=196608,
        concurrency=1,
        thinking_requested="xhigh",
        thinking_effective="xhigh",
        speculative="shared-Q8 MTP ceiling 7",
        recovery="bounded-final-only",
        adapter=GUFO_QWEN_ADAPTER,
        short_label="Qwen",
    ),
    MIMO: BackendSpec(
        id=MIMO,
        label="MiMo-V2.6-Flash-MOPD",
        node="NODE02",
        engine="Kyojin a3ac00d4",
        base="http://127.0.0.1:18571",
        model_id="mimo-mopd-kyojin",
        capacity=131072,
        concurrency=1,
        thinking_requested="native",
        thinking_effective="native-on; no separate HIGH",
        speculative="DFlash ndt 7 lossless",
        recovery="bounded-final-only",
        adapter=MIMO_KYOJIN_ADAPTER,
        short_label="MiMo",
    ),
    QWEN_UNCENSORED: BackendSpec(
        id=QWEN_UNCENSORED,
        label="Qwen3.8-Flash-Next Uncensored",
        node="NODE02",
        engine="Gufo v0.8.0",
        base="http://127.0.0.1:18581",
        model_id="qwen38-uncensored-gufo",
        capacity=int((_UNCENSORED_GATE_VALUE or {}).get("capacity", 196608)),
        concurrency=1,
        thinking_requested="xhigh",
        thinking_effective="xhigh",
        speculative="shared-Q8 MTP ceiling 7",
        recovery="bounded-final-only",
        adapter=GUFO_QWEN_ADAPTER,
        short_label="Qwen-U",
    ),
}

SPECS = {
    DEEPSEEK_KIREI: ALL_SPECS[DEEPSEEK_KIREI],
    QWEN: ALL_SPECS[QWEN],
    MIMO: ALL_SPECS[MIMO],
}

HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_MIMO_TOKENIZER: Tokenizer | None = None


def qwen_reasoning_effort(value: object) -> str:
    if value in (None, "", "auto"):
        return QWEN_DEFAULT_REASONING_EFFORT
    # Backward-compatible normalization for historical clients/jobs.
    if value == "high":
        return "xhigh"
    if isinstance(value, str) and value in QWEN_REASONING_EFFORTS:
        return value
    raise ValueError("Qwen reasoning effort must be low, medium or xhigh")


def mimo_quality_profile(value: object, *, allow_experimental: bool = False) -> str:
    if value in (None, "", MIMO_DEFAULT_PROFILE):
        return MIMO_DEFAULT_PROFILE
    if value == MIMO_ACCURATE_PROFILE and (MIMO_ACCURATE_ENABLED or allow_experimental):
        return MIMO_ACCURATE_PROFILE
    raise ValueError("Unknown or unavailable MiMo quality profile")


def quality_instruction(value: object, *, allow_experimental: bool = False) -> str:
    profile = mimo_quality_profile(value, allow_experimental=allow_experimental)
    return MIMO_ACCURATE_INSTRUCTION if profile == MIMO_ACCURATE_PROFILE else ""


def validate_id(value: object) -> str:
    if value == QWEN_UNCENSORED:
        return QWEN
    if not isinstance(value, str) or value not in SPECS:
        return DEFAULT
    return value


def spec(value: object) -> BackendSpec:
    return SPECS[validate_id(value)]


def uses_qwen_adapter(value: object) -> bool:
    return spec(value).adapter == GUFO_QWEN_ADAPTER


def uses_mimo_adapter(value: object) -> bool:
    return spec(value).adapter == MIMO_KYOJIN_ADAPTER


def uses_deepseek_adapter(value: object) -> bool:
    return spec(value).adapter == DEEPSEEK_KIREI_ADAPTER


def request(backend_id: str, path: str, value=None, timeout: float = 10):
    backend = spec(backend_id)
    data = None if value is None else json.dumps(value, ensure_ascii=False).encode()
    req = urllib.request.Request(
        backend.base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    with HTTP.open(req, timeout=timeout) as response:
        return json.load(response)


def open_stream(backend_id: str, path: str, value: dict, timeout: float):
    backend = spec(backend_id)
    req = urllib.request.Request(
        backend.base + path,
        data=json.dumps(value, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    return HTTP.open(req, timeout=timeout)


def health(backend_id: str, timeout: float = 5) -> dict:
    try:
        return request(backend_id, "/health", timeout=timeout)
    except Exception as exc:
        try:
            m = models(backend_id, timeout=timeout)
            if isinstance(m, dict) and m.get("data"):
                return {"status": "ok", "source": "v1/models"}
        except Exception:
            pass
        raise exc


def models(backend_id: str, timeout: float = 5) -> dict:
    return request(backend_id, "/v1/models", timeout=timeout)


def slots(backend_id: str, timeout: float = 5) -> list[dict]:
    try:
        value = request(backend_id, "/slots", timeout=timeout)
        if isinstance(value, list):
            return value
    except Exception:
        pass
    return []


def idle(backend_id: str) -> bool:
    s = slots(backend_id)
    if not s:
        return True
    return all(
        item.get("state", 0) == 0 and not item.get("is_processing", False)
        for item in s
    )


def wait_idle(backend_id: str, timeout: float = 120) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if idle(backend_id):
                return True
        except Exception:
            pass
        time.sleep(2)
    return False


def local_available_bytes() -> int:
    return int(
        next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
    ) * 1024


def remote_available_bytes(timeout: float = 8) -> int:
    completed = subprocess.run(
        [
            "ssh",
            "-o", "IdentityAgent=none",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=5",
            "02-evo-x3-tb",
            "awk '/^MemAvailable:/ {print $2}' /proc/meminfo",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("NODE02 memory telemetry unavailable")
    try:
        value = int(completed.stdout.strip()) * 1024
    except ValueError as exc:
        raise RuntimeError("NODE02 memory telemetry malformed") from exc
    if value <= 0:
        raise RuntimeError("NODE02 memory telemetry invalid")
    return value


def available_bytes(backend_id: str, timeout: float = 8) -> int:
    backend = spec(backend_id)
    return local_available_bytes() if backend.node in ("NODE01", "Dual Node (USB4)") else remote_available_bytes(timeout)


def _model_capacity(backend_id: str, timeout: float = 5) -> int:
    backend = spec(backend_id)
    value = models(backend.id, timeout=timeout)
    rows = value.get("data") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError(f"{backend.label} model catalog is unavailable")
    row = next((item for item in rows if item.get("id") == backend.model_id), None)
    ctx = row.get("context_length") if isinstance(row, dict) else None
    if type(ctx) is not int or ctx <= 0:
        raise RuntimeError(f"{backend.label} live context capacity is unavailable")
    return ctx


def effective_capacity(backend_id: str, timeout: float = 5) -> int:
    backend = spec(backend_id)
    if backend.id == QWEN:
        return backend.capacity
    if backend.adapter in (GUFO_QWEN_ADAPTER, DEEPSEEK_KIREI_ADAPTER):
        try:
            return _model_capacity(backend.id, timeout=timeout)
        except Exception:
            return backend.capacity
    try:
        value = health(backend.id, timeout=timeout)
        ctx = value.get("ctx") if isinstance(value, dict) else None
        if type(ctx) is int and ctx > 0:
            return ctx
    except Exception:
        pass
    return backend.capacity


_DEEPSEEK_TOKENIZER: Tokenizer | None = None


def _deepseek_tokenizer() -> Tokenizer:
    global _DEEPSEEK_TOKENIZER
    if _DEEPSEEK_TOKENIZER is None:
        if not DEEPSEEK_TOKENIZER_PATH.is_file():
            raise RuntimeError(f"DeepSeek tokenizer not found at {DEEPSEEK_TOKENIZER_PATH}")
        _DEEPSEEK_TOKENIZER = Tokenizer.from_file(str(DEEPSEEK_TOKENIZER_PATH))
    return _DEEPSEEK_TOKENIZER


def _mimo_tokenizer() -> Tokenizer:
    global _MIMO_TOKENIZER
    if _MIMO_TOKENIZER is None:
        if not MIMO_TOKENIZER.is_file():
            raise RuntimeError("MiMo tokenizer metadata is not installed on NODE01")
        digest = hashlib.sha256(MIMO_TOKENIZER.read_bytes()).hexdigest()
        if digest != MIMO_TOKENIZER_SHA256:
            raise RuntimeError("MiMo tokenizer metadata digest mismatch")
        _MIMO_TOKENIZER = Tokenizer.from_file(str(MIMO_TOKENIZER))
    return _MIMO_TOKENIZER


def render_mimo(messages: list[dict], *, enable_thinking: bool | None = None) -> str:
    body: dict[str, object] = {"messages": messages}
    if enable_thinking is not None:
        if not isinstance(enable_thinking, bool):
            raise ValueError("enable_thinking must be boolean")
        body["enable_thinking"] = enable_thinking
    value = request(MIMO, "/apply-template", body, timeout=20)
    prompt = value.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise RuntimeError("MiMo native renderer returned no prompt")
    if enable_thinking is False and not prompt.rstrip().endswith("<think></think>"):
        raise RuntimeError("MiMo native renderer did not attest enable_thinking=false")
    return prompt


def count(
    backend_id: str,
    messages: list[dict],
    output: int,
    *,
    enable_thinking: bool | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    backend = spec(backend_id)
    qwen_tokens.integer(output, "max_output")
    if output <= 0:
        raise ValueError("Output budget must be positive")
    capacity = effective_capacity(backend.id)
    if backend.adapter == DEEPSEEK_KIREI_ADAPTER:
        tok = _deepseek_tokenizer()
        rendered = ""
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            rendered += f"<｜{role.capitalize()}｜>{content}<｜end of sentence｜>"
        input_tokens = len(tok.encode(rendered, add_special_tokens=False).ids)
        available_output = capacity - input_tokens - MARGIN_TOKENS
        if available_output <= 0:
            raise ValueError(
                f"Context rejected before dispatch: input {input_tokens} + margin {MARGIN_TOKENS} >= capacity {capacity}; no room for response"
            )
        effective_output = min(output, available_output)
        required = input_tokens + effective_output + MARGIN_TOKENS
        measured = {
            "input_tokens": input_tokens,
            "max_output": effective_output,
            "margin": MARGIN_TOKENS,
            "required": required,
            "capacity": capacity,
            "reasoning_requested": "auto",
            "reasoning_effective": "auto",
            "renderer_contract": "deepseek-v4.1-chat",
            "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        }
    elif backend.adapter == GUFO_QWEN_ADAPTER:
        effort = qwen_reasoning_effort(reasoning_effort)
        measured = (
            qwen_tokens.count_uncensored(
                messages, output, capacity, reasoning_effort=effort,
            ) if backend.model_id == "qwen38-uncensored-gufo" else
            qwen_tokens.count(messages, output, capacity, reasoning_effort=effort)
        )
    else:
        prompt = render_mimo(messages, enable_thinking=enable_thinking)
        input_tokens = len(_mimo_tokenizer().encode(prompt, add_special_tokens=False).ids)
        required = input_tokens + output + MARGIN_TOKENS
        if required > capacity:
            raise ValueError(
                f"Context rejected before dispatch: input{input_tokens}+output{output}+"
                f"margin{MARGIN_TOKENS}={required}, capacity{capacity}; no truncation"
            )
        thinking = enable_thinking is not False
        measured = {
            "input_tokens": input_tokens,
            "max_output": output,
            "margin": MARGIN_TOKENS,
            "required": required,
            "capacity": capacity,
            "reasoning_requested": "native" if thinking else "none",
            "reasoning_effective": (
                "native-on; no separate HIGH"
                if thinking else
                "disabled by native enable_thinking=false template"
            ),
            "enable_thinking": enable_thinking,
            "tokenizer_sha256": MIMO_TOKENIZER_SHA256,
            "renderer_contract": "mimo-kyojin-native-apply-template-v2",
            "rendered_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        }
    measured.update(
        backend_id=backend.id,
        upstream_model_id=backend.model_id,
        node=backend.node,
        engine=backend.engine,
    )
    return measured


def payload(
    backend_id: str,
    messages: list[dict],
    output: int,
    kind: str,
    *,
    enable_thinking: bool | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    backend = spec(backend_id)
    if backend.adapter == DEEPSEEK_KIREI_ADAPTER:
        return {
            "model": backend.model_id,
            "messages": messages,
            "max_tokens": output,
            "temperature": 0.7 if kind != "code" else 0.2,
            "top_p": 0.95,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    if backend.adapter == GUFO_QWEN_ADAPTER:
        effort = qwen_reasoning_effort(reasoning_effort)
        return {
            "model": backend.model_id,
            "messages": messages,
            "max_tokens": output,
            "reasoning_effort": effort,
            "preserve_thinking": False,
            "temperature": 1.0,
            "top_p": 0.95,
            "top_k": 20,
            "min_p": 0,
            "repeat_penalty": 1,
            "presence_penalty": 0,
            "frequency_penalty": 0,
            "seed": 64001 if kind in ("code", "node") else 64004,
            "cache_prompt": True,
            "stream": True,
            "return_progress": True,
            "stream_options": {"include_usage": True},
        }
    body = {
        "model": backend.model_id,
        "messages": messages,
        "max_completion_tokens": output,
        "temperature": 1.0,
        "top_p": 0.95,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if enable_thinking is not None:
        if not isinstance(enable_thinking, bool):
            raise ValueError("enable_thinking must be boolean")
        body["enable_thinking"] = enable_thinking
    return body


def status(backend_id: str) -> dict:
    """Return bounded, partial backend telemetry without conflating probes.

    Model health is the transport/engine availability gate.  Optional model,
    slot and memory probes are independent so an SSH telemetry timeout cannot
    falsely declare a healthy resident dead.  The probes run concurrently to
    keep the authenticated HaloClu status endpoint responsive while a backend
    is generating.
    """
    backend = spec(backend_id)
    result = backend.public()
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix=f"haloclu-{backend.id}-status")
    futures = {
        "health": executor.submit(health, backend.id, 2.5),
        "models": executor.submit(models, backend.id, 2.5),
        "slots": executor.submit(slots, backend.id, 2.5),
        "memory": executor.submit(available_bytes, backend.id, 3.5),
    }
    values: dict[str, object] = {}
    errors: dict[str, str] = {}
    deadline = time.monotonic() + 4.0
    try:
        for name, future in futures.items():
            try:
                values[name] = future.result(timeout=max(0.01, deadline - time.monotonic()))
            except Exception as exc:
                errors[name] = str(exc) or type(exc).__name__
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    health_value = values.get("health")
    models_value = values.get("models")
    has_valid_models = (
        isinstance(models_value, dict)
        and bool(models_value.get("data"))
        and any(
            isinstance(item, dict) and item.get("id") == backend.model_id
            for item in models_value.get("data", [])
        )
    )
    available = (
        isinstance(health_value, dict) and health_value.get("status") in {"ok", "ready"}
    ) or has_valid_models

    if available and "health" in errors and has_valid_models:
        errors.pop("health", None)
        if not isinstance(health_value, dict) or not health_value:
            health_value = {"status": "ok", "source": "v1/models"}
            values["health"] = health_value

    current_slots = values.get("slots") if isinstance(values.get("slots"), list) else None
    result["health"] = health_value if isinstance(health_value, dict) else {}
    live_capacity = None
    if backend.adapter == MIMO_KYOJIN_ADAPTER and isinstance(health_value, dict):
        live_capacity = health_value.get("ctx")
    elif backend.adapter in (GUFO_QWEN_ADAPTER, DEEPSEEK_KIREI_ADAPTER) and isinstance(values.get("models"), dict):
        rows = values["models"].get("data")
        if isinstance(rows, list):
            row = next((item for item in rows if item.get("id") == backend.model_id), None)
            if isinstance(row, dict):
                live_capacity = row.get("context_length")
    if type(live_capacity) is int and live_capacity > 0:
        result["configured_capacity"] = backend.capacity
        result["capacity"] = live_capacity
        result["default_output"] = 4096 if live_capacity <= 16384 else (8192 if live_capacity <= 32768 else 32768)
        result["max_output"] = min(32768, max(1024, live_capacity - 1024 - 1024))
        if live_capacity != backend.capacity:
            errors["capacity"] = (
                f"live {backend.label} context {live_capacity} differs from configured {backend.capacity}"
            )
    result["models"] = values.get("models") if isinstance(values.get("models"), dict) else {
        "object": "list",
        "data": [{"id": backend.model_id, "object": "model", "owned_by": "local"}],
    }
    result["slots"] = current_slots or []
    result["native_busy"] = (
        any(item.get("state", 0) != 0 or item.get("is_processing", False) for item in current_slots)
        if current_slots is not None else None
    )
    result["available_bytes"] = values.get("memory") if type(values.get("memory")) is int else None
    result["available"] = available
    result["transport_state"] = "ready" if available else "unreachable"
    result["engine_state"] = (
        "busy" if result["native_busy"] is True else
        "ready" if result["native_busy"] is False and available else
        "unknown"
    )
    result["probe_errors"] = errors
    result["error"] = errors.get("health") if not available else None
    non_health = [f"{name}: {message}" for name, message in errors.items() if name != "health"]
    result["status_note"] = "Partial telemetry — " + "; ".join(non_health) if non_health else None
    return result


def public_registry() -> list[dict]:
    return [backend.public() for backend in SPECS.values()]
