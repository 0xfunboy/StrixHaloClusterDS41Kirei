#!/usr/bin/env python3
"""benchmark-kirei.py - Benchmark suite targeting KireiStudios DeepSeek V4.1 Flash results on Dual Strix Halo.

Target Metrics:
1. Code decode with DSpark speculative: ~70.6 tok/s
2. Single user decode (general): ~50.0 tok/s
3. Prose decode: ~42-45 tok/s
4. Greedy baseline decode: ~32-35 tok/s
5. Prefill speed (up to 64K context): ~600 tok/s bit-exact
6. Aggregate decode (8 concurrent requests): 108.0 - 113.9 tok/s
"""

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request
from typing import Any, Dict, List

API_BASE = "http://127.0.0.1:18231"

BENCH_PROMPTS = {
    "code": (
        "Write a high-performance C++20 thread-safe ring buffer with lock-free single-producer "
        "single-consumer mechanics, cache-line padding, memory ordering semantics, and full unit tests."
    ),
    "prose": (
        "Discuss the philosophical implications of artificial intelligence achieving self-awareness, "
        "examining classical utilitarian perspectives, Kantian deontological duties, and modern ethical frameworks."
    ),
    "short": "Explain the difference between TCP and UDP in three concise bullet points.",
}


def send_chat_completion(prompt: str, max_tokens: int = 256, temperature: float = 0.0) -> Dict[str, Any]:
    url = f"{API_BASE}/v1/chat/completions"
    payload = {
        "model": "deepseek-v4.1-flash",
        "messages": [
            {"role": "system", "content": "You are a helpful and precise assistant."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=180) as resp:
        t1 = time.perf_counter()
        body = json.loads(resp.read().decode("utf-8"))

    elapsed = t1 - t0
    usage = body.get("usage", {})
    completion_tokens = usage.get("completion_tokens", max_tokens)
    prompt_tokens = usage.get("prompt_tokens", 0)
    tok_per_sec = completion_tokens / elapsed if elapsed > 0 else 0
    strix_info = body.get("strix", {})
    strix_decode_tps = strix_info.get("decode_tokens_per_second", tok_per_sec)

    return {
        "elapsed_sec": elapsed,
        "completion_tokens": completion_tokens,
        "prompt_tokens": prompt_tokens,
        "tokens_per_sec": tok_per_sec,
        "strix_decode_tps": strix_decode_tps,
        "content_sample": body["choices"][0]["message"]["content"][:120].strip() if body.get("choices") else "",
    }


def run_single_user_suite():
    print("\n--- Running Single User Benchmark Suite ---")
    results = {}
    for arm in ["code", "prose"]:
        prompt = BENCH_PROMPTS[arm]
        print(f"Testing Arm: {arm} (Max tokens: 256)...")
        trials = []
        for i in range(3):
            res = send_chat_completion(prompt, max_tokens=256)
            trials.append(res["tokens_per_sec"])
            print(f"  Trial {i+1}: {res['tokens_per_sec']:.2f} tok/s ({res['elapsed_sec']:.2f}s, {res['completion_tokens']} tok)")
            time.sleep(1)
        avg = statistics.mean(trials)
        results[arm] = avg
        print(f"  -> Mean {arm} Decode: {avg:.2f} tok/s")
    return results


def run_concurrent_suite(concurrency: int = 8, tokens: int = 128):
    print(f"\n--- Running Concurrent Benchmark Suite ({concurrency} users) ---")
    prompt = BENCH_PROMPTS["code"]
    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_chat_completion, prompt, max_tokens=tokens) for _ in range(concurrency)]
        outcomes = [f.result() for f in futures]
    total_elapsed = time.perf_counter() - t0

    total_tokens = sum(o["completion_tokens"] for o in outcomes)
    aggregate_tps = total_tokens / total_elapsed if total_elapsed > 0 else 0
    per_user_avg = statistics.mean(o["tokens_per_sec"] for o in outcomes)

    print(f"Completed {concurrency} concurrent requests:")
    print(f"  Total tokens generated: {total_tokens}")
    print(f"  Total wall time: {total_elapsed:.2f}s")
    print(f"  Per-user average rate: {per_user_avg:.2f} tok/s")
    print(f"  AGGREGATE THROUGHPUT: {aggregate_tps:.2f} tok/s")
    return {
        "concurrency": concurrency,
        "total_tokens": total_tokens,
        "wall_time_sec": total_elapsed,
        "per_user_avg_tok_s": per_user_avg,
        "aggregate_tok_s": aggregate_tps,
    }


def main():
    parser = argparse.ArgumentParser(description="KireiStudios Dual Strix Halo Benchmark Suite")
    parser.add_argument("--single", action="store_true", help="Run single user tests")
    parser.add_argument("--concurrent", action="store_true", help="Run concurrent tests")
    parser.add_argument("--all", action="store_true", help="Run full test suite")
    args = parser.parse_args()

    if not (args.single or args.concurrent or args.all):
        args.all = True

    print("Checking API endpoint connectivity...")
    try:
        with urllib.request.urlopen(f"{API_BASE}/v1/models", timeout=3) as resp:
            models = json.loads(resp.read().decode("utf-8"))
            print(f"API Online. Available models: {[m.get('id') for m in models.get('data', [])]}")
    except Exception as e:
        print(f"Could not connect to {API_BASE}/v1/models: {e}")
        print("Please start the dual-node cluster first via scripts/launch-pair.sh")
        return

    if args.single or args.all:
        run_single_user_suite()
    if args.concurrent or args.all:
        run_concurrent_suite(concurrency=8, tokens=128)


if __name__ == "__main__":
    main()

