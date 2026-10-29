"""Unauthenticated health checks. They reveal only a status and a stale-tenant count (FR-027c)."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from worklist.db.engine import web_engine

router = APIRouter()


@router.get("/healthz")
def healthz():
    try:
        with web_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)
    return {"status": "ok"}


@router.get("/healthz/pipeline")
def healthz_pipeline():
    with web_engine().connect() as conn:
        stale = conn.execute(text("SELECT pipeline_stale_tenant_count()")).scalar() or 0
    if stale:
        return JSONResponse({"status": "stale", "stale_tenants": stale}, status_code=503)
    return {"status": "ok"}
