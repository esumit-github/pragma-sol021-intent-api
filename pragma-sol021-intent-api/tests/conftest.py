"""Shared test fixtures: valid intent payloads and a test client."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pragma.api import create_app
from pragma.schemas import (
    Condition,
    Context,
    ExpectationObject,
    ExpectationTarget,
    IntentExpectation,
    IntentObject,
)


def make_intent_object(
    intent_id: str = "intent-001",
    name: str = "latency-budget",
    metric: str = "latency_ms",
    target: str = "service-01",
) -> IntentObject:
    return IntentObject(
        intentId=intent_id,
        intentName=name,
        intentExpectations=[
            IntentExpectation(
                expectationId="exp-01",
                expectationName=f"{metric} < 5",
                expectationType="Property",
                expectationObject=ExpectationObject(objectId=target, objectType="service"),
                expectationTargets=[ExpectationTarget(expectationTargetId=target)],
                expectationContexts=[
                    Context(
                        contextId="ctx-01",
                        contextName="steady-state",
                        contextConditions=[
                            Condition(conditionId="c-01", operator="less than", conditionValue=5)
                        ],
                    )
                ],
            )
        ],
    )


def default_create_payload(intent_id: str = "intent-001") -> dict:
    return {"intent": make_intent_object(intent_id).model_dump(mode="json")}


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def created_intent(client):
    """Create one intent and return (client, intent_id)."""
    resp = client.post("/nfvintent/v1/intents", json=default_create_payload())
    assert resp.status_code == 201
    body = resp.json()
    return client, body["intentObjectInstanceId"]