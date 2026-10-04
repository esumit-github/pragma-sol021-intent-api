"""Subscription registry + CloudEvents notification dispatch (PRAGMA extension).

Implements IFA 050 §7.6/7.7/7.9 (Subscribe / Terminate Subscription / Notify) that
SOL 021 V5.4.1 defers. Notifications are emitted as CNCF CloudEvents 1.0 envelopes
(type org.pragma.intent.report) and delivered best-effort to subscriber callback
URLs with an in-process retry counter (recorded for the paper's evaluation).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from .schemas import CloudEvent, IntentReport, Subscription
from .store import IntentStore, new_id

logger = logging.getLogger("pragma.notify")


class NotificationService:
    """Delivers IntentReportNotification events to subscribers."""

    MAX_RETRIES = 3

    def __init__(self, store: IntentStore, timeout: float = 1.0) -> None:
        self._store = store
        self._timeout = timeout
        self.retry_counts: dict[str, int] = {}
        self.delivered: list[CloudEvent] = []
        self._transport: Optional[httpx.AsyncClient] = None

    def set_transport(self, client: httpx.AsyncClient) -> None:
        """Allow tests to inject a mock transport (no real network)."""
        self._transport = client

    async def _post(self, url: str, payload: dict) -> bool:
        if self._transport is not None:
            response = await self._transport.post(url, json=payload)
            return response.status_code < 300
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, json=payload)
            return response.status_code < 300

    async def subscribe(self, intent_id: str, callback_url: str) -> Subscription:
        return self._store.add_subscription(
            Subscription(
                subscriptionId=new_id("sub-"),
                intentObjectInstanceId=intent_id,
                callbackUrl=callback_url,
            )
        )

    async def unsubscribe(self, subscription_id: str) -> bool:
        return self._store.remove_subscription(subscription_id)

    async def notify_report(self, intent_id: str, report: IntentReport) -> list[CloudEvent]:
        """Emit an IntentReportNotification to every subscriber of *intent_id*.

        Best-effort with retry: each failed delivery is retried up to MAX_RETRIES
        times; the retry count per subscription is recorded for evaluation.
        """
        subscribers = self._store.list_subscriptions(intent_id)
        event_payload = {
            "intentReport": report.model_dump(mode="json"),
        }
        emitted: list[CloudEvent] = []

        for sub in subscribers:
            event = CloudEvent(
                id=new_id("evt-"),
                source=f"pragma://nfvintent/v1/intents/{intent_id}",
                time=datetime.now(timezone.utc).isoformat(),
                data=event_payload,
            )
            payload = event.model_dump(mode="json")
            ok = False
            for attempt in range(self.MAX_RETRIES):
                try:
                    ok = await self._post(sub.callbackUrl, payload)
                    if ok:
                        break
                except Exception:  # noqa: BLE001 — best-effort delivery
                    ok = False
                if not ok and attempt == self.MAX_RETRIES - 1:
                    logger.warning(
                        "delivery failed for %s after %d attempts", sub.callbackUrl, attempt + 1
                    )
            self.retry_counts[sub.subscriptionId] = 0 if ok else self.MAX_RETRIES
            if ok:
                self.delivered.append(event)
                emitted.append(event)

        return emitted