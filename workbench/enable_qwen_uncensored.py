#!/usr/bin/env python3
"""Fail-closed promotion gate for the optional NODE02 Qwen candidate.

The download/runtime installer can prepare everything without exposing a third
HaloClu backend.  This script writes the small local enable receipt only after
all remote assets and the live private API have been independently attested.
It never starts/stops models or the workbench.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
from typing import Any

import backend_registry as backends
import gguf_metadata
import tokens as qwen_tokens

ROOT = Path("/home/funboy/StrixHaloQw38FN")
GATE = backends.QWEN_UNCENSORED_GATE
REFERENCE_MODEL = Path(
    "/home/funboy/StrixHaloClusterGLM/state/model-downloads/"
    "91c791db62717417225e27249b465f06/data/UD-Q4_K_XL/"
    "Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf"
)
REMOTE_ROOT = Path("/home/funboy/StrixHaloQw38Uncensored")
REMOTE_STATE = Path("/home/funboy/.local/state/strixhaloqw38uncensored")
REFERENCE_TOKENIZER_METADATA_SHA256 = (
    "c7e57b1e2ab166d16b69b7cab6092ed5e17eb228f5ddc7523cc6d3b4759f0f97"
)
CANDIDATE_TOKENIZER_METADATA_SHA256 = (
    "cf9b2b47e9ca19ce7deca36d8380df69daeb141c7af33df3d398d3424db234d2"
)
CANDIDATE_TEMPLATE_SHA256 = (
    "924d3fcc64873b375d5909e8a664ba0aca97c9d6381f5ebda004d1b8a2fb4d64"
)
CANDIDATE_RENDERER_CONTRACT = "qwen38-uncensored-native-template-c706-v1"
TOKENIZER_CORE_KEYS = (
    "tokenizer.ggml.add_bos_token",
    "tokenizer.ggml.bos_token_id",
    "tokenizer.ggml.eos_token_id",
    "tokenizer.ggml.merges",
    "tokenizer.ggml.model",
    "tokenizer.ggml.padding_token_id",
    "tokenizer.ggml.pre",
    "tokenizer.ggml.token_type",
    "tokenizer.ggml.tokens",
)
EXPECTED_SIDECAR_SHA256 = (
    "5ff54097406a905cf3a724c709124ceb0e3e10235ee862298969e91c96fa96e6"
)
EXPECTED_RUNTIME_REVISION = "e03bb9115abc6be469927fed76fddaf1ef0682b3"
EXPECTED_RUNTIME_IMAGE_ID = (
    "7cf4baf499acdc97afd276114511e0e89d32e931f7941c22be75e8bf7579964c"
)


class PromotionError(ValueError):
    pass


def _ssh_json(path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ssh", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "02-evo-x3-tb", "cat", str(path),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise PromotionError(f"remote receipt unavailable: {path.name}")
    try:
        value = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise PromotionError(f"remote receipt is malformed: {path.name}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"remote receipt is not an object: {path.name}")
    return value


def _remote_fingerprint() -> dict[str, Any]:
    completed = subprocess.run(
        [
            "ssh", "-o", "IdentityAgent=none", "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "02-evo-x3-tb",
            str(REMOTE_ROOT / "bin/tokenizer-fingerprint"),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise PromotionError("candidate tokenizer fingerprint failed")
    try:
        value = json.loads(completed.stdout)
    except (ValueError, TypeError) as exc:
        raise PromotionError("candidate tokenizer fingerprint is malformed") from exc
    if not isinstance(value, dict):
        raise PromotionError("candidate tokenizer fingerprint is not an object")
    return value


def _api_json(path: str, timeout: float = 10) -> dict[str, Any]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:18581" + path, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise PromotionError(f"candidate API returned non-object for {path}")
    return value


def validate_evidence(
    acquisition: dict[str, Any],
    model_receipt: dict[str, Any],
    sidecar_receipt: dict[str, Any],
    image_receipt: dict[str, Any],
    reference_fingerprint: dict[str, Any],
    candidate_fingerprint: dict[str, Any],
    health: dict[str, Any],
    models: dict[str, Any],
) -> dict[str, Any]:
    if acquisition.get("repo") != "brainnxdomain/Qwen3.8-Flash-Next-Uncensored-Gufo-GGUF":
        raise PromotionError("unexpected target repository")
    if acquisition.get("revision") != backends.QWEN_UNCENSORED_MODEL_REVISION:
        raise PromotionError("unexpected target revision")
    if model_receipt.get("status") != "PASS":
        raise PromotionError("target verification is not PASS")
    if model_receipt.get("revision") != acquisition.get("revision"):
        raise PromotionError("target verification revision mismatch")
    if model_receipt.get("failures"):
        raise PromotionError("target verification contains failures")
    if sidecar_receipt.get("status") != "PASS":
        raise PromotionError("sidecar verification is not PASS")
    if sidecar_receipt.get("sha256") != EXPECTED_SIDECAR_SHA256:
        raise PromotionError("sidecar digest mismatch")
    if image_receipt.get("status") != "PASS":
        raise PromotionError("runtime image verification is not PASS")
    if image_receipt.get("image_id") != EXPECTED_RUNTIME_IMAGE_ID:
        raise PromotionError("runtime image identity mismatch")
    if "0.8.0" not in str(image_receipt.get("version_output") or ""):
        raise PromotionError("runtime version mismatch")
    if reference_fingerprint.get("sha256") != REFERENCE_TOKENIZER_METADATA_SHA256:
        raise PromotionError("resident tokenizer/template fingerprint changed")
    if candidate_fingerprint.get("sha256") != CANDIDATE_TOKENIZER_METADATA_SHA256:
        raise PromotionError("candidate tokenizer/template fingerprint changed")
    reference_keys = reference_fingerprint.get("per_key")
    candidate_keys = candidate_fingerprint.get("per_key")
    if not isinstance(reference_keys, dict) or not isinstance(candidate_keys, dict):
        raise PromotionError("tokenizer per-key evidence is missing")
    expected_keys = set(reference_keys) | {"tokenizer.ggml.add_eos_token"}
    if set(candidate_keys) != expected_keys:
        raise PromotionError("candidate tokenizer key set is unexpected")
    for key in TOKENIZER_CORE_KEYS:
        if candidate_keys.get(key, {}).get("sha256") != reference_keys.get(key, {}).get("sha256"):
            raise PromotionError(f"candidate tokenizer core differs at {key}")
    if candidate_keys.get("tokenizer.chat_template", {}).get("sha256") != CANDIDATE_TEMPLATE_SHA256:
        raise PromotionError("candidate chat template is not the qualified template")
    if candidate_keys.get("tokenizer.ggml.add_eos_token", {}).get("value") is not False:
        raise PromotionError("candidate add_eos_token contract changed")
    if health.get("status") not in {"ok", "ready"}:
        raise PromotionError("candidate health is not ready")
    rows = models.get("data") if isinstance(models, dict) else None
    if not isinstance(rows, list):
        raise PromotionError("candidate model catalog is missing")
    row = next(
        (item for item in rows if item.get("id") == "qwen38-uncensored-gufo"),
        None,
    )
    if not isinstance(row, dict):
        raise PromotionError("candidate model id is absent")
    capacity = row.get("context_length")
    if capacity not in (131072, 196608):
        raise PromotionError("candidate live context is not an admitted profile")
    return {
        "schema": "haloclu-qwen-uncensored-enable-v1",
        "status": "PASS",
        "backend_id": backends.QWEN_UNCENSORED,
        "model_id": "qwen38-uncensored-gufo",
        "model_revision": backends.QWEN_UNCENSORED_MODEL_REVISION,
        "repo": acquisition["repo"],
        "capacity": capacity,
        "tokenizer_sha256": qwen_tokens.SHA,
        "tokenizer_metadata_sha256": candidate_fingerprint["sha256"],
        "candidate_template_sha256": CANDIDATE_TEMPLATE_SHA256,
        "resident_template_sha256": reference_keys["tokenizer.chat_template"]["sha256"],
        "template_identical": False,
        "template_compatible": True,
        "renderer_contract": CANDIDATE_RENDERER_CONTRACT,
        "runtime_version": "0.8.0",
        "runtime_revision": EXPECTED_RUNTIME_REVISION,
        "runtime_image_id": EXPECTED_RUNTIME_IMAGE_ID,
        "sidecar_sha256": EXPECTED_SIDECAR_SHA256,
        "enabled_at": time.time(),
    }


def build_receipt() -> dict[str, Any]:
    acquisition = _ssh_json(REMOTE_ROOT / "config/acquisition.json")
    model_receipt = _ssh_json(REMOTE_STATE / "receipts/model-verification.json")
    sidecar_receipt = _ssh_json(REMOTE_STATE / "receipts/sidecar-verification.json")
    image_receipt = _ssh_json(REMOTE_STATE / "receipts/image-verification.json")
    reference = gguf_metadata.fingerprint(REFERENCE_MODEL, include_per_key=True)
    candidate = _remote_fingerprint()
    health = _api_json("/health")
    models = _api_json("/v1/models")
    return validate_evidence(
        acquisition,
        model_receipt,
        sidecar_receipt,
        image_receipt,
        reference,
        candidate,
        health,
        models,
    )


def write_gate(receipt: dict[str, Any]) -> None:
    GATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = GATE.with_name(GATE.name + ".tmp")
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(GATE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.disable:
        GATE.unlink(missing_ok=True)
        print(json.dumps({"status": "DISABLED", "gate": str(GATE)}, indent=2))
        return 0
    try:
        receipt = build_receipt()
    except PromotionError as exc:
        print(json.dumps({
            "status": "NOT_ENABLED",
            "gate": str(GATE),
            "reason": str(exc),
        }, ensure_ascii=False, indent=2))
        return 2
    if not args.check_only:
        write_gate(receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
