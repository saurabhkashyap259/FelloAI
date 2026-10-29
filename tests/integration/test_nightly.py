"""The unattended nightly run: clock, idempotency, partial failure and catch-up (US3, FR-014–FR-018)."""

from datetime import timedelta

import pytest
from sqlalchemy import text

from tests.helpers import TODAY, alerts_for, approve_tiny_model, build_world, set_clock
from worklist.agent import pipeline
from worklist.db.engine import job_engine, operator_conn, tenant_scope


@pytest.fixture
def worlds():
    acme, bravo = build_world("Acme Tenant"), build_world("Bravo Tenant")
    approve_tiny_model([acme["tenant"], bravo["tenant"]])
    set_clock(TODAY)
    yield acme, bravo
    pipeline.failure_injector = None


def _clock():
    with operator_conn() as conn:
        return conn.execute(text('SELECT "current_date" FROM sim_clock')).scalar()


def _scores(world, day):
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        return sorted((str(r.account_id), r.state, r.probability, r.band) for r in conn.execute(text(
            "SELECT s.account_id, s.state, s.probability, s.band FROM account_score s JOIN pipeline_run r "
            "ON r.id = s.run_id WHERE r.run_type = 'nightly' AND r.cutoff_at::date = :d"), {"d": day}))


def _runs(world):
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        return [dict(r) for r in conn.execute(text(
            "SELECT cutoff_at::date AS day, status, is_catch_up FROM pipeline_run WHERE run_type = 'nightly' "
            "ORDER BY cutoff_at")).mappings()]


def test_clock_advances_once_and_rerun_is_identical(worlds):
    acme, _ = worlds
    assert pipeline.nightly(use_llm=False) == pipeline.EXIT_OK
    day = TODAY + timedelta(days=1)
    assert _clock() == day
    first, alerts = _scores(acme, day), alerts_for(acme)
    assert pipeline.nightly(cutoff=day, advance=False, use_llm=False) == pipeline.EXIT_SKIPPED
    assert _clock() == day and _scores(acme, day) == first and alerts_for(acme) == alerts


def test_partial_failure_then_catch_up(worlds):
    acme, bravo = worlds
    pipeline.failure_injector = lambda tenant_id, day: (_ for _ in ()).throw(RuntimeError("boom")) \
        if tenant_id == acme["tenant"]["id"] else None
    assert pipeline.nightly(use_llm=False) == pipeline.EXIT_PARTIAL
    assert _runs(acme)[-1]["status"] == "failed" and _runs(bravo)[-1]["status"] == "succeeded"
    pipeline.failure_injector = None
    assert pipeline.nightly(use_llm=False) == pipeline.EXIT_OK
    runs = _runs(acme)
    assert [(r["day"], r["status"], r["is_catch_up"]) for r in runs] == [
        (TODAY + timedelta(days=1), "succeeded", True), (TODAY + timedelta(days=2), "succeeded", False)]


def test_more_than_three_missed_sets_needs_attention(worlds):
    acme, _ = worlds
    pipeline.failure_injector = lambda tenant_id, day: (_ for _ in ()).throw(RuntimeError("down")) \
        if tenant_id == acme["tenant"]["id"] else None
    for _ in range(4):
        pipeline.nightly(use_llm=False)
    pipeline.failure_injector = None
    assert pipeline.nightly(use_llm=False) == pipeline.EXIT_OK
    with tenant_scope(job_engine(), acme["tenant"]["id"]) as conn:
        assert conn.execute(text("SELECT needs_attention FROM tenant_settings")).scalar() is True
    assert _runs(acme)[-1]["status"] == "succeeded" and _runs(acme)[-1]["day"] == TODAY + timedelta(days=5)


def test_lock_held_skips(worlds):
    with job_engine().connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": pipeline.NIGHTLY_LOCK})
        try:
            assert pipeline.nightly(use_llm=False) == pipeline.EXIT_SKIPPED
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": pipeline.NIGHTLY_LOCK})
    assert _clock() == TODAY


def test_stale_sources_give_low_confidence_not_no_activity(worlds):
    acme, _ = worlds
    assert pipeline.nightly(cutoff=TODAY + timedelta(days=6), advance=False, use_llm=False) == pipeline.EXIT_OK
    with tenant_scope(job_engine(), acme["tenant"]["id"]) as conn:
        counts = conn.execute(text("SELECT counts FROM pipeline_run WHERE run_type = 'nightly'")).scalar()
        tickets = conn.execute(text("SELECT signals->'tickets_30d' FROM signal_snapshot LIMIT 1")).scalar()
    assert set(counts["stale_sources"]) == {"crm", "email", "support", "product", "billing"}
    assert counts["low_confidence"] > 0 and tickets is None


def test_no_approved_model_scores_nothing_and_raises_no_model_alerts():
    world = build_world("Solo Tenant")
    set_clock(TODAY)
    assert pipeline.nightly(use_llm=False) == pipeline.EXIT_OK
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        states = set(conn.execute(text("SELECT state FROM account_score")).scalars())
    assert "scored" not in states and "no_model" in states
    assert not [a for a in alerts_for(world) if a["trigger"] != "severe_event_near_renewal"]
