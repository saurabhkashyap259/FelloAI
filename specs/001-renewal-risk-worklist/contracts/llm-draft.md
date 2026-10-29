# LLM Drafting Contract

The only place an LLM is used. It never scores, ranks or decides whether to alert (FR-008).

## Input: evidence pack (built deterministically)

```json
{
  "account": {"name": "Acme Corp", "arr": 48000, "renewal_date": "2026-12-15"},
  "trigger": {"type": "entered_top_k"},
  "facts": [
    {"fact_id": "f1", "kind": "renewal", "text": "Renewal on 2026-12-15 (49 days away), contract value $48,000 per year"},
    {"fact_id": "f2", "kind": "usage_trend", "text": "Active users fell from 42 to 17 over the last 63 days"},
    {"fact_id": "f3", "kind": "support", "text": "3 support tickets opened in the last 30 days, 2 of them high or urgent priority"},
    {"fact_id": "f4", "kind": "relationship_contact", "text": "Last CSM email 47 days ago"}
  ],
  "csm_name": "Priya"
}
```

Scores, bands, ranks, run and model IDs, and each fact's `raw_event_ids` stay in the system (they are on the alert, not in the request). This is exactly the FR-023a list.

## Output (structured, schema-enforced)

```json
{
  "internal_summary": "2–4 sentences for the CSM",
  "email_subject": "string ≤ 80 chars",
  "email_body": "string ≤ 1200 chars, written as the CSM to the customer",
  "facts_used": ["f1", "f2"]
}
```

## Prompt constraints

- Use only the facts provided. Do not invent numbers, dates, names or events.
- The customer email must not mention risk scores, models or internal monitoring; it offers help grounded in the facts (e.g. a check-in on onboarding after a usage drop).
- Tone: helpful, specific, short.

## Validation (server-side, after the call)

1. Response parses against the schema.
2. `facts_used` is non-empty and ⊆ provided fact_ids.
3. Every number (commas, `%` and a trailing `.0` ignored) and ISO date in `internal_summary`, `email_subject` and `email_body` appears in the evidence pack.
4. Email subject and body contain none of: "risk", "score", "churn", "model", "probability" (case-insensitive word prefix).

Request: `client.beta.messages.parse` with a Pydantic schema (`output_config.format` JSON schema), `effort: low`, 20 s timeout, no SDK retries, and server-side refusal fallbacks (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`); a final `refusal` stop reason falls back to the template.

Any failure (drafting disabled for the tenant, no API key, timeout >20s, API error, validation failure) → the deterministic template draft is stored with `generator = template` and `fallback_reason` set (spec FR-023b).

## Data minimisation (spec FR-023a)

Only the fields shown in the evidence pack above are sent. Never sent: customer contact names, email addresses, raw payloads, ticket bodies, or any other tenant's data. One request = one account of one tenant. Model ID, evidence hash and prompt version are stored with every draft.
