"""Approve or discard an outreach draft (FR-024). Approval records who, when and the final text;
the CSM sends from their own mail client. The system never sends email."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import Connection, text

from worklist.web.deps import CurrentUser, csrf_protect, current_user, load_owned, tenant_conn

router = APIRouter()


@router.post("/drafts/{draft_id}/approve", dependencies=[Depends(csrf_protect)])
def approve(request: Request, draft_id: str, final_text: str = Form(""), user: CurrentUser = Depends(current_user),
            conn: Connection = Depends(tenant_conn)):
    draft = load_owned(request, "draft", draft_id, user, conn)
    body = final_text.strip() or draft["email_body"]
    conn.execute(text("UPDATE outreach_draft SET status = 'approved', final_text = :f, approved_by = :u, "
                      "approved_at = now() WHERE id = :id AND status <> 'approved'"),
                 {"f": body, "u": user.id, "id": draft["id"]})
    return RedirectResponse(f"/alerts/{draft['alert_id']}#draft", status_code=303)


@router.post("/drafts/{draft_id}/discard", dependencies=[Depends(csrf_protect)])
def discard(request: Request, draft_id: str, user: CurrentUser = Depends(current_user),
            conn: Connection = Depends(tenant_conn)):
    draft = load_owned(request, "draft", draft_id, user, conn)
    conn.execute(text("UPDATE outreach_draft SET status = 'discarded' WHERE id = :id AND status = 'draft'"),
                 {"id": draft["id"]})
    return RedirectResponse(f"/alerts/{draft['alert_id']}#draft", status_code=303)
