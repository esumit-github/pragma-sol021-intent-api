"""Pydantic models mirroring IFA 050 information elements and SOL 021 REST types.

Field names and enum spellings follow ETSI GS NFV-IFA 050 V5.2.1 (§8.2) and
SOL 021 V5.4.1 exactly — do not rename.
"""

from __future__ import annotations

import enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ── Enumerations (verbatim from the specs) ────────────────────────────────────

class ConditionOperator(str, enum.Enum):
    GREATER_THAN = "greater than"
    EQUAL_TO = "equal to"
    LESS_THAN = "less than"
    NOT_EQUAL_TO = "not equal to"
    ONE_OF = "one of"
    SOME_OF = "some of"
    ALL_OF = "all of"
    MAXIMUM_VALUE = "maximum value"
    MINIMUM_VALUE = "minimum value"
    MEDIAN = "median"
    CREDIBILITY = "credibility"


class ListOperator(str, enum.Enum):
    AND = "and"
    OR = "or"


class ExpectationType(str, enum.Enum):
    DELIVERY = "Delivery"
    ENSURE = "Ensure"
    PROPERTY = "Property"
    REPORT = "Report"


class FulfilmentStatus(str, enum.Enum):
    FULFILLED = "FULFILLED"
    NOT_FULFILLED = "NOT_FULFILLED"


class NotFulfilledState(str, enum.Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    COMPLIANT = "COMPLIANT"
    DEGRADED = "DEGRADED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


class IntentState(str, enum.Enum):
    """Lifecycle states per IFA 050 §8.2.6.3 (encoded via fulfilmentStatus + notFulfilledState)."""

    FULFILLED = "FULFILLED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    COMPLIANT = "COMPLIANT"
    DEGRADED = "DEGRADED"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"


class NegotiationMode(str, enum.Enum):
    SYNC = "sync"
    ASYNC = "async"


class NegotiationOutcome(str, enum.Enum):
    FEASIBLE = "FEASIBLE"
    PARTIAL = "PARTIAL"
    INFEASIBLE = "INFEASIBLE"


# ── IFA 050 information elements (§8.2) ───────────────────────────────────────

class ExpectationTarget(BaseModel):
    """8.2.1 ExpectationTarget — target resource + PRAGMA cross-domain extension."""

    expectationTargetId: str = Field(..., description="Identifier of the target")
    targetType: Optional[str] = None
    domainId: Optional[str] = Field(
        None,
        description="PRAGMA extension: administrative domain the target belongs to (cross-domain routing)",
    )
    intentOriginRef: Optional[str] = Field(
        None,
        description="PRAGMA extension: reference to the originating intent when forwarded across domains",
    )


class ExpectationObject(BaseModel):
    """8.2.1 ExpectationObject — the object the expectation applies to."""

    objectId: str
    objectType: Optional[str] = None


class Condition(BaseModel):
    """8.2.3 Condition — assertion; exactly one of conditionValue or conditionList."""

    conditionId: str
    conditionName: Optional[str] = None
    operator: ConditionOperator
    conditionValue: Optional[Any] = None
    conditionList: Optional[list[Condition]] = None
    listOperator: Optional[ListOperator] = None


class Context(BaseModel):
    """8.2.4 Context — applicability constraints."""

    contextId: str
    contextName: Optional[str] = None
    contextConditions: list[Condition] = Field(..., min_length=1)


class IntentExpectation(BaseModel):
    """8.2.1 IntentExpectation."""

    expectationId: str
    expectationName: Optional[str] = None
    expectationType: ExpectationType = ExpectationType.PROPERTY
    expectationObject: Optional[ExpectationObject] = None
    expectationTargets: list[ExpectationTarget] = Field(..., min_length=1)
    expectationContexts: list[Context] = Field(default_factory=list)


class IntentObject(BaseModel):
    """8.2.2 IntentObject — the intent declaration."""

    intentId: str
    intentName: Optional[str] = None
    intentExpectations: list[IntentExpectation] = Field(..., min_length=1)
    intentContexts: list[Context] = Field(default_factory=list)


class FulfilmentInfo(BaseModel):
    """8.2.6 FulfilmentInfo — fulfilment status of an aspect.

    The lifecycle state machine (IFA 050 §8.2.6.3) is encoded here:
    FULFILLED → fulfilmentStatus=FULFILLED; otherwise NOT_FULFILLED with
    notFulfilledState carrying the state token.
    """

    fulfilmentObjectId: str
    fulfilmentStatus: FulfilmentStatus = FulfilmentStatus.NOT_FULFILLED
    notFulfilledState: Optional[NotFulfilledState] = NotFulfilledState.ACKNOWLEDGED
    notFulfilledReasons: Optional[str] = None
    achievedValue: Optional[Any] = None
    objectInstances: list[str] = Field(default_factory=list)


class IntentReport(BaseModel):
    """8.2.7 IntentReport."""

    intentReportId: str
    intentReference: str
    fulfillmentInfos: list[FulfilmentInfo] = Field(..., min_length=1)
    reportTime: str = Field(..., description="ISO 8601 timestamp")


class IntentReportNotification(BaseModel):
    """8.2.10 IntentReportNotification."""

    intentReport: IntentReport


# ── SOL 021 REST types ───────────────────────────────────────────────────────

class CreateIntentRequest(BaseModel):
    """SOL 021 POST /intents body — description of the intent to create."""

    intent: IntentObject


class Intent(BaseModel):
    """SOL 021 stage-3 serialization of an intent object instance."""

    intentObjectInstanceId: str = Field(..., description="Assigned by the server on create")
    intent: IntentObject
    fulfilmentInfos: list[FulfilmentInfo] = Field(..., min_length=1)
    intentReports: list[IntentReport] = Field(default_factory=list)
    revision: int = Field(1, description="ETag/revision counter for If-Match (SOL 013)")
    created: Optional[str] = Field(None, description="ISO 8601 creation timestamp")


class IntentModifications(BaseModel):
    """SOL 021 PATCH body — partial update of an intent object instance."""

    intent: Optional[IntentObject] = None
    fulfilmentInfos: Optional[list[FulfilmentInfo]] = None


class ProblemDetails(BaseModel):
    """SOL 013 §6.4 / RFC 9457 error body."""

    type: str = "about:blank"
    title: str = ""
    status: int
    detail: str = ""
    instance: Optional[str] = None


# ── PRAGMA extension types ────────────────────────────────────────────────────

class NegotiationRequest(BaseModel):
    """IFA 050 7.8 Negotiate — owner-initiated feasibility check BEFORE intent creation.

    The owner submits an IntentObject (intentObjectIn); the handler returns the
    confirmed IntentObject (intentObjectOut) in the NegotiationResult. Per the
    IFA 050 7.8 NOTE this is strictly pre-creation: negotiation does not operate
    on an existing IntentObjectInstance.
    """

    mode: NegotiationMode = NegotiationMode.SYNC
    intentObject: IntentObject
    acceptableDegradationPct: Optional[float] = None
    deadlineUtc: Optional[str] = None


class ProposedModification(BaseModel):
    """A proposed relaxation of a single expectation (PRAGMA extension)."""

    expectationId: str
    metric: Optional[str] = None
    originalValue: Optional[str] = None
    proposedValue: Optional[str] = None
    reason: str


class NegotiationResult(BaseModel):
    """Result of a negotiation exchange (IFA 050 7.8.3 output intentObjectOut)."""

    negotiationId: str
    outcome: NegotiationOutcome
    intentObjectOut: Optional[IntentObject] = None
    proposedModifications: list[ProposedModification] = Field(default_factory=list)
    feasibility: Optional[str] = None
    expiresAt: Optional[str] = None
    state: Literal["PENDING", "COMPLETED"] = "COMPLETED"


class Subscription(BaseModel):
    """PRAGMA subscription resource (IFA 050 7.6)."""

    subscriptionId: str
    intentObjectInstanceId: str
    callbackUrl: str
    filterExpression: Optional[str] = None
    created: Optional[str] = None


class CloudEvent(BaseModel):
    """CNCF CloudEvents 1.0 envelope."""

    specversion: str = "1.0"
    id: str
    source: str
    type: str = "org.pragma.intent.report"
    time: Optional[str] = None
    datacontenttype: str = "application/json"
    data: Optional[dict[str, Any]] = None