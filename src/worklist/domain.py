"""Shared enumerations. Values match data-model.md exactly."""

from enum import StrEnum


class Source(StrEnum):
    CRM = "crm"
    EMAIL = "email"
    SUPPORT = "support"
    PRODUCT = "product"
    BILLING = "billing"


class Role(StrEnum):
    CSM = "csm"
    CS_MANAGER = "cs_manager"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    RENEWAL_OVERDUE = "renewal_overdue"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CHURNED = "churned"


class ContractType(StrEnum):
    TERM = "term"
    MONTHLY = "monthly"


class ScoreState(StrEnum):
    SCORED = "scored"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_DUE = "not_due"
    RENEWAL_OVERDUE = "renewal_overdue"
    NO_MODEL = "no_model"


class Band(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(StrEnum):
    NORMAL = "normal"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"


class Trigger(StrEnum):
    SEVERE_EVENT_NEAR_RENEWAL = "severe_event_near_renewal"
    ENTERED_TOP_K = "entered_top_k"
    RISK_JUMP = "risk_jump"


# Lower number = higher priority when alert caps apply (FR-017a).
TRIGGER_PRIORITY = {
    Trigger.SEVERE_EVENT_NEAR_RENEWAL: 1,
    Trigger.ENTERED_TOP_K: 2,
    Trigger.RISK_JUMP: 3,
}


class RunType(StrEnum):
    NIGHTLY = "nightly"
    RETRAIN = "retrain"
    BACKFILL = "backfill"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class ActionType(StrEnum):
    CONTACTED = "contacted"
    MEETING_BOOKED = "meeting_booked"
    SNOOZED = "snoozed"
    DISMISSED = "dismissed"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    DISCARDED = "discarded"


class DraftGenerator(StrEnum):
    LLM = "llm"
    TEMPLATE = "template"


class AuditOutcome(StrEnum):
    DENIED_CROSS_TENANT = "denied_cross_tenant"
    DENIED_NO_SESSION = "denied_no_session"
    DENIED_ROLE = "denied_role"


class QuarantineReason(StrEnum):
    NO_MATCH = "no_match"
    AMBIGUOUS_MATCH = "ambiguous_match"
    INVALID_PAYLOAD = "invalid_payload"


class Outcome(StrEnum):
    RENEWED = "renewed"
    DOWNGRADED = "downgraded"
    CHURNED = "churned"


HORIZON_DAYS = 90
