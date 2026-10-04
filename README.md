# PRAGMA Reference Implementation

Conformance-accurate implementation of **ETSI GS NFV-SOL 021 V5.4.1** (Intent Management
REST API) plus the **PRAGMA extensions**: the IFA 050 operations SOL 021 V5.4.1 defers
(Negotiate, Subscribe, Terminate Subscription, Notify) and cross-domain intent propagation.

Part of the IEEE TNSM V2 submission "Evaluation + Extension of ETSI GS NFV-SOL 021."

## Setup

```bash
uv venv
uv pip install -e ".[dev]"
```

## Run the server

```bash
uv run python -m pragma        # uvicorn on 0.0.0.0:8000
curl http://localhost:8000/openapi.json | head
curl -X POST http://localhost:8000/nfvintent/v1/intents \
     -H 'Content-Type: application/json' \
     -d '{"intent":{"intentId":"i-1","intentName":"latency-budget","intentExpectations":[{"expectationId":"e1","expectationName":"latency_ms < 5","expectationType":"Property","expectationTargets":[{"expectationTargetId":"svc-1"}]}]}}'
```

## Tests

```bash
uv run pytest -q
```

## Benchmarks (paper §6.2)

```bash
uv run python benchmarks/bench_crud.py
uv run python benchmarks/bench_negotiation.py
```

## API surface

### SOL 021 V5.4.1 conformant core

| Method | Path | Success |
|---|---|---|
| POST | `/nfvintent/v1/intents` | 201 + `Location` + `Intent` |
| GET | `/nfvintent/v1/intents` | 200 `[Intent]` |
| GET | `/nfvintent/v1/intents/{intentId}` | 200 `Intent` |
| PATCH | `/nfvintent/v1/intents/{intentId}` | 200 `Intent` (If-Match → 412) |
| DELETE | `/nfvintent/v1/intents/{intentId}` | 204 |

Errors: `ProblemDetails` (SOL 013 §6.4 / RFC 9457). 405 for unsupported methods.

### PRAGMA extensions (deferred IFA 050 ops)

| Method | Path | IFA 050 op |
|---|---|---|
| POST | `/negotiations` | Negotiate (7.8, PRE-creation) — sync 200 / async 202 |
| GET | `/negotiations/{negId}` | Poll negotiation outcome |
| POST | `/intents/{id}/subscriptions` | Subscribe (7.6) |
| GET | `/intents/{id}/subscriptions` | List subscriptions |
| DELETE | `/subscriptions/{subId}` | Terminate subscription (7.7) |
| POST | `/notifications` | Notify (7.9) — CloudEvents `org.pragma.intent.report` |

Cross-domain: `ExpectationTarget.domainId` + `intentOriginRef` (propagation chain).

## Lifecycle state machine

Six states per IFA 050 §8.2.6.3 — `ACKNOWLEDGED → COMPLIANT → FULFILLED/DEGRADED/FAILED`,
any active state → `TERMINATED`. Encoded via `FulfilmentInfo.fulfilmentStatus` +
`notFulfilledState`. Invalid transitions return 422. See `pragma/state_machine.py`
(safety/liveness properties in the module docstring — reproduced in paper §4).

## Reference

- ETSI GS NFV-IFA 050 V5.2.1 — Intent Management Service Interface and Information Model
- ETSI GS NFV-SOL 021 V5.4.1 — Protocols and data models for Intent Management
- ETSI GS NFV-SOL 013 V5.4.1 — Common aspects for RESTful NFV MANO APIs
- RFC 9457 — Problem Details for HTTP APIs