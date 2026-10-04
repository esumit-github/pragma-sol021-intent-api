"""PRAGMA extension tests — negotiation, subscriptions, notifications (IFA 050 7.6-7.9)."""

from __future__ import annotations

import httpx

from tests.conftest import default_create_payload


def _negotiation_body(mode: str = "sync", expectation_name: str = "latency_ms < 5"):
    payload = default_create_payload()
    payload["intent"]["intentExpectations"][0]["expectationName"] = expectation_name
    return {"mode": mode, "intentObject": payload["intent"]}


def test_negotiate_sync_feasible(client):
    # pre-creation: negotiation operates on an IntentObject, not an existing instance
    resp = client.post("/nfvintent/v1/negotiations", json=_negotiation_body())
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "COMPLETED"
    assert body["outcome"] in ("FEASIBLE", "PARTIAL", "INFEASIBLE")
    assert body["negotiationId"]
    assert body["intentObjectOut"] is not None


def test_negotiate_async_202_and_poll(client):
    resp = client.post("/nfvintent/v1/negotiations", json=_negotiation_body(mode="async"))
    assert resp.status_code == 202
    assert "Location" in resp.headers
    assert resp.headers["Location"].endswith(f"/negotiations/{resp.json()['negotiationId']}")
    body = resp.json()
    assert body["state"] == "PENDING"
    neg_id = body["negotiationId"]
    poll = client.get(f"/nfvintent/v1/negotiations/{neg_id}")
    assert poll.status_code == 200
    assert poll.json()["negotiationId"] == neg_id


def test_negotiate_infeasible_returns_modifications(client):
    resp = client.post(
        "/nfvintent/v1/negotiations",
        json=_negotiation_body(expectation_name="latency_ms < 1"),
    )
    assert resp.status_code == 200
    body = resp.json()
    # latency < 1 with capability 10 is a partial relaxation case
    assert body["outcome"] == "PARTIAL"
    assert body["proposedModifications"]
    assert body["proposedModifications"][0]["expectationId"] == "exp-01"
    # the confirmed object carries the relaxed value: "latency_ms < 10.0"
    assert "10.0" in body["intentObjectOut"]["intentExpectations"][0]["expectationName"]


def test_negotiate_no_intent_object_422(client):
    resp = client.post("/nfvintent/v1/negotiations", json={"mode": "sync"})
    assert resp.status_code == 422


def test_subscribe_201_and_list(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.post(
        f"/nfvintent/v1/intents/{intent_id}/subscriptions",
        params={"callback_url": "http://consumer.example/notify"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["subscriptionId"]
    assert body["intentObjectInstanceId"] == intent_id

    listing = client.get(f"/nfvintent/v1/intents/{intent_id}/subscriptions")
    assert listing.status_code == 200
    assert len(listing.json()) == 1


def test_unsubscribe_204_and_404_after(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    sub = client.post(
        f"/nfvintent/v1/intents/{intent_id}/subscriptions",
        params={"callback_url": "http://consumer.example/notify"},
    ).json()
    resp = client.delete(f"/nfvintent/v1/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204
    resp = client.delete(f"/nfvintent/v1/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 404


def test_notify_callback_endpoint_204(client):
    resp = client.post(
        "/nfvintent/v1/notifications",
        json={"specversion": "1.0", "id": "e1", "source": "pragma://x", "type": "org.pragma.intent.report"},
    )
    assert resp.status_code == 204


def test_notification_delivery_with_mock_transport(client):
    """Deliver a report to a subscriber via an injected mock transport (no network)."""
    pragma = client.app.state.pragma

    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]

    # register subscription on the SAME app instance that owns the store
    sub = client.post(
        f"/nfvintent/v1/intents/{intent_id}/subscriptions",
        params={"callback_url": "http://consumer.example/notify"},
    ).json()
    _ = sub

    received = {}

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            received["url"] = str(request.url)
            received["body"] = request.content
            return httpx.Response(204)

    pragma.notifications.set_transport(httpx.AsyncClient(transport=MockTransport()))

    import asyncio

    from pragma.schemas import FulfilmentInfo, IntentReport

    report = IntentReport(
        intentReportId="rep-1",
        intentReference=intent_id,
        fulfillmentInfos=[
            FulfilmentInfo(fulfilmentObjectId="self", fulfilmentStatus="NOT_FULFILLED", notFulfilledState="COMPLIANT")
        ],
        reportTime="2026-09-23T00:00:00Z",
    )
    asyncio.run(pragma.notifications.notify_report(intent_id, report))

    assert received.get("url") == "http://consumer.example/notify"
    assert b"org.pragma.intent.report" in received["body"]


def test_notification_failure_counts_retries(client):
    """Failed delivery records MAX_RETRIES on the retry counter."""
    pragma = client.app.state.pragma

    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    client.post(
        f"/nfvintent/v1/intents/{intent_id}/subscriptions",
        params={"callback_url": "http://consumer.example/notify"},
    )

    class FailingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(500)

    pragma.notifications.set_transport(httpx.AsyncClient(transport=FailingTransport()))

    import asyncio

    from pragma.schemas import FulfilmentInfo, IntentReport

    report = IntentReport(
        intentReportId="rep-2",
        intentReference=intent_id,
        fulfillmentInfos=[
            FulfilmentInfo(fulfilmentObjectId="self", fulfilmentStatus="FULFILLED")
        ],
        reportTime="2026-09-23T00:00:00Z",
    )
    asyncio.run(pragma.notifications.notify_report(intent_id, report))

    subs = pragma.store.list_subscriptions(intent_id)
    assert subs
    assert pragma.notifications.retry_counts[subs[0].subscriptionId] == pragma.notifications.MAX_RETRIES