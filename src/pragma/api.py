"""FastAPI application: SOL 021 V5.4.1 conformant core + PRAGMA extensions.

Run:  uv run python -m pragma   (uvicorn on 0.0.0.0:8000)
OpenAPI: served at /openapi.json
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, Header, Path, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter

from .negotiation import NegotiationService
from .routing import DomainRouter
from .schemas import (
    CreateIntentRequest,
    FulfilmentInfo,
    FulfilmentStatus,
    Intent,
    IntentModifications,
    NegotiationMode,
    NegotiationRequest,
    NegotiationResult,
    NotFulfilledState,
    ProblemDetails,
    Subscription,
)
from .state_machine import IntentEvent, StateMachine
from .store import IntentStore
from .subscriptions import NotificationService

logger = logging.getLogger("pragma")

API_ROOT = "/nfvintent/v1"

# Document the SOL 013 §6.4 error body on every endpoint that can raise it,
# so ProblemDetails is present in the published OpenAPI schema.
ERROR_RESPONSES: dict[int, dict] = {
    400: {"model": ProblemDetails, "description": "Bad request (invalid filter / selector)"},
    404: {"model": ProblemDetails, "description": "Intent not found"},
    405: {"model": ProblemDetails, "description": "Method not allowed"},
    412: {"model": ProblemDetails, "description": "Precondition failed (ETag mismatch)"},
    422: {"model": ProblemDetails, "description": "Unprocessable content / invalid state transition"},
}


class ProblemDetailsError(Exception):
    def __init__(self, status: int, title: str, detail: str, instance: Optional[str] = None) -> None:
        self.problem = ProblemDetails(
            status=status, title=title, detail=detail, instance=instance
        )
        super().__init__(detail)


def _state_of(intent: Intent) -> str:
    """Derive the current lifecycle state from the first fulfilment info."""
    if not intent.fulfilmentInfos:
        return "ACKNOWLEDGED"
    fi = intent.fulfilmentInfos[0]
    return StateMachine.from_fulfilment_encoding(
        fi.fulfilmentStatus.value, fi.notFulfilledState.value if fi.notFulfilledState else None
    ).value


def _set_state(intent: Intent, state: "IntentState") -> None:
    status, not_state = StateMachine.to_fulfilment_encoding(state)
    if not intent.fulfilmentInfos:
        intent.fulfilmentInfos = [
            FulfilmentInfo(
                fulfilmentObjectId=intent.intentObjectInstanceId or "self",
                fulfilmentStatus=FulfilmentStatus(status),
                notFulfilledState=NotFulfilledState(not_state) if not_state else None,
            )
        ]
    else:
        fi = intent.fulfilmentInfos[0]
        fi.fulfilmentStatus = FulfilmentStatus(status)
        fi.notFulfilledState = NotFulfilledState(not_state) if not_state else None


class PragmaApp:
    """Factory assembling the full application with dependency injection (testable)."""

    def __init__(self) -> None:
        self.store = IntentStore()
        self.negotiation = NegotiationService(self.store)
        self.notifications = NotificationService(self.store)
        self.router = DomainRouter()

    def build(self) -> FastAPI:
        app = FastAPI(
            title="PRAGMA — Intent Management API",
            version="1.0.0",
            description=(
                "Conformance-accurate implementation of ETSI GS NFV-SOL 021 V5.4.1 "
                "(Create/Query/Update/Delete intent object instances) plus the PRAGMA "
                "extensions: Negotiate, Subscribe/Notify, and cross-domain propagation "
                "— the IFA 050 operations SOL 021 defers."
            ),
        )
        app.state.pragma = self
        router = APIRouter(prefix=API_ROOT)

        # ── SOL 021 core: collection resource ────────────────────────────────
        @router.post("/intents", status_code=201, responses=ERROR_RESPONSES)
        async def create_intent(body: CreateIntentRequest) -> JSONResponse:
            intent = Intent(
                intentObjectInstanceId="",
                intent=body.intent,
                fulfilmentInfos=[
                    FulfilmentInfo(
                        fulfilmentObjectId="self",
                        fulfilmentStatus=FulfilmentStatus.NOT_FULFILLED,
                        notFulfilledState=NotFulfilledState.ACKNOWLEDGED,
                    )
                ],
            )
            self.router.stamp(intent)
            self.store.create(intent)
            return JSONResponse(
                status_code=201,
                content=intent.model_dump(mode="json"),
                headers={"Location": f"{API_ROOT}/intents/{intent.intentObjectInstanceId}"},
            )

        @router.get("/intents")
        async def list_intents(
            filter: Optional[str] = Query(default=None, description="Attribute-based filtering"),
            all_fields: Optional[str] = Query(default=None),
            fields: Optional[str] = Query(default=None),
            exclude_fields: Optional[str] = Query(default=None),
            nextpage_opaque_marker: Optional[str] = Query(default=None),
        ) -> list[Intent]:
            intents = self.store.list()
            if filter:
                intents = _apply_filter(intents, filter)
            if nextpage_opaque_marker:
                # simple offset-key pagination (reference implementation)
                try:
                    offset = int(nextpage_opaque_marker)
                    intents = intents[offset:]
                except ValueError:
                    raise ProblemDetailsError(
                        400, "Invalid pagination marker", "nextpage_opaque_marker must be an integer"
                    )
            return intents

        # ── SOL 021 core: item resource ──────────────────────────────────────
        @router.get("/intents/{intentId}")
        async def get_intent(intentId: str) -> Intent:
            intent = self.store.get(intentId)
            if intent is None:
                raise ProblemDetailsError(404, "Not Found", f"Intent {intentId} not found")
            return intent

        @router.patch("/intents/{intentId}")
        async def patch_intent(
            intentId: str,
            body: IntentModifications,
            if_match: Optional[str] = Header(default=None, alias="If-Match"),
        ) -> Intent:
            intent = self.store.get(intentId)
            if intent is None:
                raise ProblemDetailsError(404, "Not Found", f"Intent {intentId} not found")

            if if_match is not None and if_match.strip() != f'"{intent.revision}"':
                raise ProblemDetailsError(
                    412,
                    "Precondition Failed",
                    f"ETag mismatch: resource is at revision {intent.revision}",
                    f"/intents/{intentId}",
                )

            current_state = _state_of(intent)
            if body.fulfilmentInfos:
                desired = body.fulfilmentInfos[0]
                event = _fulfilment_event(desired.fulfilmentStatus.value, desired.notFulfilledState.value if desired.notFulfilledState else None)
                new_state, ok = StateMachine.transition(current_state, event)
                if not ok:
                    raise ProblemDetailsError(
                        422,
                        "Unprocessable Content",
                        f"Transition from {current_state} on {event.value} is not allowed by IFA 050 §8.2.6.3",
                        f"/intents/{intentId}",
                    )
                _set_state(intent, new_state)

            if body.intent is not None:
                intent.intent = body.intent

            self.store.update(intentId, intent)
            return intent

        @router.delete("/intents/{intentId}", status_code=204)
        async def delete_intent(intentId: str) -> None:
            intent = self.store.get(intentId)
            if intent is None:
                raise ProblemDetailsError(404, "Not Found", f"Intent {intentId} not found")
            self.store.delete(intentId)

        # ── PRAGMA extension: negotiation (pre-creation, IFA 050 §7.8) ──────
        @router.post("/negotiations")
        async def negotiate(body: NegotiationRequest) -> NegotiationResult:
            result = self.negotiation.negotiate(body)
            if body.mode == NegotiationMode.ASYNC:
                return JSONResponse(
                    status_code=202,
                    content=result.model_dump(mode="json"),
                    headers={"Location": f"{API_ROOT}/negotiations/{result.negotiationId}"},
                )
            return result

        @router.get("/negotiations/{negotiationId}")
        async def get_negotiation(negotiationId: str) -> NegotiationResult:
            result = self.store.get_negotiation(negotiationId)
            if result is None:
                raise ProblemDetailsError(404, "Not Found", f"Negotiation {negotiationId} not found")
            return result

        # ── PRAGMA extension: subscriptions + notifications ──────────────────
        @router.post("/intents/{intentId}/subscriptions", status_code=201)
        async def subscribe(intentId: str, callback_url: str = Query(...)) -> Subscription:
            intent = self.store.get(intentId)
            if intent is None:
                raise ProblemDetailsError(404, "Not Found", f"Intent {intentId} not found")
            return await self.notifications.subscribe(intentId, callback_url)

        @router.get("/intents/{intentId}/subscriptions")
        async def list_subscriptions(intentId: str) -> list[Subscription]:
            intent = self.store.get(intentId)
            if intent is None:
                raise ProblemDetailsError(404, "Not Found", f"Intent {intentId} not found")
            return self.store.list_subscriptions(intentId)

        @router.delete("/subscriptions/{subscriptionId}", status_code=204)
        async def unsubscribe(subscriptionId: str) -> None:
            ok = await self.notifications.unsubscribe(subscriptionId)
            if not ok:
                raise ProblemDetailsError(404, "Not Found", f"Subscription {subscriptionId} not found")

        @router.post("/notifications", status_code=204)
        async def notify_callback(request: Request) -> None:
            # Consumer-hosted callback endpoint per IFA 050 §7.9 — receives
            # IntentReportNotification (CloudEvents). The reference implementation
            # merely acknowledges receipt (204).
            await request.json()

        # ── Error handler ────────────────────────────────────────────────────
        @app.exception_handler(ProblemDetailsError)
        async def problem_details_handler(request: Request, exc: ProblemDetailsError) -> JSONResponse:
            return JSONResponse(status_code=exc.problem.status, content=exc.problem.model_dump(mode="json"))

        # 405 for unsupported methods on both resources (SOL 013 §6.4)
        @router.api_route("/intents", methods=["PUT"], include_in_schema=False)
        async def intents_put_not_allowed() -> None:
            raise ProblemDetailsError(405, "Method Not Allowed", "PUT is not supported on /intents")

        @router.api_route("/intents/{intentId}", methods=["PUT", "POST"], include_in_schema=False)
        async def item_put_not_allowed(intentId: str) -> None:
            raise ProblemDetailsError(405, "Method Not Allowed", "Method not supported on /intents/{intentId}")

        app.include_router(router)
        return app


def _apply_filter(intents: list[Intent], filter_expr: str) -> list[Intent]:
    """Attribute-based filtering over known fields (SOL 021 §5.5.2.3.2).

    Supports a single `attr=value` equality clause, and `attr!=value` exclusion.
    Invalid expressions raise 400.
    """
    expr = filter_expr.strip()
    if not expr:
        return intents
    negate = False
    if expr.startswith("!"):
        negate = True
        expr = expr[1:].strip()
    if "=" not in expr:
        raise ProblemDetailsError(400, "Bad Request", f"Invalid filter expression: {filter_expr}")

    attr, value = expr.split("=", 1)
    attr, value = attr.strip(), value.strip().strip("'\"")
    result: list[Intent] = []
    for intent in intents:
        matched = _match_attr(intent, attr, value)
        if negate:
            if not matched:
                result.append(intent)
        elif matched:
            result.append(intent)
    return result


def _match_attr(intent: Intent, attr: str, value: str) -> bool:
    if attr == "intentName":
        return intent.intent.intentName == value
    if attr == "intentId":
        return intent.intent.intentId == value
    if attr == "state":
        return _state_of(intent) == value.upper()
    if attr == "domainId":
        for exp in intent.intent.intentExpectations:
            for t in exp.expectationTargets:
                if t.domainId == value:
                    return True
        return False
    return False


def _fulfilment_event(status: str, not_state: Optional[str]) -> IntentEvent:
    """Map a PATCH fulfilment update onto the Figure 8.2.6.3-1 event set.

    notFulfilledState=COMPLIANT   -> ACCEPTED (ACKNOWLEDGED -> COMPLIANT)
    notFulfilledState=DEGRADED    -> NOT_SATISFIED (COMPLIANT/FULFILLED -> DEGRADED)
    fulfilmentStatus=FULFILLED    -> SATISFIED (COMPLIANT/DEGRADED -> FULFILLED)
    notFulfilledState=FAILED      -> CONFIRMED_FAILED (DEGRADED -> FAILED); falls back
                                     to NOT_ACCEPTED for the ACKNOWLEDGED -> FAILED path
    notFulfilledState=ACKNOWLEDGED -> UPDATED (DEGRADED -> ACKNOWLEDGED)
    notFulfilledState=TERMINATED  -> DELETED
    """
    if status == "FULFILLED":
        return IntentEvent.SATISFIED
    if not_state == "COMPLIANT":
        return IntentEvent.ACCEPTED
    if not_state == "DEGRADED":
        return IntentEvent.NOT_SATISFIED
    if not_state == "FAILED":
        return IntentEvent.CONFIRMED_FAILED
    if not_state == "ACKNOWLEDGED":
        return IntentEvent.UPDATED
    if not_state == "TERMINATED":
        return IntentEvent.DELETED
    return IntentEvent.ACCEPTED


def create_app() -> FastAPI:
    return PragmaApp().build()


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("pragma.api:app", host="0.0.0.0", port=8000, reload=False)