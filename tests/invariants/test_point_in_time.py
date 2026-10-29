"""Signals and labels at cutoff C never change when later records, matches or outcomes arrive (FR-007)."""

from datetime import UTC, datetime, timedelta

import pandas as pd
from pandas.testing import assert_frame_equal

from tests.conftest import make_account, make_tenant
from worklist.db import tables as t
from worklist.db.engine import job_engine, operator_conn, tenant_scope
from worklist.ingest.feed import ingest_envelopes
from worklist.ingest.identity import AccountIndex
from worklist.model.dataset import load_outcomes
from worklist.signals.compute import compute_snapshot
from worklist.signals.events import load_accounts, load_events, load_users

C = pd.Timestamp("2026-05-01", tz="UTC")


def _env(i, source, etype, occurred: datetime, ingested: datetime, **payload):
    return {"source": source, "source_record_id": f"r{i}", "event_type": etype,
            "occurred_at": occurred.isoformat(), "ingested_at": ingested, "payload": payload}


def _snapshot(tenant):
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        events = load_events(conn, datetime(2027, 1, 1, tzinfo=UTC))
        snap = compute_snapshot(events, load_accounts(conn), load_users(conn), C)
        labels = load_outcomes(conn, C)
    return snap, labels


def test_late_records_and_outcomes_do_not_change_cutoff_results():
    tenant = make_tenant()
    acc = make_account(tenant, primary_domain="acme.com")
    with operator_conn() as conn:
        for source, ext in (("billing", "cus_1"), ("product", "org_1"), ("support", "zd_1")):
            conn.execute(t.account_source_id.insert().values(tenant_id=tenant["id"], account_id=acc["id"],
                                                             source=source, external_id=ext))
    base = datetime(2026, 4, 29, tzinfo=UTC)
    early = [
        _env(1, "billing", "subscription.updated", base, base + timedelta(hours=1), stripe_customer_id="cus_1",
             mrr=1000, current_period_end="2026-06-15", cancel_at_period_end=False),
        _env(2, "support", "ticket.created", base, base + timedelta(hours=1), ticket_id="t1", zendesk_org_id="zd_1",
             priority="high", requester_email="a@acme.com"),
        _env(3, "product", "usage.summary", base, base + timedelta(hours=2), product_org_id="org_1",
             active_users=10, sessions=100),
    ]
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        ingest_envelopes(conn, tenant["id"], early, AccountIndex(conn), None, {})
    before, labels_before = _snapshot(tenant)

    late_time = C.to_pydatetime() + timedelta(days=2)
    late = [  # happened before C, received after C
        _env(4, "support", "ticket.created", base + timedelta(hours=12), late_time, ticket_id="t2",
             zendesk_org_id="zd_1", priority="urgent", requester_email="a@acme.com"),
        _env(5, "billing", "invoice.payment_failed", base + timedelta(hours=20), late_time, invoice_id="i1",
             stripe_customer_id="cus_1", attempt=3),
    ]
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        ingest_envelopes(conn, tenant["id"], late, AccountIndex(conn), None, {})
    with operator_conn() as conn:
        conn.execute(t.renewal_outcome.insert().values(
            id=t.new_id(), tenant_id=tenant["id"], account_id=acc["id"], renewal_date=datetime(2026, 4, 20).date(),
            outcome="churned", recorded_at=late_time))
    after, labels_after = _snapshot(tenant)

    assert_frame_equal(before.signals, after.signals)
    assert_frame_equal(before.facts, after.facts)
    assert len(labels_before) == len(labels_after) == 0
    assert before.signals.loc[acc["id"], "high_urgent_tickets_30d"] == 1
