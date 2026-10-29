"""Alert triggers and suppression (spec Definitions: material change, new material change)."""

from datetime import timedelta

from sqlalchemy import text

from tests.helpers import TODAY, alerts_for, approve_tiny_model, build_world, push_risk, score_world
from worklist.db import tables as t
from worklist.db.engine import operator_conn

STEPS = ("score", "alerts")


def _world():
    world = build_world(csm_count=1, accounts_per_csm=6, unassigned=0)
    approve_tiny_model([world["tenant"]])
    score_world(world, day=TODAY)
    return world


def _calm_account(world):
    from worklist.db.engine import job_engine, tenant_scope

    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        low = conn.execute(text("SELECT account_id FROM account_score WHERE state = 'scored' AND band <> 'high' "
                                "ORDER BY probability LIMIT 1")).scalar()
    return next(a for a in world["accounts"] if a["id"] == low)


def test_entering_high_band_raises_one_alert_and_no_repeat():
    world = _world()
    acc = _calm_account(world)
    push_risk(world, acc, TODAY + timedelta(days=1))
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    raised = [a for a in alerts_for(world) if a["account_id"] == acc["id"]]
    assert [a["trigger"] for a in raised] in (["entered_top_k"], ["severe_event_near_renewal"], ["risk_jump"])
    score_world(world, day=TODAY + timedelta(days=2), run_type="nightly", steps=STEPS)
    assert len([a for a in alerts_for(world) if a["account_id"] == acc["id"]]) == 1


def test_no_change_no_alert():
    world = _world()
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    assert alerts_for(world) == []


def test_urgent_ticket_near_renewal_is_severe():
    world = _world()
    acc = next(a for a in world["accounts"] if (a["renewal"] - TODAY).days <= 60)
    day = TODAY + timedelta(days=1)
    from tests.helpers import _at, ingest

    ingest(world, [{"source": "support", "source_record_id": "urgent-1", "event_type": "ticket.created",
                    "occurred_at": _at(TODAY, 3).isoformat(), "ingested_at": _at(TODAY, 4),
                    "payload": {"ticket_id": "urgent-1", "zendesk_org_id": f"zd_{acc['key']}",
                                "requester_email": f"x@{acc['key']}.test", "priority": "urgent"}}])
    score_world(world, day=day, run_type="nightly", steps=STEPS)
    severe = [a for a in alerts_for(world) if a["trigger"] == "severe_event_near_renewal"]
    assert any(a["account_id"] == acc["id"] and a["trigger_detail"]["event"] == "urgent_ticket" for a in severe)


def test_snooze_suppresses_same_trigger_but_not_new_type():
    world = _world()
    acc = _calm_account(world)
    with operator_conn() as conn:
        conn.execute(t.csm_action.insert().values(
            id=t.new_id(), tenant_id=world["tenant"]["id"], account_id=acc["id"], user_id=world["csms"][0]["id"],
            action="snoozed", snooze_until=TODAY + timedelta(days=30),
            fingerprint={"trigger_types": ["entered_top_k", "risk_jump"]}, probability_at_action=0.9))
    push_risk(world, acc, TODAY + timedelta(days=1))
    score_world(world, day=TODAY + timedelta(days=1), run_type="nightly", steps=STEPS)
    triggers = {a["trigger"] for a in alerts_for(world) if a["account_id"] == acc["id"]}
    assert "entered_top_k" not in triggers and "risk_jump" not in triggers
    # A severe event is a different kind of change, so it overrides the snooze (if renewal is near).
    if (acc["renewal"] - TODAY).days <= 60:
        assert triggers == {"severe_event_near_renewal"}
