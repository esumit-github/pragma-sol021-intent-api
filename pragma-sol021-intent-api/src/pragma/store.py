"""In-memory thread-safe store for intent object instances, negotiations, and subscriptions.

Dependency-light by design (IMPLEMENTATION_SPEC.md §4.7): an in-memory store is sufficient
for the reference implementation and matches the benchmark design.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from .schemas import Intent, NegotiationResult, Subscription


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex


class IntentStore:
    """Thread-safe registry of intents, negotiations, and subscriptions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._intents: dict[str, Intent] = {}
        self._negotiations: dict[str, NegotiationResult] = {}
        self._subscriptions: dict[str, Subscription] = {}

    # ── Intents (SOL 021 core) ──────────────────────────────────────────────
    def create(self, intent: Intent) -> Intent:
        with self._lock:
            intent.intentObjectInstanceId = new_id("ioi-")
            intent.created = intent.created or _now()
            intent.revision = 1
            self._intents[intent.intentObjectInstanceId] = intent
            return intent

    def get(self, intent_id: str) -> Optional[Intent]:
        with self._lock:
            return self._intents.get(intent_id)

    def list(self) -> list[Intent]:
        with self._lock:
            return list(self._intents.values())

    def update(self, intent_id: str, updated: Intent) -> Optional[Intent]:
        with self._lock:
            if intent_id not in self._intents:
                return None
            existing = self._intents[intent_id]
            updated.intentObjectInstanceId = intent_id
            updated.created = existing.created
            updated.revision = existing.revision + 1
            self._intents[intent_id] = updated
            return updated

    def delete(self, intent_id: str) -> bool:
        with self._lock:
            if intent_id not in self._intents:
                return False
            del self._intents[intent_id]
            # cascade: drop subscriptions bound to this intent
            self._subscriptions = {
                k: v
                for k, v in self._subscriptions.items()
                if v.intentObjectInstanceId != intent_id
            }
            return True

    # ── Negotiations (PRAGMA extension) ─────────────────────────────────────
    def save_negotiation(self, result: NegotiationResult) -> None:
        with self._lock:
            self._negotiations[result.negotiationId] = result

    def get_negotiation(self, negotiation_id: str) -> Optional[NegotiationResult]:
        with self._lock:
            return self._negotiations.get(negotiation_id)

    # ── Subscriptions (PRAGMA extension) ────────────────────────────────────
    def add_subscription(self, sub: Subscription) -> Subscription:
        with self._lock:
            sub.subscriptionId = new_id("sub-")
            sub.created = sub.created or _now()
            self._subscriptions[sub.subscriptionId] = sub
            return sub

    def list_subscriptions(self, intent_id: str) -> list[Subscription]:
        with self._lock:
            return [s for s in self._subscriptions.values() if s.intentObjectInstanceId == intent_id]

    def get_subscription(self, subscription_id: str) -> Optional[Subscription]:
        with self._lock:
            return self._subscriptions.get(subscription_id)

    def remove_subscription(self, subscription_id: str) -> bool:
        with self._lock:
            if subscription_id not in self._subscriptions:
                return False
            del self._subscriptions[subscription_id]
            return True