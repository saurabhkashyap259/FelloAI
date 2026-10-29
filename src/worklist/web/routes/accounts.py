"""Account detail, historical scores, signal drill-down and the JSON score (US2, FR-020–FR-022)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy import Connection, text

from worklist.signals.drilldown import drilldown
from worklist.signals.registry import BY_NAME, format_value
from worklist.web.deps import CurrentUser, NotFound, current_user, load_owned, tenant_conn
from worklist.web.render import render

router = APIRouter()

SCORE_SQL = """
SELECT s.*, r.run_type, r.status AS run_status, r.finished_at AS run_finished_at, r.model_version_id AS run_model,
       m.algorithm, m.history_window, m.scope AS model_scope
FROM account_score s JOIN pipeline_run r ON r.id = s.run_id
LEFT JOIN model_version m ON m.id = s.model_version_id
WHERE {where}
"""


def _score(conn: Connection, account_id, score_id=None) -> dict | None:
    if score_id is not None:
        row = conn.execute(text(SCORE_SQL.format(where="s.id = :sid AND s.account_id = :a")),
                           {"sid": score_id, "a": account_id}).mappings().first()
    else:
        row = conn.execute(text(SCORE_SQL.format(where="s.account_id = :a AND r.status = 'succeeded' "
                                                        "AND r.run_type IN ('nightly', 'backfill')")
                                + " ORDER BY s.cutoff_at DESC, (r.run_type = 'nightly') DESC LIMIT 1"),
                           {"a": account_id}).mappings().first()
    return dict(row) if row else None


def risk_history_svg(points: list[dict], width: int = 560, height: int = 140) -> str:
    pts = [p for p in points if p["probability"] is not None]
    if len(pts) < 2:
        return ""
    top = max(0.2, max(p["probability"] for p in pts) * 1.15)
    t0, t1 = pts[0]["cutoff_at"].timestamp(), pts[-1]["cutoff_at"].timestamp()
    span = max(t1 - t0, 1)

    def xy(p):
        return 30 + (p["cutoff_at"].timestamp() - t0) / span * (width - 40), height - 20 - p["probability"] / top * (height - 30)

    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in map(xy, pts))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#2b4acb"><title>{p["cutoff_at"]:%Y-%m-%d}: '
                   f'{p["probability"] * 100:.0f}%</title></circle>' for p, (x, y) in zip(pts, map(xy, pts), strict=True))
    return (f'<svg width="100%" viewBox="0 0 {width} {height}" role="img" aria-label="Risk history">'
            f'<line x1="30" y1="{height - 20}" x2="{width - 10}" y2="{height - 20}" stroke="#ccc"/>'
            f'<text x="2" y="14" font-size="10">{top * 100:.0f}%</text><text x="2" y="{height - 20}" font-size="10">0%</text>'
            f'<polyline points="{line}" fill="none" stroke="#2b4acb" stroke-width="1.5"/>{dots}'
            f'<text x="30" y="{height - 4}" font-size="10">{pts[0]["cutoff_at"]:%Y-%m-%d}</text>'
            f'<text x="{width - 10}" y="{height - 4}" font-size="10" text-anchor="end">{pts[-1]["cutoff_at"]:%Y-%m-%d}</text></svg>')


def _account_page(request, user, conn, account, score, historical: bool):
    aid = account["id"]
    conflicts = conn.execute(text("SELECT * FROM account_fact_conflict WHERE account_id = :a ORDER BY detected_at DESC"),
                             {"a": aid}).mappings().all()
    owner = conn.execute(text("SELECT display_name FROM app_user WHERE id = :u"), {"u": account["owner_user_id"]}).scalar()
    history = conn.execute(text(
        "SELECT s.id, s.cutoff_at, s.probability, s.band, s.state, r.run_type FROM account_score s "
        "JOIN pipeline_run r ON r.id = s.run_id WHERE s.account_id = :a AND r.status = 'succeeded' "
        "AND r.run_type IN ('nightly', 'backfill') ORDER BY s.cutoff_at, (r.run_type = 'nightly')"),
        {"a": aid}).mappings().all()
    by_cutoff = {h["cutoff_at"]: dict(h) for h in history}  # nightly wins over backfill for the same cutoff
    actions = conn.execute(text("SELECT c.*, u.display_name FROM csm_action c JOIN app_user u ON u.id = c.user_id "
                                "WHERE c.account_id = :a ORDER BY c.created_at DESC"), {"a": aid}).mappings().all()
    alerts = conn.execute(text("SELECT al.*, d.id AS draft_id, d.status AS draft_status FROM alert al "
                               "LEFT JOIN outreach_draft d ON d.alert_id = al.id WHERE al.account_id = :a "
                               "ORDER BY al.created_at DESC"), {"a": aid}).mappings().all()
    snapshot = None
    if score:
        snapshot = conn.execute(text("SELECT signals, missing_sources FROM signal_snapshot WHERE run_id = :r "
                                     "AND account_id = :a"), {"r": score["run_id"], "a": aid}).mappings().first()
    return render(request, "account.html", conn, account=account, owner=owner, score=score, historical=historical,
                  conflicts=conflicts, history=list(by_cutoff.values()),
                  chart=risk_history_svg(list(by_cutoff.values())), actions=actions, alerts=alerts,
                  snapshot=snapshot, signals=BY_NAME, fmt=format_value)


@router.get("/accounts/{account_id}")
def account_detail(request: Request, account_id: str, user: CurrentUser = Depends(current_user),
                   conn: Connection = Depends(tenant_conn)):
    account = load_owned(request, "account", account_id, user, conn)
    return _account_page(request, user, conn, account, _score(conn, account["id"]), historical=False)


@router.get("/accounts/{account_id}/scores/{score_id}")
def account_score(request: Request, account_id: str, score_id: str, user: CurrentUser = Depends(current_user),
                  conn: Connection = Depends(tenant_conn)):
    account = load_owned(request, "account", account_id, user, conn)
    owned = load_owned(request, "score", score_id, user, conn)
    score = _score(conn, account["id"], owned["id"])
    if score is None:
        raise NotFound()
    return _account_page(request, user, conn, account, score, historical=True)


@router.get("/accounts/{account_id}/signals/{signal_name}")
def signal_drilldown(request: Request, account_id: str, signal_name: str, score_id: str | None = None,
                     user: CurrentUser = Depends(current_user), conn: Connection = Depends(tenant_conn)):
    account = load_owned(request, "account", account_id, user, conn)
    if signal_name not in BY_NAME:
        raise NotFound()
    score = _score(conn, account["id"], load_owned(request, "score", score_id, user, conn)["id"]) if score_id \
        else _score(conn, account["id"])
    if score is None:
        raise NotFound()
    snapshot = conn.execute(text("SELECT signals FROM signal_snapshot WHERE run_id = :r AND account_id = :a"),
                            {"r": score["run_id"], "a": account["id"]}).scalar() or {}
    value = snapshot.get(signal_name)
    result = drilldown(conn, account["id"], signal_name, score["cutoff_at"], value)
    contribution = next((c["contribution"] for c in score["contributions"] if c["signal"] == signal_name), None)
    return render(request, "signal.html", conn, account=account, score=score, d=result, contribution=contribution,
                  formatted=format_value(BY_NAME[signal_name], value))


@router.get("/api/accounts/{account_id}/scores/{score_id}")
def score_json(request: Request, account_id: str, score_id: str, user: CurrentUser = Depends(current_user),
               conn: Connection = Depends(tenant_conn)):
    account = load_owned(request, "account", account_id, user, conn)
    owned = load_owned(request, "score", score_id, user, conn)
    score = _score(conn, account["id"], owned["id"])
    if score is None:
        raise NotFound()
    run = conn.execute(text("SELECT id, run_type, cutoff_at, status, started_at, finished_at FROM pipeline_run "
                            "WHERE id = :r"), {"r": score["run_id"]}).mappings().first()
    snapshot = conn.execute(text("SELECT signals, signal_defs_version, missing_sources FROM signal_snapshot "
                                 "WHERE run_id = :r AND account_id = :a"),
                            {"r": score["run_id"], "a": account["id"]}).mappings().first()
    return {
        "score": {k: score[k] for k in ("id", "account_id", "state", "probability", "raw_score", "band",
                                        "rank_in_owner", "confidence", "days_to_renewal", "arr", "cutoff_at",
                                        "signal_defs_version")},
        "run": dict(run),
        "model_version": {"id": score["model_version_id"], "algorithm": score["algorithm"],
                          "history_window": score["history_window"], "scope": score["model_scope"]},
        "signals": dict(snapshot) if snapshot else None,
        "contributions": score["contributions"], "reasons": score["reasons"],
    }
