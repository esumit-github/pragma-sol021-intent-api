"""State machine tests — Figure 8.2.6.3-1 transition semantics + paper §4 properties.

The allowed-transition table is reconciled against the normative figure
(verified 2026-09-23 from IFA 050 V5.2.1): plus one prose-grounded
owner-delete edge from every non-terminal state.
"""

from __future__ import annotations

import pytest

from pragma.schemas import IntentState
from pragma.state_machine import ACTIVE_STATES, ALLOWED_TRANSITIONS, IntentEvent, StateMachine


# ── Allowed transitions (figure edge set + prose owner-delete) ───────────────

@pytest.mark.parametrize(
    "state,event,expected",
    [
        (IntentState.ACKNOWLEDGED, IntentEvent.ACCEPTED, IntentState.COMPLIANT),
        (IntentState.ACKNOWLEDGED, IntentEvent.NOT_ACCEPTED, IntentState.FAILED),
        (IntentState.ACKNOWLEDGED, IntentEvent.OWNER_DELETE, IntentState.TERMINATED),
        (IntentState.COMPLIANT, IntentEvent.SATISFIED, IntentState.FULFILLED),
        (IntentState.COMPLIANT, IntentEvent.NOT_SATISFIED, IntentState.DEGRADED),
        (IntentState.COMPLIANT, IntentEvent.OWNER_DELETE, IntentState.TERMINATED),
        (IntentState.FULFILLED, IntentEvent.NOT_SATISFIED, IntentState.DEGRADED),
        (IntentState.FULFILLED, IntentEvent.DELETED, IntentState.TERMINATED),
        (IntentState.FULFILLED, IntentEvent.OWNER_DELETE, IntentState.TERMINATED),
        (IntentState.DEGRADED, IntentEvent.SATISFIED, IntentState.FULFILLED),
        (IntentState.DEGRADED, IntentEvent.UPDATED, IntentState.ACKNOWLEDGED),
        (IntentState.DEGRADED, IntentEvent.CONFIRMED_FAILED, IntentState.FAILED),
        (IntentState.DEGRADED, IntentEvent.DELETED, IntentState.TERMINATED),
        (IntentState.DEGRADED, IntentEvent.OWNER_DELETE, IntentState.TERMINATED),
        (IntentState.FAILED, IntentEvent.DELETED, IntentState.TERMINATED),
        (IntentState.FAILED, IntentEvent.OWNER_DELETE, IntentState.TERMINATED),
    ],
)
def test_allowed_transitions(state, event, expected):
    new_state, ok = StateMachine.transition(state, event)
    assert ok is True
    assert new_state == expected


# ── Forbidden transitions → 422 at API layer ─────────────────────────────────

@pytest.mark.parametrize(
    "state,event",
    [
        # ACKNOWLEDGED: only ACCEPTED, NOT_ACCEPTED, OWNER_DELETE
        (IntentState.ACKNOWLEDGED, IntentEvent.SATISFIED),
        (IntentState.ACKNOWLEDGED, IntentEvent.NOT_SATISFIED),
        (IntentState.ACKNOWLEDGED, IntentEvent.UPDATED),
        (IntentState.ACKNOWLEDGED, IntentEvent.CONFIRMED_FAILED),
        (IntentState.ACKNOWLEDGED, IntentEvent.DELETED),
        # COMPLIANT: only SATISFIED, NOT_SATISFIED, OWNER_DELETE
        (IntentState.COMPLIANT, IntentEvent.ACCEPTED),
        (IntentState.COMPLIANT, IntentEvent.NOT_ACCEPTED),
        (IntentState.COMPLIANT, IntentEvent.UPDATED),
        (IntentState.COMPLIANT, IntentEvent.CONFIRMED_FAILED),
        (IntentState.COMPLIANT, IntentEvent.DELETED),
        # FULFILLED: only NOT_SATISFIED, DELETED, OWNER_DELETE
        (IntentState.FULFILLED, IntentEvent.ACCEPTED),
        (IntentState.FULFILLED, IntentEvent.NOT_ACCEPTED),
        (IntentState.FULFILLED, IntentEvent.SATISFIED),
        (IntentState.FULFILLED, IntentEvent.UPDATED),
        (IntentState.FULFILLED, IntentEvent.CONFIRMED_FAILED),
        # DEGRADED: only SATISFIED, UPDATED, CONFIRMED_FAILED, DELETED, OWNER_DELETE
        (IntentState.DEGRADED, IntentEvent.ACCEPTED),
        (IntentState.DEGRADED, IntentEvent.NOT_ACCEPTED),
        # FAILED: only DELETED, OWNER_DELETE
        (IntentState.FAILED, IntentEvent.ACCEPTED),
        (IntentState.FAILED, IntentEvent.NOT_ACCEPTED),
        (IntentState.FAILED, IntentEvent.SATISFIED),
        (IntentState.FAILED, IntentEvent.NOT_SATISFIED),
        (IntentState.FAILED, IntentEvent.UPDATED),
        (IntentState.FAILED, IntentEvent.CONFIRMED_FAILED),
        # TERMINATED: absorbing — everything forbidden
        (IntentState.TERMINATED, IntentEvent.ACCEPTED),
        (IntentState.TERMINATED, IntentEvent.NOT_ACCEPTED),
        (IntentState.TERMINATED, IntentEvent.SATISFIED),
        (IntentState.TERMINATED, IntentEvent.NOT_SATISFIED),
        (IntentState.TERMINATED, IntentEvent.UPDATED),
        (IntentState.TERMINATED, IntentEvent.CONFIRMED_FAILED),
        (IntentState.TERMINATED, IntentEvent.DELETED),
        (IntentState.TERMINATED, IntentEvent.OWNER_DELETE),
    ],
)
def test_forbidden_transitions(state, event):
    new_state, ok = StateMachine.transition(state, event)
    assert ok is False
    assert new_state == state  # no-op on invalid transition


# ── Paper §4 properties ──────────────────────────────────────────────────────

def test_safety_s1_all_transitions_are_well_defined():
    """S1: every (state, event) pair maps to a target in S or is rejected."""
    all_states = set(IntentState)
    for state, table in ALLOWED_TRANSITIONS.items():
        for target in table.values():
            assert target in all_states


def test_safety_s2_terminated_is_unique_absorbing():
    """S2: TERMINATED is the unique absorbing state (no outgoing edges)."""
    assert ALLOWED_TRANSITIONS[IntentState.TERMINATED] == {}
    # FAILED is NOT absorbing — it has exactly one exit (DELETED -> TERMINATED)
    assert ALLOWED_TRANSITIONS[IntentState.FAILED] == {
        IntentEvent.DELETED: IntentState.TERMINATED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    }


def test_liveness_l1_no_deadlock_in_active_states():
    """L1: every non-terminal state has at least one outgoing transition."""
    for state in ACTIVE_STATES + (IntentState.FAILED,):
        assert len(ALLOWED_TRANSITIONS[state]) >= 1


def test_liveness_l2_termination_always_reachable():
    """L2: every state reaches TERMINATED in at most one transition."""
    for state in set(IntentState) - {IntentState.TERMINATED}:
        assert StateMachine.transition(state, IntentEvent.OWNER_DELETE) == (IntentState.TERMINATED, True)
    for state in (IntentState.FULFILLED, IntentState.DEGRADED, IntentState.FAILED):
        assert StateMachine.transition(state, IntentEvent.DELETED) == (IntentState.TERMINATED, True)


def test_connectivity_c1_active_submachine_strongly_connected():
    """C1: the active sub-machine {ACKNOWLEDGED, COMPLIANT, FULFILLED, DEGRADED}
    is strongly connected — every active state reaches every other active state.
    This reflects the ongoing negotiation/degradation/recovery loop."""
    for source in ACTIVE_STATES:
        for target in ACTIVE_STATES:
            if source == target:
                continue
            assert _reachable(source, target), f"{source.value} cannot reach {target.value}"


def _reachable(source: IntentState, target: IntentState, _seen: set | None = None) -> bool:
    if source == target:
        return True
    seen = _seen or {source}
    for event in StateMachine.allowed_events(source):
        next_state, ok = StateMachine.transition(source, event)
        if ok and next_state not in seen and next_state in ACTIVE_STATES:
            if _reachable(next_state, target, seen | {next_state}):
                return True
    return False


def test_initial_state():
    assert StateMachine.initial_state() == IntentState.ACKNOWLEDGED


def test_encoding_roundtrip():
    for state in IntentState:
        status, not_state = StateMachine.to_fulfilment_encoding(state)
        back = StateMachine.from_fulfilment_encoding(status, not_state)
        assert back == state


def test_dot_export_contains_all_states():
    dot = StateMachine.to_dot()
    for state in IntentState:
        assert f'"{state.value}"' in dot
    assert dot.startswith("digraph")
    # owner-delete edges drawn dashed to distinguish prose-grounding from figure edges
    assert "dashed" in dot


def test_invalid_transition_via_api_422(client):
    """Invalid transition surfaces as 422 with a ProblemDetails body.

    ACKNOWLEDGED --SATISFIED--> FULFILLED is not in Figure 8.2.6.3-1
    (Satisfied is only valid from COMPLIANT and DEGRADED).
    """
    create = client.post(
        "/nfvintent/v1/intents",
        json={"intent": {
            "intentId": "i-1",
            "intentName": "n",
            "intentExpectations": [{
                "expectationId": "e1",
                "expectationName": "x",
                "expectationType": "Property",
                "expectationTargets": [{"expectationTargetId": "t1"}],
            }],
        }},
    )
    intent_id = create.json()["intentObjectInstanceId"]
    resp = client.patch(
        f"/nfvintent/v1/intents/{intent_id}",
        json={"fulfilmentInfos": [{"fulfilmentObjectId": "self", "fulfilmentStatus": "FULFILLED"}]},
    )
    assert resp.status_code == 422
    assert resp.json()["status"] == 422
    assert "not allowed" in resp.json()["detail"]