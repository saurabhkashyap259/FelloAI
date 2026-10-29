"""Pipeline runs for this tenant, CS managers only (FR-006, FR-015, FR-018)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, text

from worklist.web.deps import CurrentUser, require_manager, tenant_conn
from worklist.web.render import render

router = APIRouter()


@router.get("/runs")
def runs(request: Request, user: CurrentUser = Depends(require_manager), conn: Connection = Depends(tenant_conn)):
    rows = conn.execute(text("SELECT * FROM pipeline_run ORDER BY cutoff_at DESC, started_at DESC LIMIT 120"))\
        .mappings().all()
    attention = conn.execute(text("SELECT needs_attention FROM tenant_settings")).scalar()
    return render(request, "runs.html", conn, runs=rows, needs_attention=attention)
