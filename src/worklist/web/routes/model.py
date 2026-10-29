"""Model page: this tenant's evaluation plus pooled totals only (FR-025a, US5)."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, text

from worklist.web.deps import CurrentUser, current_user, tenant_conn
from worklist.web.render import render

router = APIRouter()


def reliability_svg(bins: list[dict], size: int = 220) -> str:
    """Inline SVG reliability diagram: mean predicted vs observed churn rate per bin."""
    if not bins:
        return ""
    top = max(0.05, max(max(b["mean_predicted"], b["observed_rate"]) for b in bins) * 1.1)
    pad = 28

    def xy(p: float, o: float) -> tuple[float, float]:
        return pad + p / top * (size - pad - 8), size - pad - o / top * (size - pad - 8)

    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (xy(b["mean_predicted"], b["observed_rate"]) for b in bins))
    x0, y0 = xy(0, 0)
    x1, y1 = xy(top, top)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#2b4acb"/>'
                   for x, y in (xy(b["mean_predicted"], b["observed_rate"]) for b in bins))
    return (f'<svg width="{size}" height="{size}" role="img" aria-label="Reliability diagram">'
            f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#bbb" stroke-dasharray="4"/>'
            f'<line x1="{pad}" y1="{y0}" x2="{size - 8}" y2="{y0}" stroke="#999"/>'
            f'<line x1="{pad}" y1="8" x2="{pad}" y2="{y0}" stroke="#999"/>'
            f'<polyline points="{pts}" fill="none" stroke="#2b4acb"/>{dots}'
            f'<text x="{size / 2}" y="{size - 6}" font-size="10" text-anchor="middle">predicted</text>'
            f'<text x="10" y="{size / 2}" font-size="10" transform="rotate(-90 10 {size / 2})" '
            f'text-anchor="middle">observed</text></svg>')


@router.get("/model")
def model_page(request: Request, user: CurrentUser = Depends(current_user), conn: Connection = Depends(tenant_conn)):
    approved = conn.execute(text(
        "SELECT m.id, m.scope, m.algorithm, m.history_window, m.trained_at, m.windows, m.metrics, "
        "m.decision_reason, c.method, c.params->'evaluation' AS tenant_eval FROM model_version m "
        "LEFT JOIN tenant_calibrator c ON c.model_version_id = m.id "
        "WHERE m.status = 'approved' ORDER BY (m.scope = 'tenant') DESC LIMIT 1")).mappings().first()
    history = conn.execute(text(
        "SELECT id, scope, algorithm, history_window, trained_at, status, decision_reason, "
        "metrics->'overall'->>'top10_hit_rate' AS hit, metrics->'overall'->>'pr_auc' AS pr_auc "
        "FROM model_version ORDER BY trained_at DESC LIMIT 30")).mappings().all()
    last_retrain = conn.execute(text(
        "SELECT status, cutoff_at, finished_at, counts->>'decision' AS decision FROM pipeline_run "
        "WHERE run_type = 'retrain' ORDER BY cutoff_at DESC LIMIT 1")).mappings().first()
    today = conn.execute(text('SELECT "current_date" FROM sim_clock')).scalar()
    last_ok = conn.execute(text("SELECT max(cutoff_at) FROM pipeline_run WHERE run_type = 'retrain' "
                                "AND status = 'succeeded'")).scalar()
    stale_retrain = today is not None and (last_ok is None or last_ok.date() < today - timedelta(days=14))
    tenant_eval: dict = (dict(approved) if approved else {}).get("tenant_eval") or {}
    return render(request, "model.html", conn, approved=approved, history=history, last_retrain=last_retrain,
                  stale_retrain=stale_retrain, tenant_eval=tenant_eval,
                  pooled=(approved["metrics"] if approved else {}),
                  chart=reliability_svg(tenant_eval.get("calibration_bins", [])))
