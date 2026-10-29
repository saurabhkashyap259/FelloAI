"""Source precedence, conflict tolerances and account status (FR-005, spec edge cases)."""

import pandas as pd

from worklist.ingest.facts import conflicts_for, resolve_facts

CUTOFF = pd.Timestamp("2026-06-01", tz="UTC")


def _ev(account, etype, day, **fields):
    return {"account_id": account, "event_type": etype, "occurred_at": pd.Timestamp(day, tz="UTC"), **fields}


def _frame(rows):
    cols = ["account_id", "event_type", "occurred_at", "owner_email", "segment", "arr", "renewal_date", "mrr",
            "current_period_end", "cancel_at_period_end", "stage"]
    return pd.DataFrame(rows, columns=cols)


def _accounts(*ids, kind="term"):
    return pd.DataFrame({"contract_type": [kind] * len(ids), "master_segment": ["smb"] * len(ids)}, index=list(ids))


def test_billing_wins_for_arr_and_renewal_crm_for_owner():
    ev = _frame([
        _ev("A", "account.upserted", "2026-01-01", owner_email="csm@x", segment="mid", arr=90000, renewal_date="2026-09-01"),
        _ev("A", "subscription.updated", "2026-01-02", mrr=10000, current_period_end="2026-09-03", cancel_at_period_end=False),
    ])
    f = resolve_facts(ev, _accounts("A"), {"csm@x": "U1"}, CUTOFF).loc["A"]
    assert f["arr"] == 120000 and f["renewal_date"] == pd.Timestamp("2026-09-03")
    assert f["owner_user_id"] == "U1" and f["segment"] == "mid" and f["status"] == "active"


def test_conflicts_only_beyond_tolerance():
    base = [_ev("A", "subscription.updated", "2026-01-02", mrr=10000, current_period_end="2026-09-01",
                cancel_at_period_end=False)]
    within = _frame(base + [_ev("A", "account.upserted", "2026-01-01", arr=123000, renewal_date="2026-09-06")])
    beyond = _frame(base + [_ev("A", "account.upserted", "2026-01-01", arr=110000, renewal_date="2026-09-20")])
    assert conflicts_for(resolve_facts(within, _accounts("A"), {}, CUTOFF)) == []
    facts = {c["fact"] for c in conflicts_for(resolve_facts(beyond, _accounts("A"), {}, CUTOFF))}
    assert facts == {"arr", "renewal_date"}


def test_status_overdue_then_churned_after_grace_and_outcome_unknown():
    ev = _frame([_ev("A", "subscription.updated", "2025-06-01", mrr=1000, current_period_end="2026-05-01",
                     cancel_at_period_end=False)])
    assert resolve_facts(ev, _accounts("A"), {}, CUTOFF).loc["A", "status"] == "renewal_overdue"
    late = pd.Timestamp("2026-08-15", tz="UTC")
    assert resolve_facts(ev, _accounts("A"), {}, late).loc["A", "status"] == "churned"
    won = _frame(ev.to_dict("records") + [_ev("A", "opportunity.updated", "2026-05-03", stage="closed_won")])
    assert resolve_facts(won, _accounts("A"), {}, CUTOFF).loc["A", "status"] == "outcome_unknown"
