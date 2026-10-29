"""Every displayed number traces to a stored record and source events (US2, SC-006)."""

import re

from sqlalchemy import text

from tests.helpers import TODAY, score_world, scored_worlds
from worklist.db.engine import job_engine, tenant_scope


def _scored(world):
    with tenant_scope(job_engine(), world["tenant"]["id"]) as conn:
        return dict(conn.execute(text("SELECT s.*, a.owner_user_id AS owner FROM account_score s JOIN account a "
                                      "ON a.id = s.account_id WHERE s.state = 'scored' AND s.reasons <> '[]'::jsonb "
                                      "AND a.owner_user_id IS NOT NULL ORDER BY s.probability DESC LIMIT 1"))
                    .mappings().first())


def test_displayed_probability_matches_stored_record(client_as):
    acme, _ = scored_worlds(2)
    score = _scored(acme)
    csm = next(c for c in acme["csms"] if c["id"] == score["owner"])
    client = client_as(csm)
    html = client.get(f"/accounts/{score['account_id']}").text
    assert f"{score['probability'] * 100:.0f}%" in html and str(score["run_id"]) in html
    api = client.get(f"/api/accounts/{score['account_id']}/scores/{score['id']}").json()
    assert api["score"]["probability"] == score["probability"]
    assert api["model_version"]["id"] == str(score["model_version_id"])
    assert api["signals"]["signal_defs_version"] == score["signal_defs_version"]


def test_drilldown_records_respect_cutoff_and_window(client_as):
    acme, _ = scored_worlds(2)
    score = _scored(acme)
    csm = next(c for c in acme["csms"] if c["id"] == score["owner"])
    signal = score["reasons"][0]["signal"]
    html = client_as(csm).get(f"/accounts/{score['account_id']}/signals/{signal}?score_id={score['id']}").text
    assert "Source records" in html
    received = re.findall(r"<td>(2026-\d\d-\d\d \d\d:\d\d UTC)</td><td>(2026-\d\d-\d\d \d\d:\d\d UTC)</td>", html)
    assert received and all(r < f"{TODAY.isoformat()} 00:00 UTC" for _, r in received)


def test_absence_signal_shows_window_and_latest_record(client_as):
    acme, _ = scored_worlds(2)
    score = _scored(acme)
    csm = next(c for c in acme["csms"] if c["id"] == score["owner"])
    html = client_as(csm).get(f"/accounts/{score['account_id']}/signals/days_since_csm_email"
                              f"?score_id={score['id']}").text
    assert "No qualifying records were found in the window searched" in html
    assert "email.sent" in html  # the most recent CSM email before the window


def test_old_score_unchanged_after_newer_run(client_as):
    acme, bravo = scored_worlds(2)
    score = _scored(acme)
    csm = next(c for c in acme["csms"] if c["id"] == score["owner"])
    client = client_as(csm)
    before = client.get(f"/api/accounts/{score['account_id']}/scores/{score['id']}").json()
    from datetime import timedelta

    score_world(acme, day=TODAY + timedelta(days=7))
    after = client.get(f"/api/accounts/{score['account_id']}/scores/{score['id']}").json()
    assert before == after
