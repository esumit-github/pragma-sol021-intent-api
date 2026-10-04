"""Negotiation benchmark — sync + async negotiation latency/throughput (paper §6.2).

Pre-creation semantics (IFA 050 7.8): negotiation operates on an IntentObject,
not on an existing instance. Run:  uv run python benchmarks/bench_negotiation.py
"""

from __future__ import annotations

import statistics
import time

from fastapi.testclient import TestClient

from pragma.api import create_app

N_WARMUP = 20
N_MEASURE = 300


def _payload(intent_id: str) -> dict:
    return {
        "intent": {
            "intentId": intent_id,
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


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    idx = min(int(len(ordered) * pct / 100), len(ordered) - 1)
    return ordered[idx]


def main() -> None:
    client = TestClient(create_app())

    def op_sync(i: int):
        r = client.post(
            "/nfvintent/v1/negotiations",
            json={"mode": "sync", "intentObject": _payload(f"bench-{i}")["intent"]},
        )
        assert r.status_code == 200

    def op_async(i: int) -> str:
        r = client.post(
            "/nfvintent/v1/negotiations",
            json={"mode": "async", "intentObject": _payload(f"bench-{i}")["intent"]},
        )
        assert r.status_code == 202
        return r.json()["negotiationId"]

    def op_poll(neg_id: str):
        r = client.get(f"/nfvintent/v1/negotiations/{neg_id}")
        assert r.status_code == 200

    print(f"Negotiation benchmark — warmup={N_WARMUP}, measure={N_MEASURE}")
    print(f"{'operation':<28} {'p50 (ms)':>10} {'p95 (ms)':>10} {'req/s':>10}")

    # sync
    lat = []
    for i in range(N_WARMUP):
        op_sync(i)
    for i in range(N_MEASURE):
        t0 = time.perf_counter()
        op_sync(i)
        lat.append((time.perf_counter() - t0) * 1000)
    print(f"{'POST negotiate (sync)':<28} {_percentile(lat, 50):>10.2f} {_percentile(lat, 95):>10.2f} {1000.0/statistics.mean(lat):>10.1f}")

    # async create + poll
    lat = []
    for i in range(N_WARMUP):
        nid = op_async(i)
        op_poll(nid)
    for i in range(N_MEASURE):
        t0 = time.perf_counter()
        nid = op_async(i)
        op_poll(nid)
        lat.append((time.perf_counter() - t0) * 1000)
    print(f"{'POST negotiate (async)+poll':<28} {_percentile(lat, 50):>10.2f} {_percentile(lat, 95):>10.2f} {1000.0/statistics.mean(lat):>10.1f}")


if __name__ == "__main__":
    main()