# Data Model: Renewal Risk Worklist

PostgreSQL 16. All IDs are UUIDv7 (time-ordered) unless noted. Every table marked **[T]** carries `tenant_id uuid NOT NULL` with forced Row-Level Security (policy `tenant_id = current_setting('app.tenant_id', true)::uuid`). Tables marked **[A]** are append-only: the app role has no `UPDATE`/`DELETE` grant on them.

## Overview

```text
tenant ─┬─ app_user ── session
        ├─ account ─┬─ account_source_id
        │           ├─ account_fact_conflict
        │           ├─ renewal_outcome
        │           └─ csm_action
        ├─ raw_event [A] ─┬─ event_account [A]
        │                 └─ quarantined_event
        ├─ pipeline_run ─┬─ signal_snapshot [A]
        │                ├─ account_score [A]
        │                └─ alert ── outreach_draft
        └─ tenant_settings
model_version (global or per tenant) ── tenant_calibrator
sim_clock (deployment-wide), access_audit [A]
```

## Tenancy and identity

### tenant
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| name | text unique | |
| created_at | timestamptz | |

Not RLS-protected (no tenant data); the app only reads the row for the session's tenant.

### tenant_settings [T]
| Field | Type | Notes |
|---|---|---|
| tenant_id | uuid PK | |
| high_band_size | int | default 10 (FR-011) |
| high_band_floor | numeric | default 0.15 |
| alert_risk_jump | numeric | default 0.15 absolute increase vs. score 7 days earlier |
| renewal_alert_window_days | int | default 60, for "severe event near renewal" alerts |
| alert_cap_nightly | int | default 3 per CSM (FR-017a) |
| alert_cap_weekly | int | default 10 per CSM per rolling 7 days |
| llm_drafting_enabled | bool | default true (FR-023a) |
| source_timezones | jsonb | per-source tz for naive timestamps (FR-001a) |
| needs_attention | bool | set after >3 consecutive failed cutoffs (FR-014b) |

### app_user [T]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| email | citext | unique per tenant |
| display_name | text | |
| role | enum `csm` \| `cs_manager` | FR-027 |
| password_hash | text | argon2 |
| is_demo | bool | demo accounts listed on sign-in page |
| active | bool | |

### session
| Field | Type | Notes |
|---|---|---|
| id | text PK | random 256-bit, in signed cookie |
| user_id, tenant_id, role | | copied at sign-in; source of `app.tenant_id` |
| csrf_token | text | |
| expires_at | timestamptz | 12h |

Read before tenant context exists, so it is protected by grants, not RLS.

## Accounts

### account [T]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| name | text | |
| primary_domain | text | normalised; nullable |
| owner_user_id | uuid FK app_user | nullable → "unassigned" queue |
| segment | text | from CRM |
| arr | numeric | resolved per precedence (billing) |
| renewal_date | date | resolved per precedence (billing); for monthly plans = current period end |
| contract_type | enum `term` \| `monthly` | |
| contract_start | date | |
| status | enum `active` \| `renewal_overdue` \| `outcome_unknown` \| `churned` | `renewal_overdue`: renewal date passed, no renewal yet (owner's top group); `outcome_unknown`: sources contradict on whether it renewed (manager queue); `churned` after grace period with no renewal |
| facts_resolved_at | timestamptz | |

### account_source_id [T]
Maps external IDs to an account. Unique `(tenant_id, source, external_id)`.
| Field | Type |
|---|---|
| account_id | uuid FK |
| source | enum `crm` \| `email` \| `support` \| `product` \| `billing` |
| external_id | text |

### account_fact_conflict [T]
| Field | Type | Notes |
|---|---|---|
| account_id | uuid FK | |
| fact | enum `arr` \| `renewal_date` \| `owner` | |
| chosen_source, chosen_value | text | |
| other_source, other_value | text | |
| detected_run_id | uuid FK pipeline_run | |

### renewal_outcome [T]
| Field | Type | Notes |
|---|---|---|
| account_id | uuid FK | |
| renewal_date | date | |
| outcome | enum `renewed` \| `downgraded` \| `churned` | only `churned` is the positive label (Clarification Q1) |
| arr_before, arr_after | numeric | |
| recorded_at | timestamptz | used for point-in-time label availability |
| is_silent_churn | bool | **synthetic ground truth only**; never used as a feature |

## Raw data

### raw_event [T][A]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| source | enum (5 sources) | |
| source_record_id | text | nullable for sources without stable IDs |
| dedupe_key | text | `source_record_id` or content hash; unique `(tenant_id, source, dedupe_key)` → `ON CONFLICT DO NOTHING` |
| event_type | text | e.g. `ticket.created`, `usage.daily`, `invoice.payment_failed` |
| occurred_at | timestamptz | business time; normalised to UTC (source tz recorded in payload) |
| ingested_at | timestamptz | system time; cutoff key for point-in-time |
| payload | jsonb | original fields, untouched |

Indexes: `(tenant_id, ingested_at)`, `(tenant_id, source, occurred_at)`.

### event_account [T][A]
Links a raw event to an account. Kept separate so `raw_event` never changes, even when a quarantined event is matched by a later run.
| Field | Type | Notes |
|---|---|---|
| raw_event_id | uuid PK FK | |
| account_id | uuid FK | |
| match_method | enum `source_id` \| `domain` | |
| matched_at | timestamptz | signals as of cutoff C only use links with `matched_at ≤ C`, so a late match never changes past results |

Index: `(tenant_id, account_id)`.

### quarantined_event [T]
| Field | Type | Notes |
|---|---|---|
| raw_event_id | uuid FK | |
| reason | enum `no_match` \| `ambiguous_match` \| `invalid_payload` | |
| run_id | uuid FK | run that first quarantined it |
| last_attempt_run_id | uuid FK | retried every nightly run (FR-003a) |
| resolved_at | timestamptz | nullable; set when a later run links it (link `matched_at` = that run's cutoff) |

"Aged" = unresolved and `now − ingested_at > 30 days`; counted in `pipeline_run.counts`.

## Pipeline and scoring

### pipeline_run [T]
One row per tenant per run (a nightly invocation creates one per tenant).
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_type | enum `nightly` \| `retrain` \| `backfill` | |
| cutoff_at | timestamptz | data cutoff (sim clock) |
| status | enum `running` \| `succeeded` \| `partial` \| `failed` | |
| model_version_id | uuid FK | model used for scoring |
| started_at, finished_at | timestamptz | |
| counts | jsonb | `{ingested, duplicates, quarantined, quarantine_aged, scored, insufficient, not_due, alerts_raised, alerts_suppressed, stale_sources:[...]}` (FR-006, FR-015, FR-017a) |
| is_catch_up | bool | processed as catch-up for a missed cutoff (FR-014b) |
| error | text | |

Unique `(tenant_id, run_type, cutoff_at)` → idempotency (FR-016).

State transitions: `running → succeeded | partial | failed`; a re-trigger of a `failed`/`partial` run for the same cutoff resumes it (back to `running`); re-trigger of `succeeded` is a no-op. A tenant with more than 3 consecutive failed cutoffs gets `tenant_settings.needs_attention = true` and resumes at the latest cutoff (FR-014b).

### signal_snapshot [T][A]
| Field | Type | Notes |
|---|---|---|
| run_id | uuid FK | |
| account_id | uuid FK | |
| cutoff_at | timestamptz | |
| signals | jsonb | `{signal_name: value \| null}` (≈30 signals) |
| signal_defs_version | text | hash of the signal definition registry |
| missing_sources | text[] | sources with no data in lookback window |

PK `(run_id, account_id)`.

### model_version
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| scope | enum `global` \| `tenant` | |
| tenant_id | uuid | null for global; if set, RLS applies |
| algorithm | text | `lightgbm` \| `logreg_baseline` |
| history_window | enum `full` \| `recent_18m` | FR-012b |
| trained_at | timestamptz | |
| train_window, valid_window, test_window | tstzrange | |
| signal_defs_version | text | must match scoring snapshot |
| artifact | bytea | serialised model |
| metrics | jsonb | overall + per tenant (R7) |
| status | enum `candidate` \| `approved` \| `rejected` \| `retired` | |
| decision_reason | text | why promoted/rejected (FR-013, FR-014a) |

State transitions: `candidate → approved` (gate passes; previous approved → `retired`) or `candidate → rejected`. Exactly one `approved` per scope (partial unique index).

### tenant_calibrator [T]
| Field | Type |
|---|---|
| model_version_id | uuid FK |
| method | enum `isotonic` \| `platt` |
| params | jsonb |

### account_score [T][A]
The only source for anything shown on the worklist (FR-020).
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_id | uuid FK | |
| account_id | uuid FK | |
| owner_user_id | uuid | owner at scoring time |
| state | enum `scored` \| `insufficient_data` \| `not_due` \| `renewal_overdue` \| `no_model` | `not_due` = outside 90-day horizon (FR-008) |
| model_version_id, signal_defs_version | | copied for reproducibility (FR-002, FR-020) |
| raw_score | numeric | uncalibrated model output |
| probability | numeric | calibrated, 0–1; null unless `scored` |
| band | enum `high` \| `medium` \| `low` | null unless `scored` |
| rank_in_owner | int | |
| confidence | enum `normal` \| `low` \| `insufficient_data` | |
| contributions | jsonb | `[{signal, value, contribution}]` sorted by abs |
| reasons | jsonb | top 3 `[{signal, text, direction}]` |
| days_to_renewal, arr | | copied at scoring time |

Unique `(run_id, account_id)`.

## Alerts, actions and drafts

### alert [T]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| run_id | uuid FK | |
| account_id | uuid | |
| raised_for_user_id | uuid | owner when raised; null for unassigned (then visible to all CS managers) |
| score_id | uuid FK account_score | |
| priority | int | trigger priority used for capping (FR-017a) |
| trigger | enum `entered_top_k` \| `risk_jump` \| `severe_event_near_renewal` | FR-017 |
| trigger_detail | jsonb | e.g. previous vs. current rank/probability |
| evidence | jsonb | evidence pack: `[{fact_id, kind, text, raw_event_ids[]}]` (FR-018a) |
| dedupe_key | text unique | `account_id + trigger + condition fingerprint` (FR-016) |
| status | enum `open` \| `acknowledged` \| `closed` | |
| read_at | timestamptz | for the unread count (FR-017b) |

Recipient: an alert is shown to the account's **current** owner (joined at read time) and to CS managers; `raised_for_user_id` records the original recipient (spec edge case "Owner change").

### csm_action [T]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| account_id | uuid FK | |
| user_id | uuid FK | |
| action | enum `contacted` \| `meeting_booked` \| `snoozed` \| `dismissed` | FR-022a |
| snooze_until | date | required when `snoozed` |
| reason | text | required when `dismissed` |
| note | text | |
| created_at | timestamptz | |

Effect on alerts: while an account is snoozed or dismissed, a trigger fires only if its condition fingerprint differs from the one at the time of the action (i.e. something new happened).

### outreach_draft [T]
| Field | Type | Notes |
|---|---|---|
| id | uuid PK | |
| alert_id | uuid FK unique | exactly one draft per alert; re-runs never regenerate (FR-023) |
| generator | enum `llm` \| `template` | |
| fallback_reason | text | why the template was used: `disabled`, `no_api_key`, `timeout`, `api_error`, `validation_failed:<detail>` (FR-023b) |
| llm_model | text | e.g. `claude-sonnet-5-5`; null for template |
| evidence_hash | text | SHA-256 of evidence pack given to the generator |
| internal_summary | text | |
| email_subject, email_body | text | |
| facts_used | text[] | fact_ids; validated ⊆ evidence fact_ids |
| status | enum `draft` \| `approved` \| `discarded` | |
| final_text | text | set on approval |
| approved_by, approved_at | | FR-024 |

## Simulation and audit

### sim_clock
Single row: `current_date`. Advanced by one day by each nightly run (R4). The pending feed lives in `sim.pending_feed_event` (same shape as `raw_event`) in a separate `sim` schema that only the job role (`worklist_job`) can read; the web role (`worklist_web`) has no grant on it. Both roles are subject to RLS on tenant tables.

### access_audit [A]
| Field | Type | Notes |
|---|---|---|
| at | timestamptz | |
| user_id, tenant_id | uuid | |
| route | text | |
| requested_id | text | |
| outcome | enum `denied_cross_tenant` \| `denied_no_session` \| `denied_role` | FR-026 |

Not tenant-readable: the web role has `INSERT` only; operators read it directly. Retained ≥365 days (FR-026a).

## Retention (FR-031)

Raw records 3 years rolling; signal snapshots, scores, alerts, drafts, actions 2 years; access log 365 days. Purge jobs are out of scope for v1: synthetic history is ≤3 years and the demo runs for weeks.

## Validation rules (summary)

- `raw_event.occurred_at` must be ≤ `ingested_at` + 1 day (clock-skew tolerance); otherwise quarantined as `invalid_payload`.
- `account_score.probability` is null iff `state <> 'scored'`.
- `csm_action`: `snooze_until > today` when snoozed; `reason` non-empty when dismissed.
- `outreach_draft.facts_used` ⊆ fact_ids in the linked `alert.evidence`; otherwise the draft is rejected and the template draft stored instead.
- Signal snapshot and model must share `signal_defs_version`, or scoring for that tenant fails with state `no_model` (rather than silently misaligning features).
