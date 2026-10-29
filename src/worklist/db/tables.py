"""Table definitions (SQLAlchemy Core). See data-model.md.

[T] = tenant table, forced RLS on tenant_id. [A] = append-only (no UPDATE/DELETE grants).
"""

import os
import time
import uuid

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()


def new_id() -> uuid.UUID:
    """UUIDv7: 48-bit unix ms timestamp + random bits, so ids sort by creation time."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms << 80) | (0x7 << 76) | ((rand >> 64) & 0xFFF) << 64 | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return uuid.UUID(int=value)


def _id() -> Column:
    return Column("id", UUID(as_uuid=True), primary_key=True, default=new_id)


def _tenant() -> Column:
    return Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenant.id"), nullable=False)


TS = DateTime(timezone=True)

# --- Tenancy and identity -------------------------------------------------------------

tenant = Table(
    "tenant", metadata,
    _id(),
    Column("name", Text, nullable=False, unique=True),
    Column("created_at", TS, server_default=text("now()"), nullable=False),
)

tenant_settings = Table(  # [T]
    "tenant_settings", metadata,
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenant.id"), primary_key=True),
    Column("high_band_size", Integer, nullable=False, server_default="10"),
    Column("high_band_floor", Float, nullable=False, server_default="0.15"),
    Column("alert_risk_jump", Float, nullable=False, server_default="0.15"),
    Column("renewal_alert_window_days", Integer, nullable=False, server_default="60"),
    Column("alert_cap_nightly", Integer, nullable=False, server_default="3"),
    Column("alert_cap_weekly", Integer, nullable=False, server_default="10"),
    Column("llm_drafting_enabled", Boolean, nullable=False, server_default="true"),
    Column("source_timezones", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("needs_attention", Boolean, nullable=False, server_default="false"),
)

app_user = Table(  # [T]
    "app_user", metadata,
    _id(), _tenant(),
    Column("email", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("is_demo", Boolean, nullable=False, server_default="false"),
    Column("active", Boolean, nullable=False, server_default="true"),
    UniqueConstraint("tenant_id", "email"),
)

session = Table(  # protected by grants, read before tenant context exists
    "session", metadata,
    Column("id", Text, primary_key=True),
    Column("user_id", UUID(as_uuid=True), nullable=False),
    Column("tenant_id", UUID(as_uuid=True), nullable=False),
    Column("role", Text, nullable=False),
    Column("csrf_token", Text, nullable=False),
    Column("created_at", TS, server_default=text("now()"), nullable=False),
    Column("expires_at", TS, nullable=False),
)

# --- Accounts -------------------------------------------------------------------------

account = Table(  # [T]
    "account", metadata,
    _id(), _tenant(),
    Column("name", Text, nullable=False),
    Column("primary_domain", Text),
    Column("owner_user_id", UUID(as_uuid=True), ForeignKey("app_user.id")),
    Column("segment", Text),
    Column("arr", Float),
    Column("renewal_date", Date),
    Column("contract_type", Text, nullable=False),
    Column("contract_start", Date),
    Column("status", Text, nullable=False, server_default="active"),
    Column("facts_resolved_at", TS),
    Index("ix_account_owner", "tenant_id", "owner_user_id"),
)

account_source_id = Table(  # [T]
    "account_source_id", metadata,
    _tenant(),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("source", Text, nullable=False),
    Column("external_id", Text, nullable=False),
    UniqueConstraint("tenant_id", "source", "external_id"),
)

account_fact_conflict = Table(  # [T]
    "account_fact_conflict", metadata,
    _id(), _tenant(),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("fact", Text, nullable=False),
    Column("chosen_source", Text), Column("chosen_value", Text),
    Column("other_source", Text), Column("other_value", Text),
    Column("detected_run_id", UUID(as_uuid=True)),
    Column("detected_at", TS, server_default=text("now()"), nullable=False),
)

renewal_outcome = Table(  # [T]
    "renewal_outcome", metadata,
    _id(), _tenant(),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("renewal_date", Date, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("arr_before", Float), Column("arr_after", Float),
    Column("recorded_at", TS, nullable=False),
    Column("is_silent_churn", Boolean, nullable=False, server_default="false"),
    UniqueConstraint("tenant_id", "account_id", "renewal_date"),
)

# --- Raw data -------------------------------------------------------------------------

raw_event = Table(  # [T][A]
    "raw_event", metadata,
    _id(), _tenant(),
    Column("source", Text, nullable=False),
    Column("source_record_id", Text),
    Column("dedupe_key", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("occurred_at", TS, nullable=False),
    Column("ingested_at", TS, nullable=False),
    Column("payload", JSONB, nullable=False),
    UniqueConstraint("tenant_id", "source", "dedupe_key"),
    Index("ix_raw_event_ingested", "tenant_id", "ingested_at"),
    Index("ix_raw_event_occurred", "tenant_id", "source", "occurred_at"),
)

event_account = Table(  # [T][A]
    "event_account", metadata,
    Column("raw_event_id", UUID(as_uuid=True), ForeignKey("raw_event.id"), primary_key=True),
    _tenant(),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("match_method", Text, nullable=False),
    Column("matched_at", TS, nullable=False),
    Index("ix_event_account_account", "tenant_id", "account_id"),
)

quarantined_event = Table(  # [T]
    "quarantined_event", metadata,
    Column("raw_event_id", UUID(as_uuid=True), ForeignKey("raw_event.id"), primary_key=True),
    _tenant(),
    Column("reason", Text, nullable=False),
    Column("run_id", UUID(as_uuid=True)),
    Column("last_attempt_run_id", UUID(as_uuid=True)),
    Column("resolved_at", TS),
)

# --- Pipeline and scoring -------------------------------------------------------------

pipeline_run = Table(  # [T]
    "pipeline_run", metadata,
    _id(), _tenant(),
    Column("run_type", Text, nullable=False),
    Column("cutoff_at", TS, nullable=False),
    Column("status", Text, nullable=False),
    Column("model_version_id", UUID(as_uuid=True)),
    Column("started_at", TS, nullable=False),
    Column("finished_at", TS),
    Column("counts", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("is_catch_up", Boolean, nullable=False, server_default="false"),
    Column("error", Text),
    UniqueConstraint("tenant_id", "run_type", "cutoff_at"),
)

signal_snapshot = Table(  # [T][A]
    "signal_snapshot", metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("pipeline_run.id"), primary_key=True),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), primary_key=True),
    _tenant(),
    Column("cutoff_at", TS, nullable=False),
    Column("signals", JSONB, nullable=False),
    Column("signal_defs_version", Text, nullable=False),
    Column("missing_sources", ARRAY(Text), nullable=False),
)

model_version = Table(  # RLS: global rows (tenant_id null) visible to all, tenant rows to their tenant
    "model_version", metadata,
    _id(),
    Column("scope", Text, nullable=False),
    Column("tenant_id", UUID(as_uuid=True), ForeignKey("tenant.id")),
    Column("algorithm", Text, nullable=False),
    Column("history_window", Text, nullable=False),
    Column("trained_at", TS, nullable=False),
    Column("windows", JSONB, nullable=False),
    Column("signal_defs_version", Text, nullable=False),
    Column("artifact", LargeBinary, nullable=False),
    Column("metrics", JSONB, nullable=False),
    Column("status", Text, nullable=False),
    Column("decision_reason", Text),
    Column("decided_at", TS),
)
Index(
    "uq_model_version_approved", model_version.c.scope,
    text("coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
    unique=True, postgresql_where=text("status = 'approved'"),
)

tenant_calibrator = Table(  # [T]
    "tenant_calibrator", metadata,
    _tenant(),
    Column("model_version_id", UUID(as_uuid=True), ForeignKey("model_version.id"), nullable=False),
    Column("method", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    UniqueConstraint("tenant_id", "model_version_id"),
)

account_score = Table(  # [T][A]
    "account_score", metadata,
    _id(), _tenant(),
    Column("run_id", UUID(as_uuid=True), ForeignKey("pipeline_run.id"), nullable=False),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("owner_user_id", UUID(as_uuid=True)),
    Column("state", Text, nullable=False),
    Column("model_version_id", UUID(as_uuid=True)),
    Column("signal_defs_version", Text, nullable=False),
    Column("raw_score", Float),
    Column("probability", Float),
    Column("band", Text),
    Column("rank_in_owner", Integer),
    Column("confidence", Text, nullable=False),
    Column("contributions", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("reasons", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("days_to_renewal", Integer),
    Column("arr", Float),
    Column("cutoff_at", TS, nullable=False),
    UniqueConstraint("run_id", "account_id"),
    Index("ix_account_score_account", "tenant_id", "account_id", "cutoff_at"),
)

# --- Alerts, actions and drafts -------------------------------------------------------

alert = Table(  # [T]
    "alert", metadata,
    _id(), _tenant(),
    Column("run_id", UUID(as_uuid=True), ForeignKey("pipeline_run.id"), nullable=False),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("raised_for_user_id", UUID(as_uuid=True)),
    Column("score_id", UUID(as_uuid=True), ForeignKey("account_score.id"), nullable=False),
    Column("priority", Integer, nullable=False),
    Column("trigger", Text, nullable=False),
    Column("trigger_detail", JSONB, nullable=False),
    Column("fingerprint", Text, nullable=False),
    Column("evidence", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("status", Text, nullable=False, server_default="open"),
    Column("read_at", TS),
    Column("created_at", TS, nullable=False),
)

csm_action = Table(  # [T]
    "csm_action", metadata,
    _id(), _tenant(),
    Column("account_id", UUID(as_uuid=True), ForeignKey("account.id"), nullable=False),
    Column("user_id", UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False),
    Column("action", Text, nullable=False),
    Column("snooze_until", Date),
    Column("reason", Text),
    Column("note", Text),
    Column("fingerprint", JSONB),
    Column("probability_at_action", Float),
    Column("created_at", TS, server_default=text("now()"), nullable=False),
)

outreach_draft = Table(  # [T]
    "outreach_draft", metadata,
    _id(), _tenant(),
    Column("alert_id", UUID(as_uuid=True), ForeignKey("alert.id"), nullable=False, unique=True),
    Column("generator", Text, nullable=False),
    Column("fallback_reason", Text),
    Column("llm_model", Text),
    Column("prompt_version", Text),
    Column("evidence_hash", Text, nullable=False),
    Column("internal_summary", Text, nullable=False),
    Column("email_subject", Text, nullable=False),
    Column("email_body", Text, nullable=False),
    Column("facts_used", ARRAY(Text), nullable=False),
    Column("status", Text, nullable=False, server_default="draft"),
    Column("final_text", Text),
    Column("approved_by", UUID(as_uuid=True)),
    Column("approved_at", TS),
    Column("created_at", TS, server_default=text("now()"), nullable=False),
)

# --- Simulation and audit -------------------------------------------------------------

sim_clock = Table(
    "sim_clock", metadata,
    Column("id", Integer, primary_key=True, default=1),
    Column("current_date", Date, nullable=False),
)

pending_feed_event = Table(  # [T] in schema `sim`, job role only
    "pending_feed_event", metadata,
    _id(), _tenant(),
    Column("source", Text, nullable=False),
    Column("source_record_id", Text),
    Column("event_type", Text, nullable=False),
    Column("occurred_at_raw", Text, nullable=False),
    Column("ingested_at", TS, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("delivered", Boolean, nullable=False, server_default="false"),
    Index("ix_pending_feed_due", "tenant_id", "delivered", "ingested_at"),
    schema="sim",
)

access_audit = Table(  # [A] web role INSERT only
    "access_audit", metadata,
    _id(),
    Column("at", TS, server_default=text("now()"), nullable=False),
    Column("user_id", UUID(as_uuid=True)),
    Column("tenant_id", UUID(as_uuid=True)),
    Column("route", Text, nullable=False),
    Column("requested_id", Text),
    Column("outcome", Text, nullable=False),
)

TENANT_TABLES = [
    "tenant_settings", "app_user", "account", "account_source_id", "account_fact_conflict",
    "renewal_outcome", "raw_event", "event_account", "quarantined_event", "pipeline_run",
    "signal_snapshot", "tenant_calibrator", "account_score", "alert", "csm_action", "outreach_draft",
]
APPEND_ONLY_TABLES = ["raw_event", "event_account", "signal_snapshot", "account_score"]
