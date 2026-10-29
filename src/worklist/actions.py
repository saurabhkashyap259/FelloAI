"""CSM actions on an account (FR-022a). Snooze/dismiss suppress repeat alerts until a new material
change; the trigger types active and the probability at the time are recorded for that comparison."""

import json
from datetime import date
from uuid import UUID

from sqlalchemy import Connection, text

from worklist.db import tables as t

ACTIONS = {"contacted", "meeting_booked", "snoozed", "dismissed"}


class ActionError(ValueError):
    pass


def validate(action: str, snooze_until: str | None, reason: str | None, today: date) -> date | None:
    if action not in ACTIONS:
        raise ActionError("Unknown action.")
    until = None
    if action == "snoozed":
        try:
            until = date.fromisoformat(snooze_until or "")
        except ValueError as exc:
            raise ActionError("Choose a snooze date.") from exc
        if until <= today:
            raise ActionError("The snooze date must be in the future.")
    if action == "dismissed" and not (reason or "").strip():
        raise ActionError("Give a reason for dismissing.")
    return until


def record(conn: Connection, tenant_id: UUID, account_id: UUID, user_id: UUID, action: str, until: date | None,
           reason: str | None, note: str | None) -> UUID:
    active = conn.execute(text("SELECT DISTINCT trigger FROM alert WHERE account_id = :a AND status = 'open'"),
                          {"a": account_id}).scalars().all()
    probability = conn.execute(text("SELECT probability FROM account_score WHERE account_id = :a "
                                    "ORDER BY cutoff_at DESC LIMIT 1"), {"a": account_id}).scalar()
    action_id = t.new_id()
    conn.execute(t.csm_action.insert().values(
        id=action_id, tenant_id=tenant_id, account_id=account_id, user_id=user_id, action=action,
        snooze_until=until, reason=(reason or "").strip() or None, note=(note or "").strip() or None,
        fingerprint=json.loads(json.dumps({"trigger_types": sorted(active)})), probability_at_action=probability))
    return action_id
