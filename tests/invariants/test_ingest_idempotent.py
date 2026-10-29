"""Ingesting the same records twice changes nothing; late matches never alter earlier cutoffs (FR-003a, FR-004)."""

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import text

from tests.conftest import make_account, make_tenant
from worklist.db import tables as t
from worklist.db.engine import job_engine, operator_conn, tenant_scope
from worklist.ingest.feed import ingest_envelopes, retry_quarantine
from worklist.ingest.identity import AccountIndex
from worklist.signals.events import as_of, load_events

T0 = datetime(2026, 3, 1, 12, tzinfo=UTC)


def _env(rid, day, **payload):
    return {"source": "billing", "source_record_id": rid, "event_type": "invoice.paid",
            "occurred_at": f"2026-03-{day:02d}T10:00:00Z", "ingested_at": datetime(2026, 3, day, 11, tzinfo=UTC),
            "payload": {"invoice_id": rid, "stripe_customer_id": "cus_1", **payload}}


def test_reingesting_adds_no_rows_and_counts_duplicates():
    tenant = make_tenant()
    acc = make_account(tenant)
    with operator_conn() as conn:
        conn.execute(t.account_source_id.insert().values(tenant_id=tenant["id"], account_id=acc["id"],
                                                         source="billing", external_id="cus_1"))
    batch = [_env("e1", 1), _env("e2", 2), _env("e2", 2)]
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        first = ingest_envelopes(conn, tenant["id"], batch, AccountIndex(conn), None, {})
        second = ingest_envelopes(conn, tenant["id"], batch, AccountIndex(conn), None, {})
        total = conn.execute(text("SELECT count(*) FROM raw_event")).scalar()
    assert first["ingested"] == 2 and first["duplicates"] == 1
    assert second["ingested"] == 0 and second["duplicates"] == 3
    assert total == 2


def test_late_match_is_invisible_to_earlier_cutoffs():
    tenant = make_tenant()
    acc = make_account(tenant)
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        counts = ingest_envelopes(conn, tenant["id"], [_env("e1", 1)], AccountIndex(conn), None, {})
    assert counts["quarantined"] == 1  # no mapping for cus_1 yet
    with operator_conn() as conn:
        conn.execute(t.account_source_id.insert().values(tenant_id=tenant["id"], account_id=acc["id"],
                                                         source="billing", external_id="cus_1"))
    later = datetime(2026, 3, 10, tzinfo=UTC)
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        retried = retry_quarantine(conn, later, None)
        events = load_events(conn, datetime(2026, 4, 1, tzinfo=UTC))
        matched_at = conn.execute(text("SELECT matched_at FROM event_account")).scalar()
    assert retried["quarantine_resolved"] == 1 and matched_at == later
    assert len(as_of(events, pd.Timestamp("2026-03-05", tz="UTC"))) == 0
    assert len(as_of(events, pd.Timestamp("2026-03-11", tz="UTC"))) == 1
