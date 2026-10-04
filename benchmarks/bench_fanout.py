"""Notification fan-out benchmark — CloudEvents delivery to N subscribers (paper §6.2, P4).

Starts the PRAGMA app in-process (uvicorn in a daemon thread) AND a local consumer
HTTP server, registers N subscriptions through the real HTTP API, then measures
end-to-end delivery of one report to N subscribers. Delivery to the consumer
callback is over real HTTP (the consumer is a separate socket server), so the
fan-out measurement reflects the actual network path.

Run:  uv run python benchmarks/bench_fanout.py
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
import uvicorn
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from pragma.api import create_app
from pragma.schemas import FulfilmentInfo, IntentReport

API_PORT = int(os.environ.get("PRAGMA_BENCH_PORT", "8007"))
CONSUMER_PORT = int(os.environ.get("PRAGMA_CONSUMER_PORT", "8009"))
BASE = f"http://127.0.0.1:{API_PORT}"
CONSUMER = f"http://127.0.0.1:{CONSUMER_PORT}/notify"

# ── consumer server (real HTTP receiver) ─────────────────────────────────────

_received: dict[str, int] = {"count": 0}
_received_lock = threading.Lock()


class Consumer(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        with _received_lock:
            _received["count"] += 1
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_consumer() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", CONSUMER_PORT), Consumer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ── in-process PRAGMA server (shares one app/store with the trigger) ─────────

_server: uvicorn.Server | None = None


def start_pragma() -> tuple[uvicorn.Server, object]:
    global _server
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="error")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            with httpx.Client() as client:
                client.get(f"{BASE}/nfvintent/v1/intents")
            return server, app
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("server did not become ready")


def _intent_payload(i: int) -> dict:
    return {
        "intent": {
            "intentId": f"fan-{i}",
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
    consumer = start_consumer()
    server, app = start_pragma()
    try:
        client = httpx.Client(base_url=BASE, timeout=5.0)
        r = client.post("/nfvintent/v1/intents", json=_intent_payload(0))
        intent_id = r.json()["intentObjectInstanceId"]
        pragma = app.state.pragma

        print(f"Fan-out benchmark — consumer at {CONSUMER}, delivery over real HTTP")
        print(f"{'subscribers':<12} {'p50 (ms)':>10} {'p95 (ms)':>10} {'delivered':>10}")

        for n in (1, 10, 100):
            # fresh intent per round so the subscriber registry holds exactly n
            r = client.post("/nfvintent/v1/intents", json=_intent_payload(n))
            intent_id = r.json()["intentObjectInstanceId"]
            for _ in range(n):
                resp = client.post(
                    f"/nfvintent/v1/intents/{intent_id}/subscriptions",
                    params={"callback_url": CONSUMER},
                )
                assert resp.status_code == 201, resp.text

            before = _received["count"]
            lat = []
            for i in range(5):
                report = IntentReport(
                    intentReportId=f"rep-{i}",
                    intentReference=intent_id,
                    fulfillmentInfos=[
                        FulfilmentInfo(fulfilmentObjectId="self", fulfilmentStatus="FULFILLED")
                    ],
                    reportTime="2026-09-23T00:00:00Z",
                )
                t0 = time.perf_counter()
                asyncio.run(pragma.notifications.notify_report(intent_id, report))
                lat.append((time.perf_counter() - t0) * 1000)
            delivered = _received["count"] - before

            lat_sorted = sorted(lat)
            p50 = lat_sorted[len(lat_sorted) // 2]
            p95 = lat_sorted[min(int(len(lat_sorted) * 0.95) - 1, len(lat_sorted) - 1)]
            print(f"{n:<12} {p50:>10.2f} {p95:>10.2f} {delivered:>10}")
    finally:
        client.close()
        server.should_exit = True
        consumer.shutdown()


if __name__ == "__main__":
    main()