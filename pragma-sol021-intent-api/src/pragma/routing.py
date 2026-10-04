"""Cross-domain intent propagation (PRAGMA extension).

A single management domain's intent scope terminates at its boundary. When an intent
spans administrative domains, expectations must carry routing provenance so that
forwarded intents preserve their origin and negotiation results stay portable.

Implemented via two extension fields on ExpectationTarget (see schemas.py):
  - domainId: the administrative domain an expectation targets (enables cross-domain routing)
  - intentOriginRef: reference to the originating intent when forwarded across domains
"""

from __future__ import annotations

from typing import Optional

from .schemas import Intent, IntentObject, IntentExpectation


class DomainRouter:
    """Assigns and preserves cross-domain routing metadata on intent expectations."""

    def __init__(self, local_domain: str = "domain-a") -> None:
        self._local_domain = local_domain

    @property
    def local_domain(self) -> str:
        return self._local_domain

    def stamp(self, intent: Intent, origin_ref: Optional[str] = None) -> Intent:
        """Stamp every expectation target with the local domain + optional origin.

        An expectation that already declares a *different* domainId is treated as a
        forwarded/foreign expectation and left untouched (it belongs to another domain).
        """
        for expectation in _expectations(intent.intent):
            for target in expectation.expectationTargets:
                if target.domainId is None:
                    target.domainId = self._local_domain
                if origin_ref is not None and target.intentOriginRef is None:
                    target.intentOriginRef = origin_ref
        return intent

    def forward(self, intent: Intent, to_domain: str) -> Intent:
        """Produce a forwarding view of *intent* bound to *to_domain*.

        The original intent id is preserved in intentOriginRef on each target so the
        receiving domain can trace the provenance chain; the forwarded intent carries
        the originating intent's id as intentId for correlation.
        """
        forwarded = intent.model_copy(deep=True)
        forwarded.intentObjectInstanceId = None  # the receiving domain assigns its own
        for expectation in _expectations(forwarded.intent):
            for target in expectation.expectationTargets:
                if target.intentOriginRef is None:
                    target.intentOriginRef = intent.intentObjectInstanceId
        return forwarded

    def provenance_chain(self, intent: Intent) -> list[str]:
        """Return the ordered provenance chain from this intent's expectation targets."""
        origins: list[str] = []
        for expectation in _expectations(intent.intent):
            for target in expectation.expectationTargets:
                if target.intentOriginRef and target.intentOriginRef not in origins:
                    origins.append(target.intentOriginRef)
        return origins


def _expectations(intent: IntentObject) -> list[IntentExpectation]:
    return intent.intentExpectations