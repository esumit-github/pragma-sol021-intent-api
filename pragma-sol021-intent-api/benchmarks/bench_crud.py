"""CRUD benchmark — POST/GET/PATCH/DELETE latency + throughput (paper §6.2).

Run:  uv run python benchmarks/bench_crud.py
Outputs p50/p95 latencies and requests/s for the SOL 021 conformant core.
"""

from __future__ import annotations

import statistics
import time

from fastapi.testclient import TestClient

from pragma.api import create_app

N_WARMUP = 20
N_MEASURE = 500


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


def _run(operation: str, fn, n: int = N_MEASURE) -> tuple[float, float, float]:
    latencies: list[float] = []
    for _ in range(N_WARMUP):
        fn()
    for _ in range(n):
        start = time.perf_counter()
        fn()
        latencies.append((time.perf_counter() - start) * 1000)  # ms
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    rps = 1000.0 / (sum(latencies) / len(latencies))
    return p50, p95, rps


def main() -> None:
    client = TestClient(create_app())

    print(f"CRUD benchmark — warmup={N_WARMUP}, measure={N_MEASURE}")
    print(f"{'operation':<14} {'p50 (ms)':>10} {'p95 (ms)':>10} {'req/s':>10}")

    created_ids: list[str] = []

    def op_create():
        r = client.post("/nfvintent/v1/intents", json=_payload(f"bench-{len(created_ids)}"))
        assert r.status_code == 201
        created_ids.append(r.json()["intentObjectInstanceId"])

    def op_list():
        r = client.get("/nfvintent/v1/intents")
        assert r.status_code == 200

    def op_get():
        r = client.get(f"/nfvintent/v1/intents/{created_ids[-1]}")
        assert r.status_code == 200

    def op_patch():
        # fresh intent each iteration (ETag/state must start at ACKNOWLEDGED/rev-1)
        r = client.post("/nfvintent/v1/intents", json=_payload(f"patch-{len(created_ids)}"))
        assert r.status_code == 201
        iid = r.json()["intentObjectInstanceId"]
        # valid transition path ACKNOWLEDGED → COMPLIANT → FULFILLED (IFA 050 §8.2.6.3)
        r = client.patch(
            f"/nfvintent/v1/intents/{iid}",
            json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "NOT_FULFILLED", "notFulfilledState": "COMPLIANT"}]},
            headers={"If-Match": '"1"'},
        )
        assert r.status_code == 200
        r = client.patch(
            f"/nfvintent/v1/intents/{iid}",
            json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "FULFILLED"}]},
            headers={"If-Match": '"2"'},
        )
        assert r.status_code == 200
        client.delete(f"/nfvintent/v1/intents/{iid}")

    def op_delete():
        iid = created_ids.pop()
        r = client.delete(f"/nfvintent/v1/intents/{iid}")
        assert r.status_code == 204

    for name, fn in [
        ("POST /intents", op_create),
        ("GET /intents", op_list),
        ("GET /intents/{id}", op_get),
        ("PATCH /intents/{id}", op_patch),
        ("DELETE /intents/{id}", op_delete),
    ]:
        p50, p95, rps = _run(name, fn)
        print(f"{name:<14} {p50:>10.2f} {p95:>10.2f} {rps:>10.1f}")


if __name__ == "__main__":
    main()