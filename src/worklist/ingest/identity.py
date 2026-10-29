"""Identity resolution: source IDs first, then normalised email domain; never free-mail (FR-003)."""

from collections import defaultdict
from uuid import UUID

from sqlalchemy import Connection, text

FREE_MAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
             "aol.com", "proton.me", "protonmail.com", "live.com"}
ID_FIELDS = {"crm": "sf_account_id", "billing": "stripe_customer_id", "product": "product_org_id",
             "support": "zendesk_org_id"}


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    domain = value.split("@")[-1].strip().lower()
    return domain[4:] if domain.startswith("www.") else domain


class AccountIndex:
    """In-memory lookup for one tenant, built from account_source_id, account domains and tickets."""

    def __init__(self, conn: Connection) -> None:
        self.by_source_id: dict[tuple[str, str], UUID] = {}
        self.by_domain: dict[str, set[UUID]] = defaultdict(set)
        self.by_ticket: dict[str, UUID] = {}
        for row in conn.execute(text("SELECT source, external_id, account_id FROM account_source_id")):
            self.by_source_id[(row.source, row.external_id)] = row.account_id
        for row in conn.execute(text("SELECT id, primary_domain FROM account WHERE primary_domain IS NOT NULL")):
            self.by_domain[normalize_domain(row.primary_domain)].add(row.id)
        for row in conn.execute(text(
                "SELECT r.payload->>'ticket_id' AS ticket, ea.account_id FROM raw_event r "
                "JOIN event_account ea ON ea.raw_event_id = r.id WHERE r.event_type = 'ticket.created'")):
            self.by_ticket[row.ticket] = row.account_id

    def resolve(self, source: str, event_type: str, payload: dict) -> tuple[UUID | None, str]:
        """Returns (account_id, 'source_id'|'domain') or (None, 'no_match'|'ambiguous_match')."""
        id_field = ID_FIELDS.get(source)
        if id_field and payload.get(id_field):
            account = self.by_source_id.get((source, str(payload[id_field])))
            if account:
                return self._remember(event_type, payload, account), "source_id"
        if source == "support" and payload.get("ticket_id") in self.by_ticket and event_type != "ticket.created":
            return self.by_ticket[payload["ticket_id"]], "source_id"
        domain = self._domain(source, event_type, payload)
        if domain is None or domain in FREE_MAIL:
            return None, "no_match"
        candidates = self.by_domain.get(domain, set())
        if len(candidates) == 1:
            return self._remember(event_type, payload, next(iter(candidates))), "domain"
        return None, "ambiguous_match" if candidates else "no_match"

    def _remember(self, event_type: str, payload: dict, account: UUID) -> UUID:
        if event_type == "ticket.created" and payload.get("ticket_id"):
            self.by_ticket[payload["ticket_id"]] = account
        return account

    @staticmethod
    def _domain(source: str, event_type: str, payload: dict) -> str | None:
        if source == "email":
            field = {"email.sent": "to_domain", "email.received": "from_domain", "email.bounced": "to_email"}
            return normalize_domain(payload.get(field.get(event_type, "")))
        if source == "support":
            return normalize_domain(payload.get("requester_email"))
        if source == "crm":
            return normalize_domain(payload.get("domain"))
        return None
