"""Labels: churn at the renewal inside (C, C+90]; downgrades are renewals; monthly = cancellations (Q1)."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd

from tests.conftest import make_account, make_tenant
from worklist.db import tables as t
from worklist.db.engine import job_engine, operator_conn, tenant_scope
from worklist.model.dataset import load_outcomes


def _outcome(tenant, account, renewal: date, outcome: str, grace=90):
    with operator_conn() as conn:
        conn.execute(t.renewal_outcome.insert().values(
            id=t.new_id(), tenant_id=tenant["id"], account_id=account["id"], renewal_date=renewal, outcome=outcome,
            recorded_at=datetime.combine(renewal + timedelta(days=grace), datetime.min.time(), tzinfo=UTC)))


def test_outcomes_are_only_visible_once_recorded():
    tenant = make_tenant()
    acc = make_account(tenant)
    _outcome(tenant, acc, date(2026, 1, 10), "churned")
    _outcome(tenant, acc, date(2025, 1, 10), "downgraded")
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        before = load_outcomes(conn, pd.Timestamp("2026-03-01", tz="UTC"))
        after = load_outcomes(conn, pd.Timestamp("2026-05-01", tz="UTC"))
    assert list(before["outcome"]) == ["downgraded"]
    assert sorted(after["outcome"]) == ["churned", "downgraded"]


def test_only_churned_is_a_positive_label():
    labels = (pd.Series(["renewed", "downgraded", "churned"]) == "churned").astype(float)
    assert list(labels) == [0.0, 0.0, 1.0]
