"""`worklist generate`: builds the synthetic world and loads it through the real ingestion path.

Account master data, source-ID maps and ground-truth outcomes are written directly (they stand in
for the CRM integration and the outcome log). Every event goes to `sim.pending_feed_event`; the
backlog up to the demo start date is then delivered by the same feed code the nightly run uses.
"""

import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import text

from worklist.db import tables as t
from worklist.db.engine import job_engine, list_tenants, operator_conn, tenant_scope
from worklist.ingest.facts import apply_facts, resolve_facts
from worklist.ingest.feed import deliver_feed
from worklist.log import log
from worklist.seed import tenant_slug
from worklist.signals.events import as_of, load_accounts, load_events, load_users
from worklist.synth.generate import demo_start, generate, outcome_rows, scaled_config
from worklist.synth.profile import build_profile

BATCH = 5000


def cutoff_at(day) -> datetime:
    """Cutoff for sim date D: data received before D 00:00 UTC."""
    return datetime.combine(day, time(0), tzinfo=UTC)


DATA_TABLES = ["outreach_draft", "alert", "csm_action", "account_score", "tenant_calibrator", "signal_snapshot",
               "model_version", "pipeline_run", "quarantined_event", "event_account", "raw_event", "renewal_outcome",
               "account_fact_conflict", "account_source_id", "account", "sim_clock", "sim.pending_feed_event"]


def reset_data() -> None:
    """Operator-only: removes all generated and derived data, keeping tenants and users."""
    with operator_conn() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(DATA_TABLES) + " CASCADE"))
        conn.execute(text("UPDATE tenant_settings SET needs_attention = false"))


def generate_and_load(seed: int, accounts: int, events: int, history_days: int, feed_days: int, out: str,
                      reset: bool = False) -> int:
    if reset:
        reset_data()
    cfg = scaled_config(seed, accounts, events, history_days, feed_days)
    tenants = {r["name"]: r for r in list_tenants()}
    missing = [s.name for s in cfg.tenants if s.name not in tenants]
    if missing:
        log("generate.failed", reason="tenants missing; run `worklist seed-demo` first", missing=missing)
        return 1
    with operator_conn() as conn:
        if conn.execute(text("SELECT count(*) FROM account")).scalar():
            log("generate.skipped", reason="data already loaded")
            return 3

    csm_emails, vendor_domains, tenant_ids = [], [], []
    with operator_conn() as conn:
        for spec in cfg.tenants:
            tid = tenants[spec.name]["id"]
            tenant_ids.append(tid)
            emails = conn.execute(text("SELECT email FROM app_user WHERE tenant_id = :t AND role = 'csm' "
                                       "ORDER BY email"), {"t": tid}).scalars().all()
            csm_emails.append(list(emails))
            vendor_domains.append(f"{tenant_slug(spec.name)}.demo")

    log("generate.started", seed=seed, accounts=accounts, events=events)
    ds = generate(cfg, csm_emails, vendor_domains)
    start = demo_start(cfg)
    account_ids = {acc.idx: t.new_id() for acc in ds.accounts}
    outcomes = outcome_rows(ds)

    for t_idx, tenant_id in enumerate(tenant_ids):
        with tenant_scope(job_engine(), tenant_id) as conn:
            accs = [a for a in ds.accounts if a.tenant_idx == t_idx]
            conn.execute(t.account.insert(), [{
                "id": account_ids[a.idx], "tenant_id": tenant_id, "name": a.name, "primary_domain": a.domain,
                "segment": a.segment, "contract_type": a.contract_type, "contract_start": a.contract_start,
                "status": "active"} for a in accs])
            conn.execute(t.account_source_id.insert(), [
                {"tenant_id": tenant_id, "account_id": account_ids[a.idx], "source": s, "external_id": ext}
                for a in accs for s, ext in a.ids.items()])
            rows = [{"id": t.new_id(), "tenant_id": tenant_id, "account_id": account_ids[o["acc"]],
                     "renewal_date": o["renewal_date"], "outcome": o["outcome"], "arr_before": o["arr_before"],
                     "arr_after": o["arr_after"], "is_silent_churn": o["is_silent_churn"],
                     "recorded_at": cutoff_at(o["renewal_date"] + timedelta(days=o["grace_days"]))}
                    for o in outcomes if ds.accounts[o["acc"]].tenant_idx == t_idx]
            if rows:
                conn.execute(t.renewal_outcome.insert(), rows)
            feed = [{"id": t.new_id(), "tenant_id": tenant_id, "source": e["source"], "source_record_id": e["rid"],
                     "event_type": e["type"], "occurred_at_raw": e["occurred_raw"], "ingested_at": e["ingested"],
                     "payload": e["payload"]} for e in ds.events if e["t"] == t_idx]
            for i in range(0, len(feed), BATCH):
                conn.execute(t.pending_feed_event.insert(), feed[i:i + BATCH])
        log("generate.tenant_written", tenant=cfg.tenants[t_idx].name, accounts=len(accs), feed_events=len(feed))

    with job_engine().begin() as conn:
        conn.execute(text("INSERT INTO sim_clock (id, \"current_date\") VALUES (1, :d) "
                          "ON CONFLICT (id) DO UPDATE SET \"current_date\" = :d"), {"d": start})

    # Deliver the historical backlog through the ingestion path, then resolve facts as of the start.
    for tenant_id in tenant_ids:
        with tenant_scope(job_engine(), tenant_id) as conn:
            tz = conn.execute(text("SELECT source_timezones FROM tenant_settings")).scalar() or {}
            counts = deliver_feed(conn, tenant_id, cutoff_at(start), None, tz)
            events_df = load_events(conn, cutoff_at(start))
            facts = resolve_facts(as_of(events_df, _ts(start)), load_accounts(conn), load_users(conn), _ts(start))
            conflicts = apply_facts(conn, facts, None, tenant_id)
        log("generate.backlog_delivered", tenant=str(tenant_id), conflicts=conflicts, **counts)

    profile = build_profile(ds)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(profile, indent=2, default=str))
    log("generate.done", sim_clock=start.isoformat(), events=len(ds.events), report=out)
    return 0


def _ts(day):
    import pandas as pd

    return pd.Timestamp(cutoff_at(day))
