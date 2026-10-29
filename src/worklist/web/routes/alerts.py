"""In-app alerts inbox and alert detail (FR-017b, FR-018a)."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection, text

from worklist.web.deps import CurrentUser, csrf_protect, current_user, load_owned, tenant_conn
from worklist.web.render import render

router = APIRouter()


@router.get("/alerts")
def inbox(request: Request, status: str = "open", user: CurrentUser = Depends(current_user),
          conn: Connection = Depends(tenant_conn)):
    status = status if status in ("open", "acknowledged", "all") else "open"
    rows = conn.execute(text(
        "SELECT al.id, al.trigger, al.trigger_detail, al.status, al.read_at, al.created_at, al.raised_for_user_id, "
        "a.id AS account_id, a.name, a.owner_user_id, cu.display_name AS owner_name, ru.display_name AS raised_for_name, "
        "s.probability, s.band FROM alert al JOIN account a ON a.id = al.account_id "
        "JOIN account_score s ON s.id = al.score_id "
        "LEFT JOIN app_user cu ON cu.id = a.owner_user_id LEFT JOIN app_user ru ON ru.id = al.raised_for_user_id "
        "WHERE (:all OR al.status = :st) AND (:mgr OR a.owner_user_id = :u) "
        "ORDER BY al.created_at DESC, al.priority LIMIT 200"),
        {"all": status == "all", "st": status, "mgr": user.is_manager, "u": user.id}).mappings().all()
    return render(request, "alerts.html", conn, alerts=rows, status=status)


@router.get("/alerts/{alert_id}")
def alert_detail(request: Request, alert_id: str, user: CurrentUser = Depends(current_user),
                 conn: Connection = Depends(tenant_conn)):
    alert = load_owned(request, "alert", alert_id, user, conn)
    if alert["read_at"] is None:
        conn.execute(text("UPDATE alert SET read_at = now() WHERE id = :id"), {"id": alert["id"]})
    account = conn.execute(text("SELECT a.*, u.display_name AS owner_name FROM account a LEFT JOIN app_user u "
                                "ON u.id = a.owner_user_id WHERE a.id = :a"), {"a": alert["account_id"]}).mappings().first()
    raised_for = conn.execute(text("SELECT display_name FROM app_user WHERE id = :u"),
                              {"u": alert["raised_for_user_id"]}).scalar()
    score = conn.execute(text("SELECT * FROM account_score WHERE id = :s"), {"s": alert["score_id"]}).mappings().first()
    ids = [i for fact in alert["evidence"] for i in fact.get("raw_event_ids", [])]
    records = {}
    if ids:
        for r in conn.execute(text("SELECT id, source, event_type, occurred_at, ingested_at, payload FROM raw_event "
                                   "WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids}).mappings():
            records[str(r["id"])] = dict(r)
    draft = conn.execute(text("SELECT d.*, u.display_name AS approver FROM outreach_draft d LEFT JOIN app_user u "
                              "ON u.id = d.approved_by WHERE d.alert_id = :a"), {"a": alert["id"]}).mappings().first()
    return render(request, "alert.html", conn, alert=alert, account=account, raised_for=raised_for, score=score,
                  records=records, draft=draft)


@router.post("/alerts/{alert_id}/acknowledge", dependencies=[Depends(csrf_protect)])
def acknowledge(request: Request, alert_id: str, user: CurrentUser = Depends(current_user),
                conn: Connection = Depends(tenant_conn)):
    alert = load_owned(request, "alert", alert_id, user, conn)
    conn.execute(text("UPDATE alert SET status = 'acknowledged', read_at = coalesce(read_at, now()) WHERE id = :id"),
                 {"id": alert["id"]})
    return RedirectResponse(f"/alerts/{alert['id']}", status_code=303)
