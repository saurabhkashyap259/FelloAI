"""Every route the app serves, with its access rule (SC-008).

Rules: public | any_user | csm_own (CSM may only touch resources on accounts they own;
managers see the whole tenant) | manager.
`ids` maps each path parameter to the resource kind used by cross-tenant tests.
"""

INVENTORY: dict[tuple[str, str], dict] = {
    ("GET", "/login"): {"rule": "public"},
    ("POST", "/login"): {"rule": "public"},
    ("POST", "/logout"): {"rule": "any_user"},
    ("GET", "/healthz"): {"rule": "public"},
    ("GET", "/healthz/pipeline"): {"rule": "public"},
    ("GET", "/"): {"rule": "any_user"},
    ("GET", "/worklist"): {"rule": "any_user"},
    ("POST", "/accounts/{account_id}/actions"): {"rule": "csm_own", "ids": {"account_id": "account"}},
    ("GET", "/accounts/{account_id}"): {"rule": "csm_own", "ids": {"account_id": "account"}},
    ("GET", "/accounts/{account_id}/scores/{score_id}"): {
        "rule": "csm_own", "ids": {"account_id": "account", "score_id": "score"}},
    ("GET", "/accounts/{account_id}/signals/{signal_name}"): {"rule": "csm_own", "ids": {"account_id": "account"}},
    ("GET", "/api/accounts/{account_id}/scores/{score_id}"): {
        "rule": "csm_own", "ids": {"account_id": "account", "score_id": "score"}},
    ("GET", "/alerts"): {"rule": "any_user"},
    ("GET", "/alerts/{alert_id}"): {"rule": "csm_own", "ids": {"alert_id": "alert"}},
    ("POST", "/alerts/{alert_id}/acknowledge"): {"rule": "csm_own", "ids": {"alert_id": "alert"}},
    ("POST", "/drafts/{draft_id}/approve"): {"rule": "csm_own", "ids": {"draft_id": "draft"}},
    ("POST", "/drafts/{draft_id}/discard"): {"rule": "csm_own", "ids": {"draft_id": "draft"}},
    ("GET", "/model"): {"rule": "any_user"},
    ("GET", "/runs"): {"rule": "manager"},
}
