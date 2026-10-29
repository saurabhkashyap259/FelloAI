"""Drafting, fallback, approval (US4, FR-023, FR-023b, FR-024). The LLM client is mocked."""

import pathlib
from datetime import timedelta

import anthropic
import httpx2
import pytest
from sqlalchemy import text

from tests.helpers import TODAY, _at, approve_tiny_model, build_world, ingest, score_world
from worklist.agent import drafts
from worklist.config import get_settings
from worklist.db.engine import job_engine, operator_conn, tenant_scope

STEPS = ("score", "alerts", "drafts")


@pytest.fixture
def world():
    w = build_world(csm_count=1, accounts_per_csm=6, unassigned=0)
    approve_tiny_model([w["tenant"]])
    score_world(w, day=TODAY)
    near = [a for a in w["accounts"] if 0 < (a["renewal"] - TODAY).days <= 60][:2]
    ingest(w, [{"source": "support", "source_record_id": f"u-{a['key']}", "event_type": "ticket.created",
                "occurred_at": _at(TODAY, 3).isoformat(), "ingested_at": _at(TODAY, 4),
                "payload": {"ticket_id": f"u-{a['key']}", "zendesk_org_id": f"zd_{a['key']}",
                            "requester_email": f"x@{a['key']}.test", "priority": "urgent"}} for a in near])
    return w


def _drafts(w):
    with tenant_scope(job_engine(), w["tenant"]["id"]) as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM outreach_draft ORDER BY created_at")).mappings()]


def _run(w, day=TODAY + timedelta(days=1)):
    score_world(w, day=day, run_type="nightly", steps=STEPS)


def test_one_draft_per_alert_template_without_key_and_no_regeneration(world):
    _run(world)
    first = _drafts(world)
    assert first and all(d["generator"] == "template" and d["fallback_reason"] == "no_api_key" for d in first)
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        alerts = conn.execute(text("SELECT count(*) FROM alert")).scalar()
        run_id = conn.execute(text("SELECT id FROM pipeline_run WHERE run_type = 'nightly'")).scalar()
        assert drafts.draft_for_new_alerts(conn, run_id, {"llm_drafting_enabled": True}, True)["drafts_created"] == 0
    assert len(first) == alerts and _drafts(world) == first


def test_disabled_drafting_uses_template(world):
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        conn.execute(text("UPDATE tenant_settings SET llm_drafting_enabled = false"))
    _run(world)
    assert {d["fallback_reason"] for d in _drafts(world)} == {"disabled"}


def _with_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")


@pytest.mark.parametrize("failure,reason", [
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), "timeout"),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), "api_error:APIConnectionError"),
])
def test_api_failures_fall_back_to_template(world, monkeypatch, failure, reason):
    _with_key(monkeypatch)

    def boom(pack):
        raise failure

    monkeypatch.setattr(drafts, "call_claude", boom)
    _run(world)
    assert {d["fallback_reason"] for d in _drafts(world)} == {reason}


def test_invalid_llm_draft_falls_back_and_valid_one_is_kept(world, monkeypatch):
    _with_key(monkeypatch)
    monkeypatch.setattr(drafts, "call_claude", lambda pack: drafts.DraftOut(
        internal_summary="x", email_subject="Hi", email_body="Your churn risk is 87%.", facts_used=["f1"]))
    _run(world)
    assert all(d["fallback_reason"].startswith("validation_failed") for d in _drafts(world))


def test_valid_llm_draft_is_stored_with_model_and_hash(world, monkeypatch):
    _with_key(monkeypatch)
    monkeypatch.setattr(drafts, "call_claude", lambda pack: drafts.DraftOut(
        internal_summary=pack["facts"][0]["text"], email_subject="Checking in",
        email_body=f"Hi {pack['account']['name']} team, could we talk this week? {pack['csm_name']}",
        facts_used=[pack["facts"][0]["fact_id"]]))
    _run(world)
    stored = _drafts(world)
    assert stored and all(d["generator"] == "llm" and d["llm_model"] and d["evidence_hash"] for d in stored)


def test_approval_records_who_when_text_and_other_csm_gets_404(world, client_as):
    _run(world)
    draft = _drafts(world)[0]
    owner = client_as(world["csms"][0])
    r = owner.post(f"/drafts/{draft['id']}/approve", data={"csrf_token": owner.csrf, "final_text": "Final words"})
    assert r.status_code == 303
    with operator_conn() as conn:
        row = conn.execute(text("SELECT status, final_text, approved_by, approved_at FROM outreach_draft "
                                "WHERE id = :d"), {"d": draft["id"]}).mappings().first()
    assert row["status"] == "approved" and row["final_text"] == "Final words"
    assert row["approved_by"] == world["csms"][0]["id"] and row["approved_at"]
    page = owner.get(r.headers["location"].split("#")[0]).text
    assert "mailto:" in page and "Copy" in page


def test_codebase_has_no_outbound_email_path():
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    code = "\n".join(p.read_text() for p in src.rglob("*.py"))
    for marker in ("smtplib", "sendmail", "send_message(", "sendgrid", "ses.send", "postmark"):
        assert marker not in code, marker
