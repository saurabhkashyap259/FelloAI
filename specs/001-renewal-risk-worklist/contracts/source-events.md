# Source Event Contract

Shapes emitted by the synthetic generator and accepted by the ingest step. Real connectors would be adapters producing these same envelopes. The envelope maps to `raw_event`; `payload` is stored untouched.

## Envelope

```json
{
  "tenant_id": "uuid",
  "source": "crm | email | support | product | billing",
  "source_record_id": "string | null",
  "event_type": "string",
  "occurred_at": "ISO-8601 (may lack timezone for some sources)",
  "ingested_at": "ISO-8601 UTC",
  "payload": { }
}
```

Account matching keys are read from the payload: `sf_account_id`, `stripe_customer_id`, `product_org_id`, `zendesk_org_id`, or an email/domain field.

## Event types by source

| Source | event_type | Payload fields (nullable fields marked ?) | Known defects injected |
|---|---|---|---|
| crm (Salesforce-like) | `account.upserted` | `sf_account_id`, `name`, `domain?`, `owner_email?`, `segment?`, `arr?`, `renewal_date?` | stale ARR, renewal date conflicts with billing, missing owner |
| | `contact.changed` | `sf_account_id`, `contact_email`, `role?`, `change` ∈ {added, removed, title_changed} | champion removal without replacement |
| | `opportunity.updated` | `sf_account_id`, `stage`, `amount?`, `close_date?` | |
| email (Gmail-like) | `email.sent` / `email.received` | `thread_id`, `from_domain`, `to_domain`, `direction`, `is_csm?` | personal domains (gmail.com), missing `is_csm` |
| | `email.bounced` | `to_email`, `reason?` | |
| support (Zendesk-like) | `ticket.created` | `ticket_id`, `zendesk_org_id?`, `requester_email`, `priority` ∈ {low, normal, high, urgent}, `subject` | missing org (match by email domain) |
| | `ticket.updated` | `ticket_id`, `status`, `priority?` | updates arriving before the create |
| | `ticket.solved` | `ticket_id`, `satisfaction?` ∈ {good, bad, null} | |
| product | `usage.daily` | `product_org_id`, `date`, `active_users`, `sessions`, `key_actions` | duplicates (retry), local-time `occurred_at` without tz, gaps (outage days) |
| | `seats.changed` | `product_org_id`, `seats_purchased`, `seats_assigned` | |
| billing (Stripe-like) | `invoice.paid` | `invoice_id`, `stripe_customer_id`, `amount`, `currency` | |
| | `invoice.payment_failed` | `invoice_id`, `stripe_customer_id`, `attempt` | retries produce repeated events |
| | `subscription.updated` | `stripe_customer_id`, `plan`, `mrr`, `current_period_end`, `cancel_at_period_end` | |

## Ingest rules

1. Normalise `occurred_at` to UTC (naive timestamps interpreted in the source's configured timezone; original value kept in payload).
2. `dedupe_key` = `source_record_id` if present else SHA-256 of canonical JSON of `(event_type, occurred_at, payload)`; insert with `ON CONFLICT DO NOTHING` and count duplicates.
3. Reject to quarantine (`invalid_payload`) if required fields are missing or `occurred_at > ingested_at + 1 day`.
4. Identity resolution per research R12; unmatched → quarantine `no_match`.
