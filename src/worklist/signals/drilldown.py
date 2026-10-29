"""Drill-down from a reason to the records behind it (FR-021, research R12a).

Uses the same point-in-time rules as scoring: records received and linked before the score's
cutoff, inside the signal's business-time window. For absence/recency signals it shows the window
searched, that nothing qualifying was found, and the latest qualifying record before it.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Connection, text

from worklist.signals.registry import BY_NAME, Signal

LIMIT = 50


def _filters(signal: Signal) -> tuple[str, dict]:
    clauses, params = ["r.event_type = ANY(:types)"], {"types": list(signal.event_types)}
    for i, (key, values) in enumerate(signal.where.items()):
        clauses.append(f"r.payload->>'{key}' = ANY(:w{i})")
        params[f"w{i}"] = values
    if signal.name in ("days_since_csm_email", "csm_emails_90d"):
        clauses.append("coalesce(r.payload->>'is_csm', 'true') = 'true'")
    return " AND ".join(clauses), params


def _query(conn: Connection, account_id, signal: Signal, cutoff: datetime, start: datetime | None,
           end: datetime, limit: int = LIMIT) -> tuple[list[dict], int]:
    where, params = _filters(signal)
    params.update({"acc": account_id, "cutoff": cutoff, "end": end, "start": start, "limit": limit})
    window = "AND r.occurred_at < :end" + (" AND r.occurred_at >= :start" if start else "")
    base = (f"FROM raw_event r JOIN event_account ea ON ea.raw_event_id = r.id WHERE ea.account_id = :acc "
            f"AND r.ingested_at < :cutoff AND ea.matched_at < :cutoff AND {where} {window}")
    rows = conn.execute(text(f"SELECT r.id, r.source, r.event_type, r.occurred_at, r.ingested_at, r.payload, "
                             f"ea.match_method, ea.matched_at {base} ORDER BY r.occurred_at DESC LIMIT :limit"),
                        params).mappings().all()
    total = conn.execute(text(f"SELECT count(*) {base}"), params).scalar()
    return [dict(r) for r in rows], total


def drilldown(conn: Connection, account_id, signal_name: str, cutoff: datetime, value: float | None) -> dict:
    signal = BY_NAME[signal_name]
    result: dict[str, Any] = {"signal": signal, "value": value, "cutoff": cutoff, "records": [], "total": 0,
                              "absence": None, "window_start": None}
    if not signal.event_types:  # account attributes: no source records, defined by account facts
        return result
    window_days = signal.window_days
    if signal.kind in ("recency", "absence") and value is not None and signal.unit == "days":
        window_days = int(value)  # "no CSM contact in 47 days" → the 47 days searched
    start = cutoff - timedelta(days=window_days) if window_days else None
    result["window_start"] = start
    result["records"], result["total"] = _query(conn, account_id, signal, cutoff, start, cutoff)
    if signal.kind in ("recency", "absence"):
        before, _ = _query(conn, account_id, signal, cutoff, None, start or cutoff, limit=1)
        result["absence"] = {"found_in_window": result["total"] > 0, "latest_before": before[0] if before else None}
    return result
