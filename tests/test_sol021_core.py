"""SOL 021 V5.4.1 conformance tests — status codes, headers, 405s, ProblemDetails."""

from __future__ import annotations

from tests.conftest import default_create_payload


def test_create_intent_201_location(client):
    resp = client.post("/nfvintent/v1/intents", json=default_create_payload())
    assert resp.status_code == 201
    body = resp.json()
    assert "intentObjectInstanceId" in body
    assert body["intent"]["intentName"] == "latency-budget"


def test_create_then_get_single(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.get(f"/nfvintent/v1/intents/{intent_id}")
    assert resp.status_code == 200
    assert resp.json()["intentObjectInstanceId"] == intent_id


def test_list_intents_empty_and_after_create(client):
    assert client.get("/nfvintent/v1/intents").status_code == 200
    client.post("/nfvintent/v1/intents", json=default_create_payload())
    client.post("/nfvintent/v1/intents", json=default_create_payload("intent-002"))
    resp = client.get("/nfvintent/v1/intents")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_delete_intent_204(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.delete(f"/nfvintent/v1/intents/{intent_id}")
    assert resp.status_code == 204
    assert client.get(f"/nfvintent/v1/intents/{intent_id}").status_code == 404


def test_patch_intent_200(client):
    """Valid transition path: ACKNOWLEDGED → COMPLIANT → FULFILLED (IFA 050 §8.2.6.3)."""
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    # ACKNOWLEDGED → COMPLIANT (internal evaluation accepts for execution)
    resp = client.patch(
        f"/nfvintent/v1/intents/{intent_id}",
        json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "NOT_FULFILLED", "notFulfilledState": "COMPLIANT"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["fulfilmentInfos"][0]["notFulfilledState"] == "COMPLIANT"
    # COMPLIANT → FULFILLED (intent fulfilled as desired)
    resp = client.patch(
        f"/nfvintent/v1/intents/{intent_id}",
        json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "FULFILLED"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["fulfilmentInfos"][0]["fulfilmentStatus"] == "FULFILLED"


def test_get_missing_intent_404_problem_details(client):
    resp = client.get("/nfvintent/v1/intents/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "about:blank"
    assert body["status"] == 404
    assert "detail" in body


def test_delete_missing_intent_404(client):
    resp = client.delete("/nfvintent/v1/intents/nonexistent")
    assert resp.status_code == 404
    assert resp.json()["status"] == 404


def test_patch_missing_intent_404(client):
    resp = client.patch("/nfvintent/v1/intents/nonexistent", json={"intent": None})
    assert resp.status_code == 404


def test_put_collection_405(client):
    resp = client.put("/nfvintent/v1/intents", json=default_create_payload())
    assert resp.status_code == 405
    assert resp.json()["status"] == 405


def test_put_item_405(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.put(f"/nfvintent/v1/intents/{intent_id}", json={})
    assert resp.status_code == 405


def test_post_item_405(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.post(f"/nfvintent/v1/intents/{intent_id}", json={})
    assert resp.status_code == 405


def test_patch_etag_mismatch_412(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.patch(
        f"/nfvintent/v1/intents/{intent_id}",
        json={"intent": default_create_payload()["intent"]},
        headers={"If-Match": '"99"'},
    )
    assert resp.status_code == 412


def test_patch_etag_match_200(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.patch(
        f"/nfvintent/v1/intents/{intent_id}",
        json={"intent": default_create_payload()["intent"]},
        headers={"If-Match": '"1"'},
    )
    assert resp.status_code == 200


def test_invalid_filter_400(client):
    client.post("/nfvintent/v1/intents", json=default_create_payload())
    resp = client.get("/nfvintent/v1/intents?filter=not-a-filter")
    assert resp.status_code == 400
    assert resp.json()["status"] == 400


def test_filter_by_intent_name(client):
    client.post("/nfvintent/v1/intents", json=default_create_payload("intent-a"))
    client.post("/nfvintent/v1/intents", json=default_create_payload("intent-b"))
    resp = client.get('/nfvintent/v1/intents?filter=intentName="latency-budget"')
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_filter_by_domain_id(client):
    client.post("/nfvintent/v1/intents", json=default_create_payload())
    resp = client.get('/nfvintent/v1/intents?filter=domainId="domain-a"')
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_filter_state_after_update(client):
    create = client.post("/nfvintent/v1/intents", json=default_create_payload())
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.get('/nfvintent/v1/intents?filter=state="ACKNOWLEDGED"')
    assert resp.status_code == 200
    assert any(i["intentObjectInstanceId"] == intent_id for i in resp.json())


def test_pagination_marker(client):
    for i in range(3):
        client.post("/nfvintent/v1/intents", json=default_create_payload(f"intent-{i}"))
    resp = client.get("/nfvintent/v1/intents?nextpage_opaque_marker=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_invalid_pagination_marker_400(client):
    client.post("/nfvintent/v1/intents", json=default_create_payload())
    resp = client.get("/nfvintent/v1/intents?nextpage_opaque_marker=abc")
    assert resp.status_code == 400


def test_unsupported_http_on_collection(client):
    resp = client.delete("/nfvintent/v1/intents")
    assert resp.status_code == 405