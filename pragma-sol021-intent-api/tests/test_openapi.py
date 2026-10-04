"""OpenAPI sanity tests — schema loads, documented paths exist, extensions present."""

from __future__ import annotations


def test_openapi_schema_loads(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["info"]["title"] == "PRAGMA — Intent Management API"
    assert schema["openapi"].startswith("3.")


def test_all_documented_paths_exist(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    expected = {
        "/nfvintent/v1/intents": {"post", "get"},
        "/nfvintent/v1/intents/{intentId}": {"get", "patch", "delete"},
        "/nfvintent/v1/negotiations": {"post"},
        "/nfvintent/v1/negotiations/{negotiationId}": {"get"},
        "/nfvintent/v1/intents/{intentId}/subscriptions": {"post", "get"},
        "/nfvintent/v1/subscriptions/{subscriptionId}": {"delete"},
        "/nfvintent/v1/notifications": {"post"},
    }
    for path, methods in expected.items():
        assert path in paths, f"missing path {path}"
        present = {m.lower() for m in paths[path]}
        assert methods.issubset(present), f"{path} missing {methods - present}"


def test_extension_schemas_documented(client):
    schema = client.get("/openapi.json").json()
    names = set(schema["components"]["schemas"])
    # ProblemDetails is returned via the exception handler, not a response model,
    # so it is not required in components; runtime shape is asserted elsewhere.
    assert {"NegotiationRequest", "NegotiationResult", "Subscription"}.issubset(names)
    assert "ProblemDetails" in names  # present when declared via response metadata


def test_enum_spellings_verbatim(client):
    schema = client.get("/openapi.json").json()
    schemas = schema["components"]["schemas"]
    # FulfilmentStatus enum carries the exact tokens from IFA 050 §8.2.6
    status_enum = schemas.get("FulfilmentStatus", {}).get("enum", [])
    assert "FULFILLED" in status_enum
    assert "NOT_FULFILLED" in status_enum
    # NotFulfilledState enum carries the six lifecycle state tokens
    state_enum = schemas.get("NotFulfilledState", {}).get("enum", [])
    for token in ("ACKNOWLEDGED", "COMPLIANT", "DEGRADED", "TERMINATED", "FAILED"):
        assert token in state_enum
    # ConditionOperator carries verbatim operator spellings (IFA 050 §8.2.3)
    op_enum = schemas.get("ConditionOperator", {}).get("enum", [])
    for token in ("greater than", "equal to", "less than", "one of", "all of", "maximum value", "minimum value", "median", "credibility"):
        assert token in op_enum