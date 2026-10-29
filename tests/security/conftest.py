import pytest

from tests.helpers import add_alert_and_draft, scored_worlds


@pytest.fixture
def worlds():
    """Two scored tenants; each has an alert + draft on an account owned by its first CSM."""
    acme, bravo = scored_worlds(2)
    for w in (acme, bravo):
        acc = next(a for a in w["accounts"] if a["owner_user_id"] == w["csms"][0]["id"])
        w["owned"] = {"account": acc["id"], **add_alert_and_draft(w, acc)}
        w["owned"]["score"] = w["owned"].pop("score_id")
        w["owned"]["alert"] = w["owned"].pop("alert_id")
        w["owned"]["draft"] = w["owned"].pop("draft_id")
    return acme, bravo
