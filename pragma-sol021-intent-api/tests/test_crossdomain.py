"""Cross-domain propagation tests (PRAGMA extension)."""

from __future__ import annotations

from pragma.routing import DomainRouter
from pragma.schemas import ExpectationTarget, Intent, IntentObject, IntentExpectation, FulfilmentInfo
from tests.conftest import make_intent_object


def _make_intent(instance_id: str = "ioi-abc") -> Intent:
    return Intent(
        intentObjectInstanceId=instance_id,
        intent=make_intent_object(),
        fulfilmentInfos=[
            FulfilmentInfo(fulfilmentObjectId="self", fulfilmentStatus="NOT_FULFILLED", notFulfilledState="ACKNOWLEDGED")
        ],
    )


def test_stamp_assigns_local_domain():
    router = DomainRouter(local_domain="domain-a")
    intent = _make_intent()
    router.stamp(intent)
    for exp in intent.intent.intentExpectations:
        for target in exp.expectationTargets:
            assert target.domainId == "domain-a"


def test_forward_preserves_origin():
    router = DomainRouter(local_domain="domain-a")
    intent = _make_intent("ioi-origin-1")
    forwarded = router.forward(intent, to_domain="domain-b")
    # forwarded instance is unassigned (receiving domain assigns its own)
    assert forwarded.intentObjectInstanceId is None
    for exp in forwarded.intent.intentExpectations:
        for target in exp.expectationTargets:
            assert target.intentOriginRef == "ioi-origin-1"


def test_provenance_chain():
    router = DomainRouter(local_domain="domain-a")
    intent = _make_intent("ioi-origin-1")
    # simulate a two-hop chain: this intent was itself forwarded from an earlier origin
    for exp in intent.intent.intentExpectations:
        for target in exp.expectationTargets:
            target.intentOriginRef = "ioi-origin-0"
    chain = router.provenance_chain(intent)
    assert chain == ["ioi-origin-0"]


def test_foreign_expectation_target_left_untouched():
    """A target that already declares a foreign domainId is not re-stamped."""
    router = DomainRouter(local_domain="domain-a")
    intent = _make_intent()
    intent.intent.intentExpectations[0].expectationTargets[0].domainId = "domain-z"
    router.stamp(intent)
    assert intent.intent.intentExpectations[0].expectationTargets[0].domainId == "domain-z"


def test_api_created_intent_is_stamped(client):
    from tests.conftest import default_create_payload

    resp = client.post("/nfvintent/v1/intents", json=default_create_payload())
    assert resp.status_code == 201
    body = resp.json()
    targets = body["intent"]["intentExpectations"][0]["expectationTargets"]
    assert targets[0]["domainId"] == "domain-a"


def test_filter_by_foreign_domain(client):
    from tests.conftest import default_create_payload

    client.post("/nfvintent/v1/intents", json=default_create_payload())
    resp = client.get('/nfvintent/v1/intents?filter=domainId="domain-b"')
    assert resp.status_code == 200
    assert resp.json() == []