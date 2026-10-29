"""A small, fast scored world for integration and security tests.

Accounts get real events through the ingest path, a tiny LightGBM model is registered as the
approved global model (with identity calibration per tenant), and scoring runs via the pipeline.
"""

from datetime import UTC, date, datetime, timedelta

import lightgbm as lgb
import numpy as np
import pandas as pd
from sqlalchemy import text

from tests.conftest import make_account, make_tenant, make_user
from worklist.db import tables as t
from worklist.db.engine import job_engine, operator_conn, tenant_scope
from worklist.ingest.feed import ingest_envelopes
from worklist.ingest.identity import AccountIndex
from worklist.model import registry
from worklist.model.train import FittedModel
from worklist.signals.registry import FEATURES, SIGNAL_DEFS_VERSION

TODAY = date(2026, 6, 1)


def set_clock(day: date) -> None:
    with operator_conn() as conn:
        conn.execute(text('INSERT INTO sim_clock (id, "current_date") VALUES (1, :d) '
                          'ON CONFLICT (id) DO UPDATE SET "current_date" = :d'), {"d": day})


def tiny_model() -> FittedModel:
    """Risk rises with payment failures and high/urgent tickets (enough to give distinct bands)."""
    rng = np.random.default_rng(0)
    X = pd.DataFrame(rng.normal(size=(600, len(FEATURES))), columns=FEATURES)
    X["payment_failures_90d"] = rng.integers(0, 4, 600)
    X["high_urgent_tickets_30d"] = rng.integers(0, 4, 600)
    logit = -3 + 1.2 * X["payment_failures_90d"] + 0.8 * X["high_urgent_tickets_30d"]
    y = (rng.random(600) < 1 / (1 + np.exp(-logit))).astype(int)
    booster = lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 4, "min_data_in_leaf": 10},
                        lgb.Dataset(X, y), num_boost_round=30)
    return FittedModel("lightgbm", booster)


def approve_tiny_model(tenants: list[dict]) -> object:
    with job_engine().begin() as conn:
        model_id = registry.insert_version(
            conn, scope="global", tenant_id=None, model=tiny_model(), history_window="full", windows={},
            signal_defs_version=SIGNAL_DEFS_VERSION, metrics={"overall": {}}, status="candidate", reason="test")
        registry.approve(conn, model_id, "global", None)
    for tenant in tenants:
        with tenant_scope(job_engine(), tenant["id"]) as conn:
            registry.save_calibrator(conn, tenant["id"], model_id, {"method": "platt", "params": {"a": 1.0, "b": 0.0}},
                                     {"churned_accounts": 0})
    return model_id


def _at(day: date, hour: int = 10) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def account_events(acc_key: str, owner_email: str | None, renewal: date, failures: int, tickets: int,
                   start: date) -> list[dict]:
    """A plausible event history for one account, received before `start`."""
    events = []

    def env(rid, source, etype, day, **payload):
        events.append({"source": source, "source_record_id": f"{acc_key}-{rid}", "event_type": etype,
                       "occurred_at": _at(day).isoformat(), "ingested_at": _at(day, 11), "payload": payload})

    env("sub", "billing", "subscription.updated", start - timedelta(days=200), stripe_customer_id=f"cus_{acc_key}",
        mrr=4000, current_period_end=renewal.isoformat(), cancel_at_period_end=False)
    env("inv", "billing", "invoice.paid", start - timedelta(days=200), invoice_id=f"in_{acc_key}",
        stripe_customer_id=f"cus_{acc_key}", amount=48000, currency="usd")
    env("crm", "crm", "account.upserted", start - timedelta(days=150), sf_account_id=f"sf_{acc_key}", name=acc_key,
        owner_email=owner_email, segment="smb", arr=48000, renewal_date=renewal.isoformat())
    for i in range(6):
        env(f"u{i}", "product", "usage.summary", start - timedelta(days=5 + 30 * i), product_org_id=f"org_{acc_key}",
            active_users=20, sessions=300)
    env("seats", "product", "seats.changed", start - timedelta(days=190), product_org_id=f"org_{acc_key}",
        seats_purchased=30, seats_assigned=25)
    for i in range(failures):
        env(f"f{i}", "billing", "invoice.payment_failed", start - timedelta(days=10 + i), invoice_id=f"inf_{acc_key}",
            stripe_customer_id=f"cus_{acc_key}", attempt=i + 1)
    for i in range(tickets):
        env(f"t{i}", "support", "ticket.created", start - timedelta(days=3 + i), ticket_id=f"t_{acc_key}_{i}",
            zendesk_org_id=f"zd_{acc_key}", requester_email=f"a@{acc_key}.test", priority="high")
    env("mail", "email", "email.sent", start - timedelta(days=20), from_domain="vendor.demo",
        to_domain=f"{acc_key}.test", direction="outbound", is_csm=True)
    # Something recent from every source, so no feed looks stale at `start`.
    yesterday = start - timedelta(days=1)
    env("in", "email", "email.received", yesterday, from_domain=f"{acc_key}.test", to_domain="vendor.demo",
        direction="inbound")
    env("opp", "crm", "opportunity.updated", yesterday, sf_account_id=f"sf_{acc_key}", stage="renewal_open")
    env("tl", "support", "ticket.created", yesterday, ticket_id=f"t_{acc_key}_low", zendesk_org_id=f"zd_{acc_key}",
        requester_email=f"a@{acc_key}.test", priority="low")
    env("ul", "product", "usage.summary", yesterday, product_org_id=f"org_{acc_key}", active_users=20, sessions=290)
    env("inv2", "billing", "invoice.paid", yesterday, invoice_id=f"in2_{acc_key}", stripe_customer_id=f"cus_{acc_key}",
        amount=10, currency="usd")
    return events


def build_world(tenant_name: str = "Acme Tenant", csm_count: int = 2, accounts_per_csm: int = 4,
                with_model: bool = True, unassigned: int = 1) -> dict:
    """A tenant with a manager, CSMs and accounts (risk spread across them), ingested up to TODAY."""
    tenant = make_tenant(tenant_name, high_band_size=2)
    manager = make_user(tenant, role="cs_manager", name="Manager")
    csms = [make_user(tenant, name=f"CSM {i}") for i in range(csm_count)]
    accounts, envelopes = [], []
    owners = [c for c in csms for _ in range(accounts_per_csm)] + [None] * unassigned
    for i, owner in enumerate(owners):
        key = f"{tenant_name.split()[0].lower()}{i}"
        acc = make_account(tenant, owner, name=f"{tenant_name} account {i}", primary_domain=f"{key}.test",
                           contract_start=date(2024, 1, 1))
        with operator_conn() as conn:
            for source, prefix in (("billing", "cus_"), ("crm", "sf_"), ("product", "org_"), ("support", "zd_")):
                conn.execute(t.account_source_id.insert().values(tenant_id=tenant["id"], account_id=acc["id"],
                                                                 source=source, external_id=f"{prefix}{key}"))
        renewal = TODAY + timedelta(days=20 + 5 * i) if i % 5 else TODAY + timedelta(days=200)
        envelopes += account_events(key, owner["email"] if owner else None, renewal, failures=i % 4,
                                    tickets=(i * 3) % 4, start=TODAY)
        accounts.append({**acc, "key": key, "renewal": renewal})
    with tenant_scope(job_engine(), tenant["id"]) as conn:
        ingest_envelopes(conn, tenant["id"], envelopes, AccountIndex(conn), None, {})
    return {"tenant": tenant, "manager": manager, "csms": csms, "accounts": accounts}


def score_world(*worlds: dict, day: date = TODAY, run_type: str = "backfill",
                steps: tuple[str, ...] = ("score", "facts")) -> None:
    from worklist.agent.pipeline import run_tenant_cutoff

    set_clock(day)
    for w in worlds:
        assert run_tenant_cutoff(w["tenant"]["id"], day, run_type, steps) == "succeeded"


def scored_worlds(n: int = 2, with_model: bool = True) -> list[dict]:
    worlds = [build_world(name) for name in ("Acme Tenant", "Bravo Tenant", "Cobalt Tenant")[:n]]
    if with_model:
        approve_tiny_model([w["tenant"] for w in worlds])
    score_world(*worlds)
    return worlds


def add_alert_and_draft(world: dict, account: dict) -> dict:
    """Inserts an alert (with a draft) for `account` directly, for access tests."""
    from datetime import datetime as _dt

    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        score = conn.execute(text("SELECT id, run_id FROM account_score WHERE account_id = :a ORDER BY cutoff_at DESC "
                                  "LIMIT 1"), {"a": account["id"]}).first()
        alert_id, draft_id = t.new_id(), t.new_id()
        conn.execute(t.alert.insert().values(
            id=alert_id, tenant_id=world["tenant"]["id"], run_id=score.run_id, account_id=account["id"],
            raised_for_user_id=account["owner_user_id"], score_id=score.id, priority=2, trigger="entered_top_k",
            trigger_detail={"current_rank": 1}, fingerprint="entered_top_k", dedupe_key=f"test-{alert_id}",
            evidence=[{"fact_id": "f1", "kind": "support", "text": "2 high-priority tickets in 7 days",
                       "raw_event_ids": []}], created_at=_dt.now(UTC)))
        conn.execute(t.outreach_draft.insert().values(
            id=draft_id, tenant_id=world["tenant"]["id"], alert_id=alert_id, generator="template",
            fallback_reason="disabled", evidence_hash="x", internal_summary="Summary", email_subject="Checking in",
            email_body="Hi there", facts_used=["f1"]))
    return {"alert_id": alert_id, "draft_id": draft_id, "score_id": score.id}


def ingest(world: dict, envelopes: list[dict]) -> None:
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        ingest_envelopes(conn, world["tenant"]["id"], envelopes, AccountIndex(conn), None, {})


def push_risk(world: dict, account: dict, day: date, n: int = 4) -> None:
    """Payment failures and high tickets received just before `day` — enough to enter the high band."""
    key = account["key"]
    events = []
    for i in range(n):
        events.append({"source": "billing", "source_record_id": f"{key}-push-f{day}-{i}",
                       "event_type": "invoice.payment_failed", "occurred_at": _at(day - timedelta(days=1), 9).isoformat(),
                       "ingested_at": _at(day - timedelta(days=1), 10), "payload": {
                           "invoice_id": f"push-{key}", "stripe_customer_id": f"cus_{key}", "attempt": i + 1}})
        events.append({"source": "support", "source_record_id": f"{key}-push-t{day}-{i}", "event_type": "ticket.created",
                       "occurred_at": _at(day - timedelta(days=1), 9).isoformat(),
                       "ingested_at": _at(day - timedelta(days=1), 10), "payload": {
                           "ticket_id": f"push-{key}-{day}-{i}", "zendesk_org_id": f"zd_{key}",
                           "requester_email": f"a@{key}.test", "priority": "high"}})
    ingest(world, events)


def alerts_for(world: dict) -> list[dict]:
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM alert ORDER BY created_at")).mappings()]
