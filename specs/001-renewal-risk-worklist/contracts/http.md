# HTTP Contract

Server-rendered HTML pages (primary interface) plus a small JSON surface for tests and the drill-down. All routes except `/login`, `/healthz*` and static assets require a valid session. Tenant and role always come from the session (never from the URL, query or body).

**Common rules**

- No session → `303` to `/login` (HTML) or `401` (JSON).
- An ID that does not exist **or** belongs to another tenant → `404` (identical response, so tenant existence can't be probed), plus an `access_audit` row (`denied_cross_tenant` when the ID exists in another tenant).
- CSM requesting another CSM's account (or its alerts/drafts) in the same tenant → `404` + `denied_role` audit. CS managers can see and act on all accounts in their tenant (record actions, acknowledge alerts, approve drafts), per spec FR-027.
- Sessions expire 12h after sign-in; `POST /logout` deletes the session row server-side.
- All `POST` requests require the session's CSRF token (`csrf_token` form field or `X-CSRF-Token` header); missing/invalid → `403`.
- Every page that shows scores displays `run_id`, `cutoff_at` and `model_version` of the run it reads from, and a banner if the latest run is not `succeeded`.

## Auth

| Method | Path | Behaviour |
|---|---|---|
| GET | `/login` | Sign-in form. When `DEMO_MODE=true` only: a list of demo accounts (tenant, name, role) with one-click sign-in buttons. |
| POST | `/login` | `email`, `password`. Success → `303 /`; failure → re-render with a generic error (no user enumeration). Rate-limited to 10 attempts/min per IP and per email (`429`). |
| POST | `/logout` | Deletes the session. |

## Worklist (Story 1)

| Method | Path | Behaviour |
|---|---|---|
| GET | `/` | CSM: own worklist. CS manager: tenant overview with a CSM picker, an **Unassigned** queue and an **Outcome unknown** queue. Header shows the alerts unread count. |
| GET | `/worklist?owner={user_id}&band=&state=` | Rows from `account_score` of the latest succeeded run, in groups (spec FR-019): **Renewal overdue**, **Act this week** (`high`), **Watch** (`medium`), **Healthy** (`low`), **Insufficient data**, **Snoozed**, **Later renewals** (`not_due`), **No model yet**. Scored row: name, probability (%), band, top 3 reasons, confidence, days to renewal, ARR, latest action, "new" marker if it has an unread alert. `owner` is only honoured for CS managers. |
| POST | `/accounts/{account_id}/actions` | Body: `action` ∈ {contacted, meeting_booked, snoozed, dismissed}, `snooze_until` (required if snoozed), `reason` (required if dismissed), `note`. Returns the updated row fragment (HTMX). `422` on validation failure. |

## Account detail and traceability (Story 2)

| Method | Path | Behaviour |
|---|---|---|
| GET | `/accounts/{account_id}` | Header facts (ARR, renewal date, owner) with any `account_fact_conflict`; current score with run/model/cutoff; full contribution list; risk history chart across runs; actions; alerts; drafts. |
| GET | `/accounts/{account_id}/scores/{score_id}` | A specific historical score, exactly as stored. |
| GET | `/accounts/{account_id}/signals/{signal_name}?score_id=` | Drill-down: signal definition (plain-language + window), its value in that snapshot, and the source records that produced it (raw events matched to the account with `ingested_at ≤ cutoff`, `matched_at ≤ cutoff`, inside the signal's `occurred_at` window). For `absence`/`recency` signals: the window searched, "no qualifying records in window", and the latest qualifying record before it or "none since account start" (spec FR-021). |
| GET | `/api/accounts/{account_id}/scores/{score_id}` | JSON form of the above for tests: `{score, run, model_version, signals, contributions, reasons}`. |

## Alerts and drafts (Stories 3–4)

| Method | Path | Behaviour |
|---|---|---|
| GET | `/alerts?status=open` | In-app alerts inbox (spec FR-017b): alerts on accounts the user currently owns (manager: all tenant alerts, including unassigned accounts). Shows "originally raised for" when the owner changed. |
| GET | `/alerts/{alert_id}` | Trigger detail, evidence pack items each linked to source records, and the draft (summary + email; labelled "Template" with reason if it fell back) with the facts it used highlighted. Opening marks it read. |
| POST | `/alerts/{alert_id}/acknowledge` | Sets status `acknowledged`. |
| POST | `/drafts/{draft_id}/approve` | Account owner or CS manager only. Body: `final_text`. Records approver/time/text, status → `approved`. Response shows the text with **Copy** and **Open in mail client** (`mailto:`) controls. The server sends nothing. |
| POST | `/drafts/{draft_id}/discard` | Status → `discarded`. |

## Model and runs (Story 5, operability)

| Method | Path | Behaviour |
|---|---|---|
| GET | `/model` | Approved model for this tenant: training and history windows, evaluation report for **this tenant plus pooled totals only** (top-10 hit rate per CSM, lead time, calibration plot, baseline comparison, silent-churn recall, planted-driver recovery, full-vs-18-month comparison), history of candidates with promotion decisions, last retrain outcome. Notice if no retrain succeeded in 14 days. |
| GET | `/runs` | Pipeline runs for this tenant with status and counts (ingested, duplicates, quarantined/aged, stale sources, alerts raised/suppressed, catch-up flag). CS manager only. |
| GET | `/healthz` | `200 {"status":"ok"}` if the DB is reachable. No auth. |
| GET | `/healthz/pipeline` | `200 {"status":"ok"}` if every tenant has a succeeded nightly run within 26h of the sim clock, else `503 {"status":"stale","stale_tenants":<count>}`. No names, IDs or volumes (spec FR-027c). No auth. |

## Route inventory

Every route above is listed in a test fixture with its access rule (public / any user / CSM-own / manager). A meta-test fails if the app registers a route not in the inventory (spec SC-008).
