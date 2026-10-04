"""Deterministic intent lifecycle state machine (IFA 050 §8.2.6.3).

Reconciled against the normative Figure 8.2.6.3-1 (verified from the V5.2.1 PDF
on 2026-09-23): the transition set below is the figure's exact edge set, with
one explicit prose-grounded addition (owner-initiated DELETE from every
non-terminal state, per the TERMINATED state definition).

Figure transitions (verbatim labels):
    Start → ACKNOWLEDGED : Created
    ACKNOWLEDGED → COMPLIANT : Accepted
    ACKNOWLEDGED → FAILED : Not accepted
    COMPLIANT → FULFILLED : Satisfied
    COMPLIANT → DEGRADED : Not satisfied
    FULFILLED → DEGRADED : Not satisfied
    FULFILLED → TERMINATED : Deleted
    DEGRADED → FULFILLED : Satisfied
    DEGRADED → ACKNOWLEDGED : Updated
    DEGRADED → FAILED : Confirmed failed
    DEGRADED → TERMINATED : Deleted
    FAILED → TERMINATED : Deleted
    TERMINATED → End : Ended

Properties (paper §4):
  - Safety S1: every transition maps within S (the table is a partial function
    into S).
  - Safety S2: TERMINATED is the unique absorbing state (no outgoing edges).
    FAILED is not absorbing — it has exactly one exit, Deleted → TERMINATED.
  - Liveness L1: every non-terminal state has at least one outgoing transition
    (no deadlock in the active sub-machine).
  - Liveness L2: TERMINATED is reachable from every state in at most one
    transition (every non-terminal state has a Deleted/owner-delete edge).
  - Connectivity C1 (strong connectivity of the active sub-machine): the
    sub-machine over {ACKNOWLEDGED, COMPLIANT, FULFILLED, DEGRADED} is strongly
    connected — every active state is reachable from every other active state
    (via Accepted, Not satisfied, Satisfied, Updated), reflecting the ongoing
    negotiation/degradation/recovery loop.
"""

from __future__ import annotations

import enum
from typing import Optional

from .schemas import IntentState


class IntentEvent(str, enum.Enum):
    """Events driving the machine — event names follow Figure 8.2.6.3-1 labels."""

    CREATED = "CREATED"  # Start → ACKNOWLEDGED
    ACCEPTED = "ACCEPTED"  # ACKNOWLEDGED → COMPLIANT ("Accepted")
    NOT_ACCEPTED = "NOT_ACCEPTED"  # ACKNOWLEDGED → FAILED ("Not accepted")
    SATISFIED = "SATISFIED"  # COMPLIANT/DEGRADED → FULFILLED ("Satisfied")
    NOT_SATISFIED = "NOT_SATISFIED"  # COMPLIANT/FULFILLED → DEGRADED ("Not satisfied")
    UPDATED = "UPDATED"  # DEGRADED → ACKNOWLEDGED ("Updated")
    CONFIRMED_FAILED = "CONFIRMED_FAILED"  # DEGRADED → FAILED ("Confirmed failed")
    DELETED = "DELETED"  # FULFILLED/DEGRADED/FAILED → TERMINATED ("Deleted")
    OWNER_DELETE = "OWNER_DELETE"  # prose-grounded: owner delete → TERMINATED (SOL 021 DELETE)


# ── Allowed-transition table (Figure 8.2.6.3-1 + prose-grounded owner delete) ──
ALLOWED_TRANSITIONS: dict[IntentState, dict[IntentEvent, IntentState]] = {
    IntentState.ACKNOWLEDGED: {
        IntentEvent.ACCEPTED: IntentState.COMPLIANT,
        IntentEvent.NOT_ACCEPTED: IntentState.FAILED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    },
    IntentState.COMPLIANT: {
        IntentEvent.SATISFIED: IntentState.FULFILLED,
        IntentEvent.NOT_SATISFIED: IntentState.DEGRADED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    },
    IntentState.FULFILLED: {
        IntentEvent.NOT_SATISFIED: IntentState.DEGRADED,
        IntentEvent.DELETED: IntentState.TERMINATED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    },
    IntentState.DEGRADED: {
        IntentEvent.SATISFIED: IntentState.FULFILLED,
        IntentEvent.UPDATED: IntentState.ACKNOWLEDGED,
        IntentEvent.CONFIRMED_FAILED: IntentState.FAILED,
        IntentEvent.DELETED: IntentState.TERMINATED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    },
    IntentState.FAILED: {
        IntentEvent.DELETED: IntentState.TERMINATED,
        IntentEvent.OWNER_DELETE: IntentState.TERMINATED,
    },
    IntentState.TERMINATED: {},  # absorbing
}

# States of the "active" sub-machine (excludes TERMINATED, FAILED)
ACTIVE_STATES = (IntentState.ACKNOWLEDGED, IntentState.COMPLIANT, IntentState.FULFILLED, IntentState.DEGRADED)


# ── State machine ────────────────────────────────────────────────────────────

class StateMachine:
    """Pure transition engine over the IFA 050 lifecycle states."""

    @staticmethod
    def initial_state() -> IntentState:
        """Default state right after the request is acknowledged (IFA 050 §8.2.6.3)."""
        return IntentState.ACKNOWLEDGED

    @staticmethod
    def is_terminal(state: IntentState) -> bool:
        return state == IntentState.TERMINATED

    @staticmethod
    def transition(state: IntentState, event: IntentEvent) -> tuple[IntentState, bool]:
        """Apply *event* in *state*.

        Returns (new_state, ok). ok=False means the transition is not allowed
        (maps to HTTP 422 on the API layer).
        """
        table = ALLOWED_TRANSITIONS.get(state)
        if table is None:
            return state, False
        new_state = table.get(event)
        if new_state is None:
            return state, False
        return new_state, True

    @staticmethod
    def allowed_events(state: IntentState) -> list[IntentEvent]:
        return list(ALLOWED_TRANSITIONS.get(state, {}).keys())

    @staticmethod
    def to_fulfilment_encoding(state: IntentState) -> tuple[str, Optional[str]]:
        """Encode a lifecycle state into (fulfilmentStatus, notFulfilledState)."""
        if state == IntentState.FULFILLED:
            return "FULFILLED", None
        return "NOT_FULFILLED", state.value

    @staticmethod
    def from_fulfilment_encoding(
        fulfilment_status: str, not_fulfilled_state: Optional[str]
    ) -> IntentState:
        if fulfilment_status == "FULFILLED":
            return IntentState.FULFILLED
        return IntentState(not_fulfilled_state or "ACKNOWLEDGED")

    @staticmethod
    def to_dot() -> str:
        """Export the machine as DOT for the paper's state diagram figure.

        Figure-edge set (the 12 lifecycle transitions) is drawn solid; the
        prose-grounded owner-delete edges are drawn dashed so the reader can
        see exactly what is normative in the figure versus the TERMINATED prose.
        """
        fig_edges = {
            (IntentState.ACKNOWLEDGED, IntentEvent.ACCEPTED),
            (IntentState.ACKNOWLEDGED, IntentEvent.NOT_ACCEPTED),
            (IntentState.COMPLIANT, IntentEvent.SATISFIED),
            (IntentState.COMPLIANT, IntentEvent.NOT_SATISFIED),
            (IntentState.FULFILLED, IntentEvent.NOT_SATISFIED),
            (IntentState.FULFILLED, IntentEvent.DELETED),
            (IntentState.DEGRADED, IntentEvent.SATISFIED),
            (IntentState.DEGRADED, IntentEvent.UPDATED),
            (IntentState.DEGRADED, IntentEvent.CONFIRMED_FAILED),
            (IntentState.DEGRADED, IntentEvent.DELETED),
            (IntentState.FAILED, IntentEvent.DELETED),
        }
        lines = [
            "digraph intent_lifecycle {",
            "  rankdir=LR;",
            '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
            '  edge [fontname="Helvetica", fontsize=8];',
            '  "ACKNOWLEDGED" [fillcolor="#e6f2ff"];',
            '  "COMPLIANT"    [fillcolor="#e6f9e6"];',
            '  "FULFILLED"    [fillcolor="#ccf2cc"];',
            '  "DEGRADED"     [fillcolor="#fff3cc"];',
            '  "TERMINATED"   [fillcolor="#f2f2f2", shape=doublecircle];',
            '  "FAILED"       [fillcolor="#f9e5e5", shape=doublecircle];',
        ]
        for state, table in ALLOWED_TRANSITIONS.items():
            for event, target in table.items():
                style = 'style="dashed"' if event == IntentEvent.OWNER_DELETE else ""
                lines.append(
                    f'  "{state.value}" -> "{target.value}" [label="{event.value}"{(" " + style) if style else ""}];'
                )
        lines.append("}")
        return "\n".join(lines)