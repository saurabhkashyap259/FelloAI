"""Worklist page (US1, FR-019, FR-018)."""

import re
from datetime import UTC, datetime

from tests.helpers import scored_worlds
from worklist.db import tables as t
from worklist.db.engine import operator_conn


def _account_names(html: str) -> set[str]:
    return set(re.findall(r"<strong>([^<]*account \d+)</strong>", html))


def test_csm_sees_only_own_accounts_grouped_and_ordered(client_as):
    acme, bravo = scored_worlds(2)
    csm = acme["csms"][0]
    html = client_as(csm).get("/").text
    own = {a["name"] for a in acme["accounts"] if a["owner_user_id"] == csm["id"]}
    assert _account_names(html) == own
    assert not any("Bravo" in name for name in _account_names(html))
    groups = re.findall(r"<h2>([A-Za-z ]+) \(\d+\)</h2>", html)
    order = ["Renewal overdue", "Act this week", "Watch", "Healthy", "Insufficient data", "Snoozed",
             "Later renewals", "No model yet"]
    assert groups == [g for g in order if g in groups]
    for section in re.findall(r"<h2>(?:Act this week|Watch|Healthy) \(\d+\)</h2>(.*?)</table>", html, re.S):
        risks = [int(x) for x in re.findall(r"<strong>(\d+)%</strong>", section)]
        assert risks == sorted(risks, reverse=True)


def test_page_states_run_cutoff_and_model(client_as):
    acme, _ = scored_worlds(2)
    html = client_as(acme["csms"][0]).get("/").text
    assert "from data received before 2026-06-01 00:00 UTC" in html and "model <code>" in html


def test_failed_latest_run_shows_banner_and_last_good_results(client_as):
    acme, _ = scored_worlds(2)
    with operator_conn() as conn:
        conn.execute(t.pipeline_run.insert().values(
            id=t.new_id(), tenant_id=acme["tenant"]["id"], run_type="nightly", status="failed",
            cutoff_at=datetime(2026, 6, 2, tzinfo=UTC), started_at=datetime.now(UTC)))
    html = client_as(acme["csms"][0]).get("/").text
    assert "did not complete" in html and "from data received before 2026-06-01" in html


def test_owner_filter_is_ignored_for_csms(client_as):
    acme, _ = scored_worlds(2)
    a, b = acme["csms"]
    html = client_as(a).get(f"/worklist?owner={b['id']}").text
    assert _account_names(html) == {x["name"] for x in acme["accounts"] if x["owner_user_id"] == a["id"]}


def test_manager_sees_team_and_unassigned_queue(client_as):
    acme, _ = scored_worlds(2)
    client = client_as(acme["manager"])
    home = client.get("/").text
    assert "Team overview" in home and "Unassigned queue" in home
    unassigned = client.get("/worklist?owner=unassigned").text
    assert _account_names(unassigned) == {x["name"] for x in acme["accounts"] if x["owner_user_id"] is None}
