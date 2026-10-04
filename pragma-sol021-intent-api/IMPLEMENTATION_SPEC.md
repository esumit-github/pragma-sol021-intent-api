# PRAGMA V2 — Reference Implementation Specification

**Purpose:** Conformance-accurate reference implementation supporting the TNSM V2 paper
"Evaluation + Extension of ETSI GS NFV-SOL 021." Implements the SOL 021 V5.4.1 conformant
core (Create/Query/Update/Delete) plus the PRAGMA extensions: the four IFA 050 operations
SOL 021 V5.4.1 defers (Negotiate, Subscribe, Terminate Subscription, Notify) and
cross-domain intent propagation.

**Owner:** Sumit Kumar Mitra | **Spec date:** 2026-09-23 | **Status:** V1

---

## 0. Normative References (verified 2026-09-22)

| Ref | Spec | Version | Date | Role |
|---|---|---|---|---|
| [1] | ETSI GS NFV-IFA 050 *Intent Management Service Interface and Information Model* | V5.2.1 | 2024-11 | Information model + state machine (8.2.6.3) |
| [2] | ETSI GS NFV-SOL 021 *Protocols and data models for Intent Management* | V5.4.1 | 2026-09 | REST/OpenAPI binding (conformance core) |
| [3] | ETSI GS NFV-SOL 013 *Common aspects for RESTful NFV MANO APIs* | V5.4.1 | 2026-03 | REST conventions, ProblemDetails, error model |
| [4] | RFC 9457 *Problem Details for HTTP APIs* | — | 2023 | Replaces RFC 7807 |
| [5] | CNCF CloudEvents | 1.0.2 | 2022 | Notification transport (extension) |

---

## 1. Scope

### 1.1 Conformance core — SOL 021 V5.4.1 (exactly these five resources)

Base URI: `{apiRoot}/nfvintent/v1` (SOL 021 §5.3). In the reference implementation,
`{apiRoot}` = `http://localhost:8000`.

| Method | Path | Success | Semantics |
|---|---|---|---|
| POST | `/intents` | **201** + `Location` header + body `Intent` | Create intent object instance; body `CreateIntentRequest` |
| GET | `/intents` | **200** body `[Intent]` | Query list; query params `filter`, `all_fields`, `fields`, `exclude_fields`, `nextpage_opaque_marker` |
| GET | `/intents/{intentId}` | **200** body `Intent` | Query single instance |
| PATCH | `/intents/{intentId}` | **200** body `Intent` | Update; body `IntentModifications`; **412** on ETag mismatch |
| DELETE | `/intents/{intentId}` | **204** | Delete instance |
| (any) | `/intents` or `/intents/{intentId}` other methods | **405** | Unsupported method (PUT etc.) |

Error body everywhere: `ProblemDetails` (SOL 013 §6.4 / RFC 9457).

Required error cases (SOL 021):
- `400` — invalid attribute-based filter / invalid attribute selector / response too big
- `404` — intent not found
- `405` — method not allowed
- `412` — PATCH with stale ETag
- `422` — content well-formed but cannot be processed (e.g. invalid state transition)

### 1.2 PRAGMA extensions (the paper's contribution — NOT in SOL 021 V5.4.1)

These implement IFA 050 operations SOL 021 explicitly defers (§5.1), modeled after SOL
013/SOL 005 conventions, plus cross-domain propagation.

| Method | Path | Success | IFA 050 op |
|---|---|---|---|
| POST | `/negotiations` | **202** + `Location` (async) or **200** (sync) | Negotiate Intent Object (7.8) — PRE-CREATION |
| GET | `/negotiations/{negotiationId}` | **200** negotiation result | Poll negotiation outcome |
| POST | `/intents/{intentId}/subscriptions` | **201** + `Subscription` | Subscribe (7.6) |
| GET | `/intents/{intentId}/subscriptions` | **200** `[Subscription]` | List subscriptions |
| DELETE | `/subscriptions/{subscriptionId}` | **204** | Terminate subscription (7.7) |
| POST | `/notifications` (callback, consumer-hosted) | **204** | Notify (7.9) — `IntentReportNotification` in CloudEvents envelope |

**Negotiation is pre-creation** (per IFA 050 7.8 NOTE): the owner submits an
`IntentObject` (`intentObjectIn`) via `POST /negotiations`; the handler returns the
confirmed `IntentObject` (`intentObjectOut`) in the `NegotiationResult`. The owner then
creates an instance from the agreed object via `POST /intents`. Negotiation does NOT
operate on an existing `IntentObjectInstance` — it precedes creation.

Cross-domain extension fields on `ExpectationTarget`: `domainId`, `intentOriginRef`
(propagation chain preservation).

---

## 2. Data Model

### 2.1 IFA 050 information elements (the modeling layer)

- **IntentExpectation** (8.2.1): `expectationId`, `expectationName`, `expectationType`
  (Delivery/Ensure/Property/Report), `expectationObject`, `expectationTargets` (1..N),
  `expectationContexts` (0..N).
- **IntentObject** (8.2.2): `intentId`, `intentName`, `intentExpectations` (1..N),
  `intentContexts` (0..N).
- **Condition** (8.2.3): `conditionId`, `conditionName`, `operator` (enum: greater than /
  equal to / less than / not equal to / one of / some of / all of / maximum value /
  minimum value / median / credibility; or and/or when `conditionList`), `conditionValue`
  (0..1), `conditionList` (0..N) — exactly one of value/list.
- **Context** (8.2.4): `contextId`, `contextName`, `contextConditions` (1..N).
- **IntentObjectInstance** (8.2.5): `intentObjectInstanceId`, `intentId`, `fulfilmentInfos`
  (1..N), `intentReports` (0..N).
- **FulfilmentInfo** (8.2.6): `fulfilmentObjectId`, `fulfilmentStatus` (enum:
  `FULFILLED`, `NOT_FULFILLED`), `notFulfilledState` (0..1, enum: `ACKNOWLEDGED`,
  `COMPLIANT`, `DEGRADED`, `TERMINATED`, `FAILED`), `notFulfilledReasons` (0..1),
  `achievedValue` (0..1), `objectInstances` (0..N).
- **IntentReport** (8.2.7): `intentReportId`, `intentReference`, `fulfillmentInfos` (1..N),
  `reportTime`.
- **IntentReportNotification** (8.2.10): `intentReport`.

### 2.2 SOL 021 REST types

- **CreateIntentRequest** — POST body; the intent to create (an IntentObjectInstance-level
  description).
- **Intent** — the stage-3 serialization returned on POST/GET/PATCH.
- **IntentModifications** — PATCH body (partial update of the intent).
- **ProblemDetails** — error body (SOL 013 §6.4): `type`, `title`, `status`, `detail`,
  `instance`.

### 2.3 Lifecycle state machine (IFA 050 §8.2.6.3 — Figure 8.2.6.3-1 edge set)

States (exactly): `ACKNOWLEDGED`, `COMPLIANT`, `FULFILLED`, `DEGRADED`, `TERMINATED`,
`FAILED`.

Encoding: `fulfilmentStatus` + `notFulfilledState`.
- `FULFILLED` ⇒ `fulfilmentStatus = FULFILLED`.
- All other states ⇒ `fulfilmentStatus = NOT_FULFILLED` with `notFulfilledState` =
  the state token.

Allowed transitions (verified against Figure 8.2.6.3-1, 2026-09-23 — the
figure's exact edge set, plus prose-grounded owner-delete):

```
Start           → ACKNOWLEDGED  : Created
ACKNOWLEDGED    → COMPLIANT     : Accepted
ACKNOWLEDGED    → FAILED        : Not accepted
COMPLIANT       → FULFILLED     : Satisfied
COMPLIANT       → DEGRADED      : Not satisfied
FULFILLED       → DEGRADED      : Not satisfied
FULFILLED       → TERMINATED    : Deleted
DEGRADED        → FULFILLED     : Satisfied
DEGRADED        → ACKNOWLEDGED  : Updated
DEGRADED        → FAILED        : Confirmed failed
DEGRADED        → TERMINATED    : Deleted
FAILED          → TERMINATED    : Deleted
TERMINATED      → End           : Ended
-- prose-grounded (owner-initiated deletion, SOL 021 DELETE):
any active state → TERMINATED    : OWNER_DELETE
```

NOT in the figure (do not implement): COMPLIANT→FAILED, COMPLIANT→TERMINATED,
ACKNOWLEDGED→TERMINATED, FULFILLED→FAILED, COMPLIANT→ACKNOWLEDGED,
FULFILLED→ACKNOWLEDGED.

Event names in the code follow the figure labels: `ACCEPTED`, `NOT_ACCEPTED`,
`SATISFIED`, `NOT_SATISFIED`, `UPDATED`, `CONFIRMED_FAILED`, `DELETED`,
`OWNER_DELETE` (plus `CREATED` for Start→ACKNOWLEDGED).

Invalid transition ⇒ **422** (extensible `ProblemDetails.detail`).

Formal properties (paper §4): S1 well-defined transitions; S2 TERMINATED is the
unique absorbing state (FAILED is NOT absorbing — it exits via DELETED);
L1 no deadlock (every non-terminal state has ≥1 outgoing); L2 OWNER_DELETE
reaches TERMINATED from every state in one step; C1 the active sub-machine
{ACKNOWLEDGED, COMPLIANT, FULFILLED, DEGRADED} is strongly connected.

---

## 3. Project Structure

```
reference-implementation/
  pyproject.toml               # uv-managed; deps: fastapi, uvicorn, pydantic, pytest, httpx
  README.md                    # run instructions
  src/pragma/
    __init__.py
    schemas.py                 # Pydantic models mirroring §2 (IntObject, IntentExpectation,
                               #   Condition, Context, FulfilmentInfo, Intent, ProblemDetails…)
    state_machine.py           # deterministic transition engine; guards; dot export
    store.py                   # in-memory store (thread-safe dict + id generation + ETag)
    negotiation.py             # negotiation service (sync + async modes)
    subscriptions.py           # subscription registry + CloudEvents notifier
    routing.py                 # cross-domain: domainId / intentOriginRef propagation
    api.py                     # FastAPI app: routers, error handlers, OpenAPI meta
  tests/
    test_sol021_core.py        # §1.1 conformance: status codes, Location, 405, ProblemDetails
    test_state_machine.py      # §2.3 transitions: allowed set + invalid → 422
    test_extensions.py         # negotiate / subscribe / notify flows
    test_crossdomain.py        # domainId + intentOriginRef propagation
    test_openapi.py            # schema loads; every documented path exists; OpenAPI 3.1
  benchmarks/
    bench_crud.py              # POST/GET/PATCH/DELETE latency + throughput
    bench_negotiation.py       # negotiation under load, sync + async, fan-out of reports
  openapi/                     # generated on startup by FastAPI
```

---

## 4. Implementation Rules

1. **Conformance over convenience.** Status codes, headers, enum values, and field names
   exactly as §1/§2. Do not invent new states or rename IFA 050 attributes.
2. **State machine is a pure function.** `transition(state, event, context) -> (new_state, ok)`
   with an explicit allowed-transition table; the API layer maps HTTP actions to events.
3. **ETag/412 on PATCH** (SOL 013): store a revision; client sends `If-Match`; mismatch → 412.
4. **Negotiation:** sync mode returns the negotiated `IntentObject` (with
   `proposedModifications` where applicable) directly; async mode returns 202 + a
   negotiation resource that later resolves. Feasibility is deterministic (evaluable
   conditions) — no external solver needed for the reference implementation.
5. **Notifications:** emitted as CloudEvents `1.0` envelopes with `type =
   org.pragma.intent.report` to all subscriber callback URLs; delivery best-effort with
   in-process retry counter (recorded for evaluation).
6. **OpenAPI:** expose the spec at `/openapi.json`; document extensions alongside the
   conformant core. Note the SOL 021-forge OpenAPI version (expected 3.0.x per SOL 015
   conventions) — our served spec is 3.1 but conformance is measured against SOL 021
   semantics, not its YAML.
7. **No production deps beyond the core:** SQLite not required; in-memory store is
   sufficient and matches the benchmark design. Keep it dependency-light.

---

## 5. Verification

- `uv run pytest -q` — all green.
- Every §1.1/§1.2 row covered by at least one test (status + body shape).
- Benchmark scripts print p50/p95 and requests/s tables (used in paper §6).
- `uv run python -m pragma` starts the server; smoke-test via `curl`.

---

## 6. Paper Mapping (what each artifact feeds)

| Artifact | Paper section |
|---|---|
| `state_machine.py` + transition table | §4 formal model (with proofs of safety/liveness in text) |
| `test_sol021_core.py` (green) | §6.1 conformance evaluation |
| `bench_crud.py` / `bench_negotiation.py` | §6.2 performance evaluation |
| `test_extensions.py` + `test_crossdomain.py` | §6.3 extension validation |
| `openapi.json` | §3.2 published schema (annex link) |
| CloudEvents notifier behavior | §3.4 notification semantics |

---

*File: CV/groom/research_papers/WP01-PRAGMA/reference-implementation/IMPLEMENTATION_SPEC.md · V1 · 2026-09-23*