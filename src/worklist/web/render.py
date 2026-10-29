"""Template rendering with the shared layout context (user, unread alerts, run banner)."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import Connection, text

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value / 1000:,.0f}k" if value >= 1000 else f"${value:,.0f}"


def _dt(value: datetime | date | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return value.isoformat()


templates.env.filters.update(pct=_pct, money=_money, dt=_dt)


def layout_context(conn: Connection, user) -> dict[str, Any]:
    """Unread alert count and the status of the latest nightly run (FR-017b, FR-018)."""
    unread = conn.execute(
        text("SELECT count(*) FROM alert al JOIN account a ON a.id = al.account_id "
             "WHERE al.read_at IS NULL AND al.status = 'open' "
             "AND (a.owner_user_id = :u OR (:m AND a.owner_user_id IS NULL) OR (:m AND :all_mgr))"),
        {"u": user.id, "m": user.is_manager, "all_mgr": user.is_manager},
    ).scalar()
    latest = conn.execute(
        text("SELECT status, cutoff_at, finished_at FROM pipeline_run "
             "WHERE run_type = 'nightly' ORDER BY cutoff_at DESC, started_at DESC LIMIT 1")
    ).mappings().first()
    return {"unread_alerts": unread, "latest_run": dict(latest) if latest else None}


def render(request: Request, name: str, conn: Connection | None = None, status_code: int = 200,
           **context: Any):
    user = getattr(request.state, "user", None)
    ctx: dict[str, Any] = {"request": request, "user": user}
    if conn is not None and user is not None:
        ctx.update(layout_context(conn, user))
    ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
