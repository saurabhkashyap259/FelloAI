"""Worklist pages and CSM actions (US1, FR-019, FR-022a)."""

from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import Connection

from worklist import actions
from worklist import worklist as wl
from worklist.web.deps import CurrentUser, csrf_protect, current_user, load_owned, tenant_conn
from worklist.web.render import render, templates

router = APIRouter()


@router.get("/")
def home(request: Request, user: CurrentUser = Depends(current_user), conn: Connection = Depends(tenant_conn)):
    if not user.is_manager:
        return _worklist_page(request, user, conn, owner=user.id, title="Your worklist")
    data = wl.build(conn, unassigned=True)
    overview = wl.manager_overview(conn, data.run["id"]) if data.run else []
    return render(request, "manager.html", conn, data=data, overview=overview, unknown=wl.outcome_unknown(conn))


@router.get("/worklist")
def worklist_page(request: Request, owner: str | None = None, user: CurrentUser = Depends(current_user),
                  conn: Connection = Depends(tenant_conn)):
    if not user.is_manager or not owner:  # `owner` is only honoured for CS managers
        return _worklist_page(request, user, conn, owner=user.id, title="Your worklist")
    if owner == "unassigned":
        return _worklist_page(request, user, conn, unassigned=True, title="Unassigned accounts")
    try:
        owner_id = UUID(owner)
    except ValueError:
        owner_id = None
    name = conn.exec_driver_sql("SELECT display_name FROM app_user WHERE id = %(id)s",
                                {"id": owner_id}).scalar() if owner_id else None
    return _worklist_page(request, user, conn, owner=owner_id, title=f"Worklist: {name or 'unknown CSM'}")


def _worklist_page(request, user, conn, title: str, owner=None, unassigned: bool = False):
    data = wl.build(conn, owner=owner, unassigned=unassigned)
    return render(request, "worklist.html", conn, data=data, title=title)


@router.post("/accounts/{account_id}/actions", dependencies=[Depends(csrf_protect)])
def record_action(request: Request, account_id: str, action: str = Form(...), snooze_until: str | None = Form(None),
                  reason: str | None = Form(None), note: str | None = Form(None),
                  user: CurrentUser = Depends(current_user), conn: Connection = Depends(tenant_conn)):
    account = load_owned(request, "account", account_id, user, conn)
    try:
        until = actions.validate(action, snooze_until, reason, wl.sim_today(conn))
    except actions.ActionError as exc:
        return HTMLResponse(f'<p class="error">{exc}</p>', status_code=422)
    actions.record(conn, user.tenant_id, account["id"], user.id, action, until, reason, note)
    data = wl.build(conn, account_id=account["id"])
    row = next((r for g in data.groups for r in g["rows"]), None)
    if row is None:
        return HTMLResponse('<tr><td colspan="8" class="meta">Action recorded.</td></tr>')
    return templates.TemplateResponse(request, "_row.html", {"row": row, "user": user, "today": data.today,
                                                             "request": request})
