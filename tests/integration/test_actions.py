"""CSM actions (FR-022a)."""

from sqlalchemy import text

from tests.helpers import scored_worlds
from worklist.db.engine import operator_conn


def _own(world, csm):
    return next(a for a in world["accounts"] if a["owner_user_id"] == csm["id"])


def test_snooze_requires_future_date_and_moves_to_snoozed(client_as):
    acme, _ = scored_worlds(2)
    csm = acme["csms"][0]
    client = client_as(csm)
    acc = _own(acme, csm)
    bad = client.post(f"/accounts/{acc['id']}/actions", data={"action": "snoozed", "snooze_until": "2026-05-01",
                                                              "csrf_token": client.csrf})
    assert bad.status_code == 422
    ok = client.post(f"/accounts/{acc['id']}/actions", data={"action": "snoozed", "snooze_until": "2026-07-01",
                                                             "csrf_token": client.csrf})
    assert ok.status_code == 200 and "until 2026-07-01" in ok.text
    html = client.get("/").text
    snoozed = html.split("Snoozed (")[1].split("</table>")[0]
    assert acc["name"] in snoozed


def test_dismiss_requires_reason_and_actions_record_who_and_when(client_as):
    acme, _ = scored_worlds(2)
    csm = acme["csms"][0]
    client = client_as(csm)
    acc = _own(acme, csm)
    assert client.post(f"/accounts/{acc['id']}/actions", data={"action": "dismissed", "reason": " ",
                                                               "csrf_token": client.csrf}).status_code == 422
    for action in ("contacted", "meeting_booked"):
        assert client.post(f"/accounts/{acc['id']}/actions", data={"action": action,
                                                                   "csrf_token": client.csrf}).status_code == 200
    with operator_conn() as conn:
        rows = conn.execute(text("SELECT action, user_id, created_at FROM csm_action WHERE account_id = :a"),
                            {"a": acc["id"]}).all()
    assert {r.action for r in rows} == {"contacted", "meeting_booked"}
    assert all(r.user_id == csm["id"] and r.created_at for r in rows)


def test_manager_can_act_on_any_account_and_csrf_is_required(client_as):
    acme, _ = scored_worlds(2)
    acc = _own(acme, acme["csms"][1])
    manager = client_as(acme["manager"])
    assert manager.post(f"/accounts/{acc['id']}/actions", data={"action": "contacted"}).status_code == 403
    assert manager.post(f"/accounts/{acc['id']}/actions", data={"action": "contacted",
                                                                "csrf_token": manager.csrf}).status_code == 200
