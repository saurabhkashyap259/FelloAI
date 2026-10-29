"""Worklist service: groups and orders accounts from the latest succeeded run (FR-019, FR-018).

Everything shown comes from stored `account_score` rows of one run; actions and alert markers are
overlaid. Only the current owner's accounts are listed for a CSM.
"""

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import Connection, text

GROUPS = [
    ("renewal_overdue", "Renewal overdue", "Renewal date has passed with no renewal recorded."),
    ("high", "Act this week", "Highest risk in your book, above the high-band floor."),
    ("medium", "Watch", "Elevated risk; keep an eye on these."),
    ("low", "Healthy", "Low estimated risk at the next renewal."),
    ("insufficient_data", "Insufficient data", "Too little data to estimate risk. Never treated as healthy."),
    ("snoozed", "Snoozed", "Snoozed by you or a manager until a date."),
    ("not_due", "Later renewals", "Renewal more than 90 days away; not scored yet."),
    ("no_model", "No model yet", "No approved model is available to score these accounts."),
]


@dataclass
class Worklist:
    run: dict | None
    groups: list[dict] = field(default_factory=list)
    today: date | None = None


def latest_run(conn: Connection) -> dict | None:
    row = conn.execute(text(
        "SELECT r.id, r.run_type, r.cutoff_at, r.finished_at, r.model_version_id, r.counts FROM pipeline_run r "
        "WHERE r.status = 'succeeded' AND r.run_type IN ('nightly', 'backfill') "
        "ORDER BY r.cutoff_at DESC, (r.run_type = 'nightly') DESC LIMIT 1")).mappings().first()
    return dict(row) if row else None


def sim_today(conn: Connection) -> date | None:
    return conn.execute(text('SELECT "current_date" FROM sim_clock')).scalar()


ROW_SQL = """
SELECT s.id AS score_id, s.account_id, a.name, a.owner_user_id, a.status AS account_status, s.state, s.probability,
       s.band, s.rank_in_owner, s.confidence, s.reasons, s.days_to_renewal, s.arr,
       act.action AS last_action, act.snooze_until, act.created_at AS last_action_at, act.reason AS last_reason,
       u.display_name AS owner_name,
       EXISTS (SELECT 1 FROM alert al WHERE al.account_id = s.account_id AND al.read_at IS NULL
               AND al.status = 'open') AS has_new_alert
FROM account_score s
JOIN account a ON a.id = s.account_id
LEFT JOIN app_user u ON u.id = a.owner_user_id
LEFT JOIN LATERAL (SELECT action, snooze_until, created_at, reason FROM csm_action c
                   WHERE c.account_id = s.account_id ORDER BY created_at DESC LIMIT 1) act ON true
WHERE s.run_id = :run AND a.status <> 'churned' {filter}
"""


def _group_key(row: dict, today: date | None) -> str:
    if row["last_action"] == "snoozed" and row["snooze_until"] and today and row["snooze_until"] > today:
        return "snoozed"
    if row["state"] == "scored":
        return row["band"]
    return row["state"]


def build(conn: Connection, owner: UUID | None = None, unassigned: bool = False,
          account_id: UUID | None = None) -> Worklist:
    run = latest_run(conn)
    today = sim_today(conn)
    if run is None:
        return Worklist(None, [], today)
    params: dict = {"run": run["id"]}
    if account_id is not None:
        flt, params["acc"] = "AND s.account_id = :acc", account_id
    elif unassigned:
        flt = "AND a.owner_user_id IS NULL"
    else:
        flt, params["owner"] = "AND a.owner_user_id = :owner", owner
    rows = [dict(r) for r in conn.execute(text(ROW_SQL.format(filter=flt)), params).mappings()]
    buckets: dict[str, list[dict]] = {key: [] for key, _, _ in GROUPS}
    for row in rows:
        row["group"] = _group_key(row, today)
        buckets[row["group"]].append(row)
    for key, items in buckets.items():
        if key in ("not_due", "insufficient_data", "no_model", "renewal_overdue"):
            items.sort(key=lambda r: (r["days_to_renewal"] is None, r["days_to_renewal"] or 0))
        else:
            items.sort(key=lambda r: -(r["probability"] or 0))
    groups = [{"key": k, "title": title, "help": help_text, "rows": buckets[k]} for k, title, help_text in GROUPS]
    return Worklist(run, groups, today)


def manager_overview(conn: Connection, run_id) -> list[dict]:
    rows = conn.execute(text(
        "SELECT u.id, u.display_name, u.email, "
        "count(*) FILTER (WHERE s.band = 'high') AS high, count(*) FILTER (WHERE s.band = 'medium') AS medium, "
        "count(*) FILTER (WHERE s.state = 'renewal_overdue') AS overdue, count(a.id) AS accounts "
        "FROM app_user u LEFT JOIN account a ON a.owner_user_id = u.id AND a.status <> 'churned' "
        "LEFT JOIN account_score s ON s.account_id = a.id AND s.run_id = :run "
        "WHERE u.role = 'csm' GROUP BY u.id ORDER BY u.email"), {"run": run_id}).mappings().all()
    return [dict(r) for r in rows]


def outcome_unknown(conn: Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(text(
        "SELECT a.id, a.name, a.renewal_date, u.display_name AS owner_name FROM account a "
        "LEFT JOIN app_user u ON u.id = a.owner_user_id WHERE a.status = 'outcome_unknown' ORDER BY a.renewal_date"))
        .mappings()]
