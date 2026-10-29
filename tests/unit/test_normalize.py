"""Envelope normalisation (FR-001a, FR-004, contracts/source-events.md ingest rules)."""

from datetime import UTC, datetime

from worklist.ingest.normalize import normalize

TZ = {"product": "America/New_York"}
INGESTED = datetime(2026, 3, 2, 12, tzinfo=UTC)


def test_naive_timestamp_uses_source_timezone_and_keeps_original():
    n = normalize("product", None, "usage.summary", "2026-03-01T23:00:00", INGESTED,
                  {"product_org_id": "o1", "active_users": 4}, TZ)
    assert n.occurred_at == datetime(2026, 3, 2, 4, tzinfo=UTC)
    assert n.payload["_occurred_at_original"] == "2026-03-01T23:00:00"
    assert n.invalid_reason is None


def test_aware_timestamp_converted_to_utc():
    n = normalize("support", "t1", "ticket.created", "2026-03-01T10:00:00+02:00", INGESTED,
                  {"ticket_id": "t1", "priority": "high"}, TZ)
    assert n.occurred_at == datetime(2026, 3, 1, 8, tzinfo=UTC)


def test_dedupe_key_prefers_source_record_id_else_content_hash():
    payload = {"product_org_id": "o1", "active_users": 4}
    a = normalize("product", None, "usage.summary", "2026-03-01T23:00:00", INGESTED, payload, TZ)
    b = normalize("product", None, "usage.summary", "2026-03-01T23:00:00", INGESTED, dict(payload), TZ)
    c = normalize("billing", "evt_1", "invoice.paid", "2026-03-01T10:00:00Z", INGESTED,
                  {"invoice_id": "i", "stripe_customer_id": "c"}, TZ)
    assert a.dedupe_key == b.dedupe_key and a.dedupe_key.startswith("sha256:")
    assert c.dedupe_key == "evt_1"


def test_future_occurred_at_is_invalid():
    n = normalize("billing", "e", "invoice.paid", "2026-03-05T10:00:00Z", INGESTED,
                  {"invoice_id": "i", "stripe_customer_id": "c"}, TZ)
    assert n.invalid_reason and "after ingested_at" in n.invalid_reason


def test_missing_required_field_is_invalid():
    n = normalize("support", "x", "ticket.created", "2026-03-01T10:00:00Z", INGESTED, {"ticket_id": "x"}, TZ)
    assert n.invalid_reason == "missing priority"
