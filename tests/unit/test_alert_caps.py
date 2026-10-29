"""Per-CSM alert caps and priority (FR-017a)."""

from datetime import timedelta

from sqlalchemy import text

from tests.helpers import TODAY, _at, alerts_for, approve_tiny_model, build_world, ingest, score_world
from worklist.db.engine import job_engine, tenant_scope

STEPS = ("score", "alerts")


def test_nightly_cap_keeps_highest_priority_and_counts_suppressed():
    world = build_world(csm_count=1, accounts_per_csm=8, unassigned=0)
    approve_tiny_model([world["tenant"]])
    score_world(world, day=TODAY)
    near = [a for a in world["accounts"] if 0 < (a["renewal"] - TODAY).days <= 60]
    assert len(near) >= 4
    ingest(world, [{"source": "support", "source_record_id": f"u-{a['key']}", "event_type": "ticket.created",
                    "occurred_at": _at(TODAY, 3).isoformat(), "ingested_at": _at(TODAY, 4),
                    "payload": {"ticket_id": f"u-{a['key']}", "zendesk_org_id": f"zd_{a['key']}",
                                "requester_email": f"x@{a['key']}.test", "priority": "urgent"}} for a in near])
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    alerts = alerts_for(world)
    assert len(alerts) == 3
    assert all(a["trigger"] == "severe_event_near_renewal" for a in alerts)
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        counts = conn.execute(text("SELECT counts FROM pipeline_run WHERE run_type = 'nightly'")).scalar()
    assert counts["alerts_suppressed"] >= len(near) - 3


def test_weekly_cap_limits_rolling_seven_days():
    world = build_world(csm_count=1, accounts_per_csm=8, unassigned=0)
    approve_tiny_model([world["tenant"]])
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        conn.execute(text("UPDATE tenant_settings SET alert_cap_nightly = 10, alert_cap_weekly = 2"))
    score_world(world, day=TODAY)
    near = [a for a in world["accounts"] if 0 < (a["renewal"] - TODAY).days <= 60]
    ingest(world, [{"source": "support", "source_record_id": f"u-{a['key']}", "event_type": "ticket.created",
                    "occurred_at": _at(TODAY, 3).isoformat(), "ingested_at": _at(TODAY, 4),
                    "payload": {"ticket_id": f"u-{a['key']}", "zendesk_org_id": f"zd_{a['key']}",
                                "requester_email": f"x@{a['key']}.test", "priority": "urgent"}} for a in near])
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    assert len(alerts_for(world)) == 2


def test_unassigned_account_alerts_go_to_managers():
    world = build_world(csm_count=1, accounts_per_csm=1, unassigned=3)
    approve_tiny_model([world["tenant"]])
    score_world(world, day=TODAY)
    near = [a for a in world["accounts"] if a["owner_user_id"] is None and 0 < (a["renewal"] - TODAY).days <= 60]
    ingest(world, [{"source": "support", "source_record_id": f"u-{a['key']}", "event_type": "ticket.created",
                    "occurred_at": _at(TODAY, 3).isoformat(), "ingested_at": _at(TODAY, 4),
                    "payload": {"ticket_id": f"u-{a['key']}", "zendesk_org_id": f"zd_{a['key']}",
                                "requester_email": f"x@{a['key']}.test", "priority": "urgent"}} for a in near])
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    alerts = alerts_for(world)
    assert alerts and all(a["raised_for_user_id"] is None for a in alerts)
