"""Request dependencies: who is signed in, the tenant-scoped connection, CSRF, and denial.

Tenant and role always come from the server-side session, never from the request (FR-027a).
"""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Connection, text

from worklist.db.engine import tenant_scope, web_engine
from worklist.domain import AuditOutcome, Role
from worklist.web.auth import COOKIE_NAME, load_session, unsign


class NotAuthenticated(Exception):
    """Raised for a protected route without a valid session (303 for pages, 401 for /api)."""


class NotFound(Exception):
    """The single 404 used for missing, cross-tenant and other-CSM resources (FR-026)."""


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    tenant_id: UUID
    role: str
    session_id: str
    csrf_token: str
    display_name: str
    tenant_name: str

    @property
    def is_manager(self) -> bool:
        return self.role == Role.CS_MANAGER


def audit(request: Request, user_id, tenant_id, requested_id: str | None, outcome: str) -> None:
    """Writes an access_audit row in its own transaction, so it survives the failing request."""
    with web_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO access_audit (id, user_id, tenant_id, route, requested_id, outcome) "
                 "VALUES (gen_random_uuid(), :u, :t, :r, :q, :o)"),
            {"u": user_id, "t": tenant_id, "r": request.url.path, "q": requested_id, "o": outcome},
        )


def current_user(request: Request) -> CurrentUser:
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    cookie = request.cookies.get(COOKIE_NAME)
    session_id = unsign(cookie) if cookie else None
    session = None
    if session_id:
        with web_engine().begin() as conn:
            session = load_session(conn, session_id)
    if session is None:
        raise NotAuthenticated()
    with tenant_scope(web_engine(), session["tenant_id"]) as conn:
        row = conn.execute(
            text("SELECT u.display_name, t.name AS tenant_name FROM app_user u "
                 "JOIN tenant t ON t.id = u.tenant_id WHERE u.id = :u AND u.active"),
            {"u": session["user_id"]},
        ).mappings().first()
    if row is None:
        raise NotAuthenticated()
    user = CurrentUser(id=session["user_id"], tenant_id=session["tenant_id"], role=session["role"],
                       session_id=session_id, csrf_token=session["csrf_token"],
                       display_name=row["display_name"], tenant_name=row["tenant_name"])
    request.state.user = user
    return user


def tenant_conn(user: CurrentUser = Depends(current_user)) -> Iterator[Connection]:
    """A connection confined by RLS to the signed-in user's tenant, for the whole request."""
    with tenant_scope(web_engine(), user.tenant_id) as conn:
        yield conn


def require_manager(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if not user.is_manager:
        raise NotFound()
    return user


async def csrf_protect(request: Request, user: CurrentUser = Depends(current_user)) -> None:
    token = request.headers.get("X-CSRF-Token")
    if token is None:
        form = await request.form()
        token = form.get("csrf_token")  # type: ignore[assignment]
    if not token or token != user.csrf_token:
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def deny(request: Request, user: CurrentUser, requested_id: str | None, outcome: str) -> NotFound:
    audit(request, user.id, user.tenant_id, requested_id, outcome)
    return NotFound()


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


OWNED_QUERIES = {
    "account": "SELECT a.*, a.owner_user_id AS current_owner FROM account a WHERE a.id = :id",
    "alert": "SELECT al.*, a.owner_user_id AS current_owner FROM alert al "
             "JOIN account a ON a.id = al.account_id WHERE al.id = :id",
    "draft": "SELECT d.*, a.owner_user_id AS current_owner, al.account_id FROM outreach_draft d "
             "JOIN alert al ON al.id = d.alert_id JOIN account a ON a.id = al.account_id WHERE d.id = :id",
    "score": "SELECT s.*, a.owner_user_id AS current_owner FROM account_score s "
             "JOIN account a ON a.id = s.account_id WHERE s.id = :id",
}


def load_owned(request: Request, kind: str, raw_id: str, user: CurrentUser, conn: Connection) -> dict:
    """Loads an account/alert/draft/score the user may see, or raises the shared 404 + audit.

    RLS hides other tenants' rows; CSM ownership (by the account's current owner) is checked here.
    Unassigned accounts are visible to managers only.
    """
    resource_id = _parse_uuid(raw_id)
    row = None
    if resource_id is not None:
        row = conn.execute(text(OWNED_QUERIES[kind]), {"id": resource_id}).mappings().first()
    if row is None:
        outcome = None
        if resource_id is not None:
            outcome = conn.execute(text("SELECT classify_missing_resource(:k, :id)"),
                                   {"k": kind, "id": resource_id}).scalar()
        if outcome == "other_tenant":
            raise deny(request, user, raw_id, AuditOutcome.DENIED_CROSS_TENANT)
        raise NotFound()
    if not user.is_manager and row["current_owner"] != user.id:
        raise deny(request, user, raw_id, AuditOutcome.DENIED_ROLE)
    return dict(row)
