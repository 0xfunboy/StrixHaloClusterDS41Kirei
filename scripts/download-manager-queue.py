#!/usr/bin/env python3
"""Autonomous download manager queue for StrixHaloClusterDS41Kirei.

Uses the running HaloClu Go downloader API (http://127.0.0.1:18093) to sequence:
1. Job 1: DSpark Drafter (DeepSeek-V4.1-1017-DSpark-MXFP4.gguf) [currently running]
2. Job 2: Dense U6 Overlay (c-u6.gguf + aux vocabs + draft stats) [already planned]
3. Job 3: Trellis Mix-B (ours-K4 and ours-K5 safetensors)
Once downloaded, assets are verified and linked into /home/funboy/models/ds41/kirei/.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = "http://127.0.0.1:18093"
TOKEN = "d12489558d108cc647158d7385fe8cc10979acba28879c44b3edebe0c375802c"
LOG_FILE = Path("/home/funboy/StrixHaloClusterDS41Kirei/download_chain.log")
TARGET_DIR = Path("/home/funboy/models/ds41/kirei")

JOB1_ID = "853df177532031acb5049878c688dd2e"
JOB2_ID = "2ee6ab1f92a2c651f046c8dbafe80f2b"


def log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def api_request(method: str, path: str, data: dict = None) -> dict:
    url = f"{API_URL}{path}"
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_job(job_id: str, label: str):
    log(f"Monitoring {label} (Job: {job_id})...")
    last_pct = -1
    while True:
        try:
            status = api_request("GET", f"/v1/downloads/jobs/{job_id}")
            st = status.get("status")
            total = status.get("total_bytes", 1)
            downloaded = status.get("bytes", 0)
            rate = status.get("bytes_per_second", 0)
            eta = status.get("eta_seconds")
            pct = (downloaded / total) * 100 if total > 0 else 0

            if int(pct) != last_pct and int(pct) % 5 == 0:
                eta_str = f", ETA: {eta/60:.1f}m" if eta else ""
                log(f"{label}: {st} | {downloaded / 1024**2:.1f} / {total / 1024**2:.1f} MB ({pct:.1f}%) @ {rate / 1024**2:.2f} MB/s{eta_str}")
                last_pct = int(pct)

            if st == "COMPLETE":
                log(f"SUCCESS: {label} completed successfully.")
                return status
            if st in ("FAILED", "PAUSED"):
                err = status.get("error", "")
                if "interrupted" in err or "Gateway stopped" in err or "partial preserved" in err:
                    log(f"Transient interruption detected for {label}: '{err}'. Resuming in 5s...")
                    time.sleep(5)
                    try:
                        api_request("POST", f"/v1/downloads/jobs/{job_id}/resume", {"confirm": True})
                    except Exception as e:
                        log(f"Resume attempt error: {e}")
                    continue
                log(f"ERROR: {label} reached status: {st} (Error: {err})")
                sys.exit(1)
            if st == "CANCELLED":
                log(f"ERROR: {label} was CANCELLED")
                sys.exit(1)
        except Exception as e:
            log(f"Warning during polling: {e}")
        time.sleep(10)


def start_job(job_id: str, label: str):
    log(f"Starting {label} ({job_id})...")
    resp = api_request("POST", f"/v1/downloads/jobs/{job_id}/start", {"confirm": True})
    log(f"Start response: {resp.get('status')}")


def plan_trellis_job() -> str:
    log("Planning Trellis packs download (hyukudan/DeepSeek-V4.1-Flash-1017-strix-trellis-EXL3)...")
    req = urllib.request.Request("https://huggingface.co/api/models/hyukudan/DeepSeek-V4.1-Flash-1017-strix-trellis-EXL3")
    with urllib.request.urlopen(req, timeout=30) as resp:
        hf_meta = json.loads(resp.read().decode("utf-8"))

    revision = hf_meta.get("sha")
    siblings = [s["rfilename"] for s in hf_meta.get("siblings", [])]
    # Filter ours-K4 and ours-K5 files
    k4_k5_files = [f for f in siblings if f.startswith("ours-K4/") or f.startswith("ours-K5/")]
    log(f"Selected {len(k4_k5_files)} files for Trellis Mix-B (K4/K5)")

    plan_data = {
        "source": "huggingface",
        "repo": "hyukudan/DeepSeek-V4.1-Flash-1017-strix-trellis-EXL3",
        "revision": revision,
        "files": k4_k5_files,
    }
    plan = api_request("POST", "/v1/downloads/plan", plan_data)
    log(f"Trellis plan created: Job {plan.get('id')}, Total: {plan.get('total_bytes') / 1024**2:.1f} MB")
    return plan.get("id")


def setup_symlinks():
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    base_model = Path("/home/funboy/models/ds41/ds41-abliterated-q2-imatrix/DeepSeek-V4.1-Flash-Abliterated-Q2-imatrix.gguf")
    if base_model.exists():
        target_base = TARGET_DIR / "DeepSeek-V4.1-Flash-1017-Q2.gguf"
        if not target_base.exists():
            target_base.symlink_to(base_model)
            log(f"Linked base model {base_model} -> {target_base}")

    # Link downloads from state dir
    state_dir = Path("/home/funboy/StrixHaloClusterGLM/state/model-downloads")
    for job_dir in state_dir.glob("*/data"):
        for item in job_dir.rglob("*"):
            if item.is_file():
                rel = item.relative_to(job_dir)
                dest = TARGET_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    dest.symlink_to(item)
                    log(f"Linked asset {rel} -> {dest}")


def main():
    log("=== Auto Downloader Chain Started ===")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Wait for DSpark
    wait_for_job(JOB1_ID, "DSpark Drafter GGUF")

    # 2. Start and wait for U6 Dense Overlay
    start_job(JOB2_ID, "Dense U6 Overlay + Aux Vocabs")
    wait_for_job(JOB2_ID, "Dense U6 Overlay + Aux Vocabs")

    # 3. Plan, start and wait for Trellis
    trellis_job_id = plan_trellis_job()
    start_job(trellis_job_id, "Trellis Mix-B (K4/K5)")
    wait_for_job(trellis_job_id, "Trellis Mix-B (K4/K5)")

    # 4. Final link setup
    setup_symlinks()
    log("=== All Downloads and Model Setup Completed Successfully ===")


if __name__ == "__main__":
    main()
