"""Real-network benchmark — PRAGMA over actual HTTP (paper §6.2, P4).

Starts the uvicorn server as a subprocess on a private port and measures the
SOL 021 core + negotiation extensions over real TCP via httpx — NOT the
in-process TestClient used in the earlier microbenchmarks. This answers the
reviewer objection that the in-process numbers do not describe the API over a
real network.

Run:  uv run python benchmarks/bench_network.py
"""

from __future__ import annotations

import os
import signal
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

PORT = int(os.environ.get("PRAGMA_BENCH_PORT", "8007"))
BASE = f"http://127.0.0.1:{PORT}"
N_WARMUP = 20
N_MEASURE = 300

_REPO = Path(__file__).resolve().parent.parent


def start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "pragma"],
        cwd=_REPO,
        env={**os.environ, "PORT": str(PORT)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # wait for readiness
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
            "intentId": f"bench-{i}",
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


def main() -> None:
    proc = start_server()
    try:
        client = httpx.Client(base_url=BASE, timeout=5.0)

        def op_create(i: int) -> str:
            r = client.post("/nfvintent/v1/intents", json=_intent_payload(i))
            assert r.status_code == 201, r.text
            return r.json()["intentObjectInstanceId"]

        def op_list():
            r = client.get("/nfvintent/v1/intents")
            assert r.status_code == 200

        def op_get(iid: str):
            r = client.get(f"/nfvintent/v1/intents/{iid}")
            assert r.status_code == 200

        def op_patch(i: int) -> str:
            # fresh intent each iteration (ETag/state must start at ACKNOWLEDGED/rev-1)
            iid = op_create(i)
            # valid transition path ACKNOWLEDGED → COMPLIANT → FULFILLED (Fig 8.2.6.3-1)
            r = client.patch(
                f"/nfvintent/v1/intents/{iid}",
                headers={"If-Match": '"1"'},
                json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "NOT_FULFILLED", "notFulfilledState": "COMPLIANT"}]},
            )
            assert r.status_code == 200, r.text
            r = client.patch(
                f"/nfvintent/v1/intents/{iid}",
                headers={"If-Match": '"2"'},
                json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "FULFILLED"}]},
            )
            assert r.status_code == 200, r.text
            return iid

        def op_delete(i: int) -> None:
            # fresh intent each iteration so DELETE always targets a live instance
            iid = op_create(i)
            r = client.delete(f"/nfvintent/v1/intents/{iid}")
            assert r.status_code == 204, r.text

        def op_negotiate(i: int) -> dict:
            r = client.post("/nfvintent/v1/negotiations", json={
                "mode": "sync",
                "intentObject": _intent_payload(i)["intent"],
            })
            assert r.status_code == 200, r.text
            return r.json()

        print(f"Network benchmark (real HTTP over TCP) — warmup={N_WARMUP}, measure={N_MEASURE}")
        print(f"{'operation':<28} {'p50 (ms)':>10} {'p95 (ms)':>10} {'req/s':>10}")

        # seed one intent for get/patch/delete
        seed_id = op_create(-1)

        def bench(label: str, fn, *args):
            lat = []
            for _ in range(N_WARMUP):
                fn(*args)
            for i in range(N_MEASURE):
                t0 = time.perf_counter()
                fn(*args)
                lat.append((time.perf_counter() - t0) * 1000)
            mean = statistics.mean(lat)
            print(f"{label:<28} {_percentile(lat, 50):>10.2f} {_percentile(lat, 95):>10.2f} {1000.0/mean:>10.1f}")

        bench("POST /intents (create)", op_create, 0)
        bench("GET /intents (list)", op_list)
        bench("GET /intents/{id}", op_get, seed_id)
        bench("PATCH /intents/{id} (2-step)", op_patch, 0)
        bench("DELETE /intents/{id}", op_delete, seed_id)
        bench("POST /negotiations (sync)", op_negotiate, 1)
    finally:
        client.close()
        stop_server(proc)


if __name__ == "__main__":
    main()