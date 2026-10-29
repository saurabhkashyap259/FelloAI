"""The per-tenant run core, backfill, and the unattended nightly run (FR-014–FR-016, research R9).

One `pipeline_run` row per (tenant, run type, cutoff): a succeeded run is a no-op on re-trigger,
a failed/partial one is resumed. All steps for one tenant run in one transaction, so a failure
leaves nothing half-written and other tenants are unaffected.
"""

import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import text

from worklist.db import tables as t
from worklist.db.engine import job_engine, list_tenants, tenant_scope
from worklist.ingest.facts import apply_facts
from worklist.ingest.feed import deliver_feed, retry_quarantine
from worklist.log import log
from worklist.model.score import score_tenant, settings_for
from worklist.signals.events import load_events
from worklist.synth.writer import cutoff_at

EXIT_OK, EXIT_FAILED, EXIT_PARTIAL, EXIT_SKIPPED = 0, 1, 2, 3
NIGHTLY_LOCK = 4_242_001
MAX_CATCH_UP = 3

# Test hook: a callable(tenant_id, cutoff_day) that raises to simulate a tenant failure.
failure_injector: Callable | None = None


def sim_today() -> date:
    with job_engine().connect() as conn:
        return conn.execute(text('SELECT "current_date" FROM sim_clock WHERE id = 1')).scalar()


def _claim_run(tenant_id, run_type: str, cutoff_day: date, is_catch_up: bool):
    """Creates or resumes the run row in its own transaction. Returns (run_id, already_succeeded)."""
    with tenant_scope(job_engine(), tenant_id) as conn:
        row = conn.execute(text("SELECT id, status FROM pipeline_run WHERE run_type = :rt AND cutoff_at = :c"),
                           {"rt": run_type, "c": cutoff_at(cutoff_day)}).first()
        if row and row.status == "succeeded":
            return row.id, True
        if row:
            conn.execute(text("UPDATE pipeline_run SET status = 'running', started_at = now(), error = NULL, "
                              "is_catch_up = :cu WHERE id = :id"), {"id": row.id, "cu": is_catch_up})
            return row.id, False
        run_id = t.new_id()
        conn.execute(t.pipeline_run.insert().values(
            id=run_id, tenant_id=tenant_id, run_type=run_type, cutoff_at=cutoff_at(cutoff_day), status="running",
            started_at=datetime.now(UTC), is_catch_up=is_catch_up))
        return run_id, False


def run_tenant_cutoff(tenant_id, cutoff_day: date, run_type: str, steps: tuple[str, ...],
                      is_catch_up: bool = False, use_llm: bool = True) -> str:
    """Runs `steps` for one tenant and cutoff. Returns 'succeeded', 'failed' or 'skipped'."""
    run_id, done = _claim_run(tenant_id, run_type, cutoff_day, is_catch_up)
    if done:
        return "skipped"
    cutoff = pd.Timestamp(cutoff_at(cutoff_day))
    counts: Counter = Counter()
    try:
        with tenant_scope(job_engine(), tenant_id) as conn:
            if failure_injector is not None:
                failure_injector(tenant_id, cutoff_day)
            settings = settings_for(conn)
            if "feed" in steps:
                counts += deliver_feed(conn, tenant_id, cutoff.to_pydatetime(), run_id, settings["source_timezones"])
            if "quarantine" in steps:
                counts += retry_quarantine(conn, cutoff.to_pydatetime(), run_id)
            events = load_events(conn, cutoff.to_pydatetime())
            result = score_tenant(conn, tenant_id, cutoff, run_id, settings, events)
            if "facts" in steps:
                counts["conflicts_recorded"] = apply_facts(conn, result.snapshot.facts, run_id, tenant_id)
            extra = {}
            if "alerts" in steps:
                from worklist.agent.alerts import raise_alerts

                extra.update(raise_alerts(conn, tenant_id, run_id, cutoff, result, events, settings))
            if "drafts" in steps:
                from worklist.agent.drafts import draft_for_new_alerts

                extra.update(draft_for_new_alerts(conn, run_id, settings, use_llm))
            final_counts = {**{k: int(v) for k, v in counts.items()}, **result.counts, **extra}
            conn.execute(text("UPDATE pipeline_run SET status = 'succeeded', finished_at = now(), counts = :c, "
                              "model_version_id = :m WHERE id = :id"),
                         {"c": json.dumps(final_counts, default=str), "m": result.model_version_id, "id": run_id})
        log("run.tenant_succeeded", tenant=str(tenant_id), run_type=run_type, cutoff=cutoff_day.isoformat(),
            **{k: v for k, v in final_counts.items() if not isinstance(v, list)})
        return "succeeded"
    except Exception as exc:  # noqa: BLE001 — one tenant's failure must not stop the others
        with tenant_scope(job_engine(), tenant_id) as conn:
            conn.execute(text("UPDATE pipeline_run SET status = 'failed', finished_at = now(), error = :e "
                              "WHERE id = :id"), {"e": f"{type(exc).__name__}: {exc}"[:2000], "id": run_id})
        log("run.tenant_failed", tenant=str(tenant_id), run_type=run_type, cutoff=cutoff_day.isoformat(),
            error=f"{type(exc).__name__}: {exc}")
        return "failed"


def _resolve_day(value: str, today: date) -> date:
    if value == "today":
        return today
    if value.startswith("-") and value.endswith("d"):
        return today - timedelta(days=int(value[1:-1]))
    return date.fromisoformat(value)


def backfill(from_: str, to: str, every_days: int) -> int:
    """Scores past cutoffs to build risk history (never raises alerts). Data must already be ingested."""
    today = sim_today()
    start, end = _resolve_day(from_, today), _resolve_day(to, today)
    statuses: Counter[str] = Counter()
    day = start
    while day <= end:
        for tenant in list_tenants():
            statuses[run_tenant_cutoff(tenant["id"], day, "backfill", ("score",))] += 1
        day += timedelta(days=every_days)
    log("backfill.done", **statuses)
    return EXIT_PARTIAL if statuses["failed"] else EXIT_OK


NIGHTLY_STEPS = ("feed", "quarantine", "facts", "score", "alerts", "drafts")


def _tenant_by_ref(ref: str | None) -> list[dict]:
    tenants = list_tenants()
    if ref is None:
        return tenants
    return [x for x in tenants if x["name"] == ref or str(x["id"]) == ref]


def missed_cutoffs(tenant_id, before: date) -> list[date]:
    """Failed/partial nightly cutoffs since the tenant's last succeeded nightly run, oldest first."""
    with tenant_scope(job_engine(), tenant_id) as conn:
        last_ok = conn.execute(text("SELECT max(cutoff_at) FROM pipeline_run WHERE run_type = 'nightly' "
                                    "AND status = 'succeeded'")).scalar()
        rows = conn.execute(text(
            "SELECT cutoff_at FROM pipeline_run WHERE run_type = 'nightly' AND status IN ('failed', 'partial', "
            "'running') AND cutoff_at < :b AND (CAST(:ok AS timestamptz) IS NULL OR cutoff_at > :ok) "
            "ORDER BY cutoff_at"),
            {"b": cutoff_at(before), "ok": last_ok}).scalars().all()
    return [r.date() for r in rows]


def nightly(cutoff: date | None = None, tenant: str | None = None, advance: bool = True, use_llm: bool = True) -> int:
    """Unattended nightly run: lock, advance the sim clock once, then per tenant catch up and process."""
    with job_engine().connect() as lock_conn:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": NIGHTLY_LOCK}).scalar():
            log("nightly.skipped", reason="another run holds the lock")
            return EXIT_SKIPPED
        try:
            if advance:
                with job_engine().begin() as conn:
                    conn.execute(text('UPDATE sim_clock SET "current_date" = "current_date" + 1 WHERE id = 1'))
            day = cutoff or sim_today()
            outcomes: Counter[str] = Counter()
            for ten in _tenant_by_ref(tenant):
                try:
                    outcomes[_process_tenant(ten, day, use_llm)] += 1
                except Exception as exc:  # noqa: BLE001 — never let one tenant stop the others
                    log("nightly.tenant_error", tenant=ten["name"], error=f"{type(exc).__name__}: {exc}")
                    outcomes["failed"] += 1
            log("nightly.done", cutoff=day.isoformat(), **outcomes)
            if outcomes["failed"] and (outcomes["succeeded"] or outcomes["skipped"]):
                return EXIT_PARTIAL
            if outcomes["failed"]:
                return EXIT_FAILED
            if not outcomes["succeeded"]:
                return EXIT_SKIPPED
            return EXIT_OK
        finally:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": NIGHTLY_LOCK})


def _process_tenant(ten: dict, day: date, use_llm: bool) -> str:
    missed = missed_cutoffs(ten["id"], day)
    if len(missed) > MAX_CATCH_UP:
        with tenant_scope(job_engine(), ten["id"]) as conn:
            conn.execute(text("UPDATE tenant_settings SET needs_attention = true"))
        log("nightly.needs_attention", tenant=ten["name"], missed=len(missed))
        missed = []  # resume at the latest cutoff; the gap stays visible on the runs page
    for past in missed:
        if run_tenant_cutoff(ten["id"], past, "nightly", NIGHTLY_STEPS, is_catch_up=True, use_llm=use_llm) == "failed":
            _mark_blocked(ten["id"], day, past)
            return "failed"
    # needs_attention stays set until an operator clears it.
    return run_tenant_cutoff(ten["id"], day, "nightly", NIGHTLY_STEPS, use_llm=use_llm)


def _mark_blocked(tenant_id, day: date, blocking: date) -> None:
    """Records the new cutoff as failed when an older cutoff could not be caught up (it is missed too)."""
    run_id, done = _claim_run(tenant_id, "nightly", day, False)
    if done:
        return
    with tenant_scope(job_engine(), tenant_id) as conn:
        conn.execute(text("UPDATE pipeline_run SET status = 'failed', finished_at = now(), error = :e WHERE id = :id"),
                     {"e": f"not processed: catch-up of {blocking.isoformat()} failed", "id": run_id})
