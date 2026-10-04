"""Concurrent-load benchmark — PRAGMA under parallel clients (paper §6.2, P4).

Fires N parallel client workers, each issuing M sequential requests, against a
real uvicorn server over TCP. Reports aggregate throughput and latency
percentiles — answers the reviewer objection that the benchmarks show no
evidence of behaviour under contention.

Run:  uv run python benchmarks/bench_concurrent.py
"""

from __future__ import annotations

import os
import signal
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

PORT = int(os.environ.get("PRAGMA_BENCH_PORT", "8007"))
BASE = f"http://127.0.0.1:{PORT}"
N_WORKERS = 16
N_PER_WORKER = 100

_REPO = Path(__file__).resolve().parent.parent


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "pragma"],
        cwd=_REPO,
        env={**os.environ, "PORT": str(PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            with httpx.Client() as client:
                client.get(f"{BASE}/nfvintent/v1/intents")
            return proc
        except httpx.HTTPError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("server did not become ready")


def stop_server(proc: subprocess.Popen) -> None:
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[idx]


def _intent_payload(i: int) -> dict:
    return {
        "intent": {
            "intentId": f"conc-{i}",
            "intentName": "latency-budget",
            "intentExpectations": [
                {
                    "expectationId": "exp-01",
                    "expectationName": "latency_ms < 5",
                    "expectationType": "Property",
                    "expectationObject": {"objectId": "service-01", "objectType": "service"},
                    "expectationTargets": [{"expectationTargetId": "service-01"}],
                    "expectationContexts": [
                        {
                            "contextId": "ctx-01",
                            "contextName": "steady-state",
                            "contextConditions": [
                                {"conditionId": "c-01", "operator": "less than", "conditionValue": 5}
                            ],
                        }
                    ],
                }
            ],
        }
    }


def _worker(worker_id: int) -> list[float]:
    lat: list[float] = []
    with httpx.Client(base_url=BASE, timeout=5.0) as client:
        for j in range(N_PER_WORKER):
            t0 = time.perf_counter()
            r = client.post(
                "/nfvintent/v1/intents",
                json=_intent_payload(worker_id * 1000 + j),
            )
            assert r.status_code == 201, r.text
            iid = r.json()["intentObjectInstanceId"]
            r2 = client.get(f"/nfvintent/v1/intents/{iid}")
            assert r2.status_code == 200
            r3 = client.delete(f"/nfvintent/v1/intents/{iid}")
            assert r3.status_code == 204
            lat.append((time.perf_counter() - t0) * 1000)
    return lat


def main() -> None:
    proc = start_server()
    try:
        total_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=N_WORKERS) as pool:
            results = list(pool.map(_worker, range(N_WORKERS)))
        elapsed = time.perf_counter() - total_start

        all_lat = [v for r in results for v in r]
        total_ops = len(all_lat) * 3  # create + get + delete per iteration
        print(f"Concurrent benchmark — workers={N_WORKERS}, per-worker={N_PER_WORKER}, ops={total_ops}")
        print(f"elapsed: {elapsed:.2f}s")
        print(f"aggregate throughput: {total_ops / elapsed:,.0f} ops/s")
        print(f"latency per full cycle (create+get+delete): p50={_percentile(all_lat, 50):.2f}ms p95={_percentile(all_lat, 95):.2f}ms p99={_percentile(all_lat, 99):.2f}ms")
        print(f"per-request latency: p50={_percentile(all_lat, 50) / 3:.2f}ms p95={_percentile(all_lat, 95) / 3:.2f}ms")
    finally:
        stop_server(proc)


if __name__ == "__main__":
    main()