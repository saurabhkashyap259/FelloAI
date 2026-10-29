"""Only the minimal evidence pack leaves the system (FR-023a)."""

import json

from sqlalchemy import text

from tests.helpers import add_alert_and_draft, scored_worlds
from worklist.agent.drafts import evidence_pack
from worklist.db.engine import job_engine, tenant_scope


def test_pack_contains_only_allowed_fields_and_one_tenant():
    acme, bravo = scored_worlds(2)
    acc = next(a for a in acme["accounts"] if a["owner_user_id"])
    ids = add_alert_and_draft(acme, acc)
    with tenant_scope(job_engine(), acme["tenant"]["id"]) as conn:
        alert = dict(conn.execute(text("SELECT * FROM alert WHERE id = :a"), {"a": ids["alert_id"]}).mappings().first())
        pack = evidence_pack(conn, alert)
    assert set(pack) == {"account", "trigger", "facts", "csm_name"}
    assert set(pack["account"]) == {"name", "arr", "renewal_date"}
    assert all(set(f) == {"fact_id", "kind", "text"} for f in pack["facts"])
    blob = json.dumps(pack)
    assert "@" not in blob and "payload" not in blob and "raw_event" not in blob
    assert "Bravo" not in blob and str(bravo["tenant"]["id"]) not in blob
    assert pack["csm_name"] == "CSM"  # first name only
