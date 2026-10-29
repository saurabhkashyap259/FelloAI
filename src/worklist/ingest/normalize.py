"""Envelope normalisation: UTC times, dedupe keys and payload validation (FR-001a, FR-004)."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

REQUIRED = {
    "account.upserted": ["sf_account_id"], "contact.changed": ["sf_account_id", "change"],
    "opportunity.updated": ["sf_account_id", "stage"],
    "email.sent": ["to_domain"], "email.received": ["from_domain"], "email.bounced": ["to_email"],
    "ticket.created": ["ticket_id", "priority"], "ticket.updated": ["ticket_id"], "ticket.solved": ["ticket_id"],
    "usage.summary": ["product_org_id", "active_users"], "seats.changed": ["product_org_id", "seats_purchased"],
    "invoice.paid": ["invoice_id", "stripe_customer_id"],
    "invoice.payment_failed": ["invoice_id", "stripe_customer_id", "attempt"],
    "subscription.updated": ["stripe_customer_id", "current_period_end"],
}
SKEW_TOLERANCE = timedelta(days=1)


@dataclass
class Normalized:
    occurred_at: datetime
    dedupe_key: str
    payload: dict
    invalid_reason: str | None


def parse_time(raw: str, source_tz: str) -> tuple[datetime, bool]:
    """Returns (UTC time, was_naive). Naive values are read in the source's configured timezone."""
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(source_tz)).astimezone(UTC), True
    return value.astimezone(UTC), False


def dedupe_key(source_record_id: str | None, event_type: str, occurred_raw: str, payload: dict) -> str:
    if source_record_id:
        return source_record_id
    canonical = json.dumps([event_type, occurred_raw, payload], sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def normalize(source: str, source_record_id: str | None, event_type: str, occurred_raw: str | None,
              ingested_at: datetime, payload: dict, source_timezones: dict) -> Normalized:
    key = dedupe_key(source_record_id, event_type, occurred_raw or "", payload)
    stored = dict(payload)
    invalid = None
    missing = [f for f in REQUIRED.get(event_type, []) if payload.get(f) in (None, "")]
    if event_type not in REQUIRED:
        invalid = f"unknown event_type {event_type}"
    elif missing:
        invalid = "missing " + ",".join(missing)
    try:
        occurred, naive = parse_time(occurred_raw or "", source_timezones.get(source, "UTC"))
        if naive:
            stored["_occurred_at_original"] = occurred_raw
            stored["_occurred_at_tz"] = source_timezones.get(source, "UTC")
    except ValueError:
        occurred, invalid = ingested_at, invalid or "unparseable occurred_at"
    if invalid is None and occurred > ingested_at + SKEW_TOLERANCE:
        invalid = "occurred_at after ingested_at + 1 day"
    return Normalized(occurred, key, stored, invalid)
