# PRAGMA — Using the Supporting Artifacts

Practical guide for reproducing the evaluation and using the reference
implementation, written 2026-09-23 against the TNSM V2 submission.

## 1. Artifact Map

| Path | What it is | Use it for |
|---|---|---|
| `IMPLEMENTATION_SPEC.md` | Normative spec: data model, state machine, endpoints, status codes | Any implementation must match this; it is the contract |
| `src/pragma/schemas.py` | Pydantic models mirroring IFA 050 + SOL 021 REST types | Wire-format ground truth (verbatim enum spellings) |
| `src/pragma/state_machine.py` | Pure transition function δ + DOT exporter | §4 formal model; drives all update validity |
| `src/pragma/store.py` | In-memory store, ETag/revision | Reference storage layer |
| `src/pragma/negotiation.py` | Sync/async negotiation + capability oracle | §3 Extension 1 |
| `src/pragma/subscriptions.py` | Subscription registry + CloudEvents notifier | §3 Extension 2 |
| `src/pragma/routing.py` | Cross-domain DomainRouter | §3 Extension 3 |
| `src/pragma/api.py` | FastAPI app (routes, error handler, OpenAPI) | Entry point; OpenAPI at `/openapi.json` |
| `tests/` | 95-test conformance suite (5 suites) | Validate any SOL 021/PRAGMA implementation |
| `benchmarks/` | CRUD + negotiation microbenchmarks | Reproduce the paper §6.2 tables |
| `fig_state_machine.pdf/png` | Lifecycle diagram (generated from the code) | Paper Fig. 1 |
| `fig_architecture.pdf/png` | Implementation architecture | Paper Fig. 2 |

## 2. Prerequisites

- Python 3.11+ (verified on 3.12.13)
- `uv` (fast venv/deps) — or plain `python3 -m venv`
- macOS/Linux (Apple Silicon and Intel verified for benchmarks)

## 3. Setup

```bash
cd reference-implementation
uv venv --python /Users/sumit.mitra/.local/bin/python3.12   # or: uv venv
uv pip install -e ".[dev]"
```

On a machine without uv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 4. Run the server

```bash
uv run python -m pragma        # uvicorn on 0.0.0.0:8000
```

OpenAPI schema (live):

```bash
curl -s http://localhost:8000/openapi.json | python3 -m json.tool | head -40
```

## 5. Smoke test the SOL 021 core (curl)

All commands below use a shell variable `INTENT_ID` captured from the create
response. This is deliberate: zsh treats a literal `<id>` in a URL as input
redirection ("read from a file named `id`"), which breaks curl. Always use the
variable pattern.

Create an intent and capture its ID:

```bash
INTENT_ID=$(curl -s -X POST http://localhost:8000/nfvintent/v1/intents \
  -H 'Content-Type: application/json' \
  -d '{"intent":{"intentId":"i-1","intentName":"latency-budget","intentExpectations":[{"expectationId":"e1","expectationName":"latency_ms < 5","expectationType":"Property","expectationTargets":[{"expectationTargetId":"svc-1"}]}]}}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['intentObjectInstanceId'])")
echo "intent_id = $INTENT_ID"
```

Expect `201 Created` with a `Location` header and the created `Intent`; the
command above extracts the `intentObjectInstanceId` into `$INTENT_ID`.

Query the list:

```bash
curl -s http://localhost:8000/nfvintent/v1/intents | python3 -m json.tool
```

Query one instance:

```bash
curl -s "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID" | python3 -m json.tool
```

Advance the lifecycle to COMPLIANT (valid transition ACKNOWLEDGED → COMPLIANT):

```bash
curl -i -X PATCH "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID" \
  -H 'Content-Type: application/json' \
  -H 'If-Match: "1"' \
  -d '{"fulfilmentInfos":[{"fulfilmentObjectId":"self","fulfilmentStatus":"NOT_FULFILLED","notFulfilledState":"COMPLIANT"}]}'
```

To FULFILLED (COMPLIANT → FULFILLED):

```bash
curl -i -X PATCH "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID" \
  -H 'Content-Type: application/json' \
  -H 'If-Match: "2"' \
  -d '{"fulfilmentInfos":[{"fulfilmentObjectId":"self","fulfilmentStatus":"FULFILLED"}]}'
```

Invalid transition (ACKNOWLEDGED → FULFILLED directly) → 422:

```bash
curl -i -X PATCH "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID" \
  -H 'Content-Type: application/json' \
  -H 'If-Match: "1"' \
  -d '{"fulfilmentInfos":[{"fulfilmentObjectId":"self","fulfilmentStatus":"FULFILLED"}]}'
```

405 on unsupported method:

```bash
curl -i -X PUT "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID"
```

## 6. Exercise the PRAGMA extensions

**Negotiation is pre-creation** (IFA 050 7.8): it operates on an `IntentObject`
declaration, NOT on an existing instance. Submit the intent object you intend to
create; the handler returns the confirmed object (`intentObjectOut`), and you then
create an instance from the agreed object.

Sync negotiation:

```bash
NEG=$(curl -s -X POST "http://localhost:8000/nfvintent/v1/negotiations" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"sync","intentObject":{"intentId":"i-1","intentName":"latency-budget","intentExpectations":[{"expectationId":"e1","expectationName":"latency_ms < 5","expectationType":"Property","expectationTargets":[{"expectationTargetId":"svc-1"}]}]}}')
echo "$NEG" | python3 -m json.tool
```

The result carries `outcome` (FEASIBLE/PARTIAL/INFEASIBLE), `proposedModifications`
(relaxation reasons), and the confirmed `intentObjectOut` with proposed values applied.
Create the instance from the agreed object:

```bash
curl -s -X POST "http://localhost:8000/nfvintent/v1/intents" \
  -H 'Content-Type: application/json' \
  -d "$(echo "$NEG" | python3 -c "import json,sys; print(json.dumps({'intent': json.load(sys.stdin)['intentObjectOut']}))")"
```

Async negotiation (202 + Location) and poll — capture the negotiation ID:

```bash
NEG_ID=$(curl -s -X POST "http://localhost:8000/nfvintent/v1/negotiations" \
  -H 'Content-Type: application/json' \
  -d '{"mode":"async","intentObject":{"intentId":"i-1","intentName":"latency-budget","intentExpectations":[{"expectationId":"e1","expectationName":"latency_ms < 5","expectationType":"Property","expectationTargets":[{"expectationTargetId":"svc-1"}]}]}}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['negotiationId'])")
curl -s "http://localhost:8000/nfvintent/v1/negotiations/$NEG_ID" | python3 -m json.tool
```

Subscribe and list:

```bash
curl -i -X POST "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID/subscriptions?callback_url=http://consumer.example/notify"
curl -s "http://localhost:8000/nfvintent/v1/intents/$INTENT_ID/subscriptions" | python3 -m json.tool
```

## 7. Run the conformance suite

```bash
uv run pytest -q
```

Expected: `95 passed`. The suite is behaviour-based (status codes, body
shapes, headers), so it validates ANY SOL 021/PRAGMA implementation, not just
this one.

## 8. Reproduce the paper's benchmark numbers

```bash
uv run python benchmarks/bench_crud.py            # in-process microbench (paper Table III)
uv run python benchmarks/bench_negotiation.py     # negotiation microbench (paper Table IV)
uv run python benchmarks/bench_network.py         # real HTTP over TCP (paper Table V)
uv run python benchmarks/bench_concurrent.py      # 16-worker parallel load (paper §6.2)
uv run python benchmarks/bench_fanout.py          # CloudEvents fan-out 1/10/100 subs (Table VI)
uv run python benchmarks/interop_smoke.py         # independent OpenAPI-generated client (needs live server on :8000)
```

These produce the tables in the paper §6.2 (p50/p95 in ms, requests/s). The
network/concurrent/fan-out benchmarks run a real uvicorn server; the in-process
ones use ASGI TestClient as bounds on the prototype, not production claims.

## 9. Regenerate the paper figures from the code

```bash
# State machine figure (rendered from state_machine.py's transition table)
uv run python -c "
from pragma.state_machine import StateMachine
print(StateMachine.to_dot())" > /tmp/state.dot
# (styling: add rankdir=LR, terminal double-circles — see pragma_tnsm_v2.tex fig)
dot -Tpdf /tmp/state.dot -o fig_state_machine.pdf

# Architecture figure: maintained as /tmp/arch_fig.dot content (see tex sources)
dot -Tpdf arch_fig.dot -o fig_architecture.pdf
```

## 10. Conformance target for other implementers

If you are implementing a SOL 021 or PRAGMA-compatible server, the acceptance
bar is: **all 95 tests green against your server**. Point the suite at any
deployment by swapping the TestClient app factory for a live client
(`httpx` against your URL); the assertions are purely HTTP-level.

## 11. Common issues

- **`zsh: no such file or directory: id` in curl URLs** → you typed a literal
  `ioi-<id>` (or `neg-<id>`) in a URL. zsh treats `<id>` as input redirection
  (read from a file named `id`) and breaks the command. Use the `$INTENT_ID` /
  `$NEG_ID` variable pattern from sections 5--6, or quote the URL. Never put a
  literal `<...>` placeholder in a curl URL under zsh.
- **Port busy** → change `PORT` in `src/pragma/api.py` (`uvicorn.run`), or run
  with `uv run python -c "import uvicorn; uvicorn.run('pragma.api:app', port=8001)"`.
- **Python 3.9 system python** → do NOT use it; the package needs 3.11+ (the
  default macOS python3 is 3.9). Use the uv-managed 3.12.
- **`python -m pragma` fails** → ensure the venv has the package installed
  (`uv pip install -e ".[dev]"`); the entry point lives in
  `src/pragma/__main__.py`.
- **Benchmark assertion failures** → the ETag/If-Match and state-transition
  constraints are strict by design; the benchmark creates a fresh intent per
  iteration (see `bench_crud.py`), so do not reuse intents across iterations.