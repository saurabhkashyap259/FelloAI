"""Identity resolution order and quarantine reasons (FR-003)."""

from worklist.ingest.identity import AccountIndex


def _index() -> AccountIndex:
    idx = AccountIndex.__new__(AccountIndex)
    idx.by_source_id = {("billing", "cus_1"): "A", ("support", "zd_1"): "A"}
    idx.by_domain = {"acme.com": {"A"}, "shared.com": {"B", "C"}, "beta.com": {"B"}}
    idx.by_ticket = {}
    return idx


def test_source_id_wins_over_domain():
    assert _index().resolve("support", "ticket.created",
                            {"zendesk_org_id": "zd_1", "requester_email": "x@beta.com", "ticket_id": "t"}) == ("A", "source_id")


def test_domain_fallback_and_ticket_follow_up():
    idx = _index()
    assert idx.resolve("support", "ticket.created", {"requester_email": "a@Beta.com", "ticket_id": "t9"}) == ("B", "domain")
    assert idx.resolve("support", "ticket.solved", {"ticket_id": "t9"}) == ("B", "source_id")


def test_free_mail_never_matches():
    assert _index().resolve("email", "email.received", {"from_domain": "gmail.com"}) == (None, "no_match")


def test_two_candidates_is_ambiguous_and_none_is_no_match():
    idx = _index()
    assert idx.resolve("email", "email.sent", {"to_domain": "shared.com"}) == (None, "ambiguous_match")
    assert idx.resolve("email", "email.sent", {"to_domain": "nowhere.com"}) == (None, "no_match")
    assert idx.resolve("billing", "invoice.paid", {"stripe_customer_id": "deleted_cus_1"}) == (None, "no_match")
