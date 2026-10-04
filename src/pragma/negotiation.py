"""Negotiation service (PRAGMA extension — IFA 050 §7.8, deferred by SOL 021 V5.4.1).

Implements the bilateral feasibility exchange between Intent Owner and Intent Handler.
Per IFA 050 §7.8 the operation is strictly PRE-CREATION: the owner submits an
IntentObject (intentObjectIn) before any instance exists, the handler evaluates
feasibility and returns the confirmed IntentObject (intentObjectOut); the owner then
creates an instance from the agreed object. Support for sync (200) and async
(202 + poll) modes. No external solver: feasibility is decided by comparing declared
conditions against a deterministic capability model.
"""

from __future__ import annotations

import re
from typing import Optional

from .schemas import (
    IntentObject,
    NegotiationMode,
    NegotiationOutcome,
    NegotiationRequest,
    NegotiationResult,
    ProposedModification,
)
from .store import IntentStore, new_id


class CapabilityModel:
    """Deterministic feasibility oracle for the reference implementation.

    Maps each expectation to a capability bound; the negotiated value relaxes the
    original until it falls within the bound. This is intentionally simple — the
    paper's contribution is the negotiation protocol, not the solver.
    """

    # metric -> (capability bound, failure reason)
    CAPABILITIES: dict[str, tuple[float, str]] = {
        "latency_ms": (10.0, "Insufficient compute headroom on target cluster"),
        "availability_pct": (99.9, "Capacity cannot sustain the required availability"),
        "throughput_gbps": (50.0, "Backhaul port aggregation insufficient"),
    }

    def __init__(self, overrides: Optional[dict[str, tuple[float, str]]] = None) -> None:
        if overrides:
            self.capabilities = dict(self.CAPABILITIES)
            self.capabilities.update(overrides)
        else:
            self.capabilities = self.CAPABILITIES


class NegotiationService:
    def __init__(self, store: IntentStore, capabilities: Optional[CapabilityModel] = None) -> None:
        self._store = store
        self._capabilities = capabilities or CapabilityModel()

    def negotiate(self, request: NegotiationRequest) -> NegotiationResult:
        """Evaluate feasibility of the submitted IntentObject (pre-creation).

        In async mode the returned result is stored as PENDING and later resolved by
        resolve(). In sync mode it is returned COMPLETED directly. The result carries
        the confirmed intentObjectOut, which the owner uses to create an instance.
        """
        intent_object = request.intentObject
        negotiation_id = new_id("neg-")
        outcome, modifications = self._evaluate(intent_object)
        intent_object_out = self._apply_modifications(intent_object, modifications)

        if request.mode == NegotiationMode.ASYNC:
            result = NegotiationResult(
                negotiationId=negotiation_id,
                outcome=outcome,
                intentObjectOut=intent_object_out,
                proposedModifications=modifications,
                feasibility=outcome.value,
                expiresAt=request.deadlineUtc,
                state="PENDING",
            )
            self._store.save_negotiation(result)
            return result

        result = NegotiationResult(
            negotiationId=negotiation_id,
            outcome=outcome,
            intentObjectOut=intent_object_out,
            proposedModifications=modifications,
            feasibility=outcome.value,
            expiresAt=request.deadlineUtc,
            state="COMPLETED",
        )
        self._store.save_negotiation(result)
        return result

    def resolve(self, negotiation_id: str) -> Optional[NegotiationResult]:
        """Complete a pending negotiation (simulated handler decision)."""
        result = self._store.get_negotiation(negotiation_id)
        if result is None:
            return None
        result.state = "COMPLETED"
        self._store.save_negotiation(result)
        return result

    def _evaluate(
        self, intent_object: IntentObject
    ) -> tuple[NegotiationOutcome, list[ProposedModification]]:
        """Return (outcome, proposed modifications) for the given intent declaration."""
        modifications: list[ProposedModification] = []
        infeasible = 0
        partial = 0
        feasible = 0

        for expectation in intent_object.intentExpectations:
            # Extract the leading numeric metric from the expectation's context
            # conditions or a target name heuristic; default to latency_ms.
            metric = self._metric_of(expectation)
            cap, reason = self._capabilities.capabilities.get(metric, (None, "Unknown metric"))
            if cap is None:
                feasible += 1
                continue

            original_value = self._value_of(expectation, metric)
            if original_value is None:
                feasible += 1
                continue

            relaxed = self._relaxed_value(original_value, cap, metric)
            if relaxed == original_value:
                feasible += 1
            elif relaxed is not None:
                partial += 1
                modifications.append(
                    ProposedModification(
                        expectationId=expectation.expectationId,
                        metric=metric,
                        originalValue=str(original_value),
                        proposedValue=str(relaxed),
                        reason=reason,
                    )
                )
            else:
                infeasible += 1
                modifications.append(
                    ProposedModification(
                        expectationId=expectation.expectationId,
                        metric=metric,
                        originalValue=str(original_value),
                        proposedValue="<unachievable>",
                        reason=f"{reason}; no relaxation within capability",
                    )
                )

        if infeasible > 0:
            return NegotiationOutcome.INFEASIBLE, modifications
        if partial > 0:
            return NegotiationOutcome.PARTIAL, modifications
        return NegotiationOutcome.FEASIBLE, modifications

    @staticmethod
    def _apply_modifications(
        intent_object: IntentObject, modifications: list[ProposedModification]
    ) -> IntentObject:
        """Return the confirmed intentObjectOut with proposed values applied.

        For each proposed modification, the first numeric token in the expectation's
        name is rewritten to the proposed value, producing the object the owner
        would create an instance from.
        """
        if not modifications:
            return intent_object
        updated = intent_object.model_copy(deep=True)
        by_id = {m.expectationId: m for m in modifications}
        for expectation in updated.intentExpectations:
            mod = by_id.get(expectation.expectationId)
            if mod and mod.proposedValue and mod.proposedValue != "<unachievable>":
                name = expectation.expectationName or ""
                new_name = re.sub(
                    r"-?\d+(\.\d+)?", mod.proposedValue, name, count=1
                )
                expectation.expectationName = new_name
        return updated

    @staticmethod
    def _metric_of(expectation) -> str:
        target_names = [t.expectationTargetId for t in expectation.expectationTargets]
        joined = " ".join(target_names).lower()
        for metric in CapabilityModel.CAPABILITIES:
            if metric in joined:
                return metric
        for metric in CapabilityModel.CAPABILITIES:
            if metric in (expectation.expectationName or "").lower():
                return metric
        return "latency_ms"

    @staticmethod
    def _value_of(expectation, metric: str) -> Optional[float]:
        text = f"{expectation.expectationName or ''} {metric}".lower()
        for token in text.split():
            try:
                return float(token.strip("<>="))
            except ValueError:
                continue
        return None

    @staticmethod
    def _relaxed_value(original: float, cap: float, metric: str) -> Optional[float]:
        """Relax the original value toward the capability bound (deterministic).

        Capability bound semantics: the system guarantees latency <= cap (lower is
        better) and availability/throughput >= cap (higher is better). A request is
        feasible unchanged when it already sits within the capability; otherwise it
        is relaxed to the capability bound.
        """
        if metric in ("latency_ms",):
            return original if original >= cap else cap
        if metric in ("availability_pct", "throughput_gbps"):
            return original if original <= cap else cap
        return None