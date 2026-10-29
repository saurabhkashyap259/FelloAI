---
description: "Task list for Renewal Risk Worklist for CSMs"
---

# Tasks: Renewal Risk Worklist for CSMs

**Input**: Design documents from `specs/001-renewal-risk-worklist/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. The spec makes testability a requirement (SC-006, SC-008 route-inventory meta-test, FR-016 idempotency, FR-029a evaluation) and research R13 defines the test layers.

**Organization**: Grouped by user story. Story phases are ordered by **dependency**, not strictly by priority. US7 (data) and US5 (model) come before US1 because the worklist cannot show scores without data and an approved model. See Dependencies.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US7 as defined in spec.md
- Paths follow plan.md: single package `src/worklist/`, tests in `tests/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton, tooling, local database

- [X] T001 Create `pyproject.toml` (Python 3.12, src layout, package `worklist`, console script `worklist = worklist.cli:app`) with the dependencies in research.md R15, plus dev deps pytest, ruff and mypy
- [X] T002 Create `docker-compose.yml` with a `postgres:16` service (db `worklist`, healthcheck) and `.env.example` with `DATABASE_URL_OWNER`, `DATABASE_URL_WEB`, `DATABASE_URL_JOB`, `SESSION_SECRET`, `DEMO_MODE`, `DEMO_PASSWORD`, `ANTHROPIC_API_KEY`, `LLM_MODEL=claude-sonnet-5-5`
- [X] T003 [P] Configure ruff, mypy (non-strict) and pytest markers (`model` for slow suites) in `pyproject.toml`; add `.gitignore` (`.env`, `reports/`, `.venv/`, `__pycache__/`)
- [X] T004 [P] Implement settings loader in `src/worklist/config.py` (pydantic-settings, reads the variables from T002, `demo_mode: bool`, `llm_timeout_s = 20`)
- [X] T005 [P] Create Typer CLI skeleton in `src/worklist/cli.py` with command stubs from contracts/cli.md (`db upgrade`, `generate`, `seed-demo`, `train`, `nightly`, `retrain`, `backfill`, `evaluate`), JSON-lines logging to stdout and exit codes 0/1/2/3
- [X] T006 [P] Create shared enums in `src/worklist/domain.py`: Source, Role, AccountStatus, ContractType, ScoreState, Band, Confidence, Trigger, RunType, RunStatus, ModelStatus, ActionType, DraftStatus, DraftGenerator, AuditOutcome (values exactly as in data-model.md)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Database with tenant isolation, sign-in, app shell, route inventory. **No user story work starts until this phase is complete.**

- [X] T007 Set up Alembic in `alembic.ini` and `src/worklist/db/migrations/env.py`, using `DATABASE_URL_OWNER`; wire `worklist db upgrade` in `src/worklist/cli.py`
- [X] T008 Define all tables from data-model.md in `src/worklist/db/tables.py` (SQLAlchemy 2: tenant, tenant_settings, app_user, session, account, account_source_id, account_fact_conflict, renewal_outcome, raw_event, event_account, quarantined_event, pipeline_run, signal_snapshot, model_version, tenant_calibrator, account_score, alert, csm_action, outreach_draft, sim_clock, sim.pending_feed_event, access_audit), with UUIDv7 defaults, unique constraints (raw_event `(tenant_id, source, dedupe_key)`, pipeline_run `(tenant_id, run_type, cutoff_at)`, account_score `(run_id, account_id)`, alert `dedupe_key`, outreach_draft `alert_id`, partial unique "one approved model_version per scope/tenant") and indexes
- [X] T009 Migration `src/worklist/db/migrations/versions/0001_roles.py`: create roles `worklist_owner` (table owner), `worklist_web`, `worklist_job` (both NOINHERIT, non-superuser, not owners); create schema `sim` usable only by `worklist_job`
- [X] T010 Migration `src/worklist/db/migrations/versions/0002_schema.py`: create all tables from T008
- [X] T011 Migration `src/worklist/db/migrations/versions/0003_rls_grants.py`: `ENABLE` + `FORCE ROW LEVEL SECURITY` on every [T] table with policy `tenant_id = current_setting('app.tenant_id', true)::uuid`; grant SELECT/INSERT only (no UPDATE/DELETE) on [A] tables (raw_event, event_account, signal_snapshot, account_score, access_audit); `access_audit` INSERT-only for `worklist_web`; `sim` schema not granted to `worklist_web`; `session` and `tenant` protected by grants
- [X] T012 Implement `src/worklist/db/engine.py`: separate engines for web and job roles; `tenant_scope(conn, tenant_id)` context manager that opens a transaction and runs `SET LOCAL app.tenant_id`; an `operator_conn()` for the owner role used only by CLI/admin code
- [X] T013 [P] Write `tests/conftest.py`: create a test database, run migrations, factories for tenant/user/account, per-test transaction rollback, a `client_as(user)` helper for signed-in requests
- [X] T014 [P] Write `tests/security/test_rls_baseline.py`: with no `app.tenant_id`, every [T] table returns 0 rows for web and job roles; with tenant A set, no tenant B rows are visible; web role cannot UPDATE/DELETE [A] tables, cannot SELECT `access_audit`, cannot read `sim.pending_feed_event`
- [X] T015 Implement auth in `src/worklist/web/auth.py`: argon2 hashing, server-side session rows (256-bit ID in signed HttpOnly SameSite=Lax cookie, 12h expiry), logout deletes the row, per-session CSRF token, in-memory rate limiter (10/min per IP and per email) (FR-027a)
- [X] T016 Implement request dependencies in `src/worklist/web/deps.py`: `current_user` (loads session, or redirects to /login for HTML and returns 401 for `/api/*`), `tenant_conn` (yields a `tenant_scope` connection from the session's tenant), `require_role`, `csrf_protect`, and `deny(requested_id, outcome)`, which writes `access_audit` and raises the shared 404 (FR-026)
- [X] T017 Create the FastAPI app factory in `src/worklist/web/app.py` (Jinja2 templates, static files, identical 404 page for HTML and `{"detail":"Not found"}` for JSON, middleware for sessions/CSRF); base layout `src/worklist/web/templates/base.html` with nav, alerts unread-count slot and run-status banner slot; vendor `src/worklist/web/static/htmx.min.js`
- [X] T018 Implement sign-in routes in `src/worklist/web/routes/auth.py` (`GET/POST /login`, `POST /logout`, generic error, demo account list only when `DEMO_MODE`) and `src/worklist/web/templates/login.html`
- [X] T019 Implement `worklist seed-demo` in `src/worklist/seed.py`: 4 tenants with default `tenant_settings` (K=10, floor 0.15, risk jump 0.15, window 60, caps 3/10, drafting on, source timezones); ~42 CSMs at ~120 accounts each plus one CS manager per tenant, `is_demo=true`; idempotent; refuses to run unless `DEMO_MODE` (FR-027b)
- [X] T020 Implement health routes in `src/worklist/web/routes/health.py`: `/healthz` and `/healthz/pipeline` returning only status and stale-tenant count (FR-027c); the pipeline check uses the operator connection
- [X] T021 Create the route inventory `tests/security/route_inventory.py` (each route mapped to an access rule: public / any-user / csm-own / manager) and meta-test `tests/security/test_route_inventory.py`, which fails if the app registers a route missing from the inventory (SC-008)
- [X] T022 [P] Write `tests/unit/test_auth.py`: password hashing round-trip, session expiry at 12h, logout invalidation, POST without CSRF → 403, 11th sign-in attempt within a minute → 429, same error text for unknown email and wrong password

**Checkpoint**: the database enforces isolation, users can sign in, and the app shell runs.

---

## Phase 3: User Story 7 - Realistic synthetic data (Priority: P1, enabling)

**Goal**: A reproducible generator producing ~5,000 accounts and ~200,000 messy events across five sources and 4 tenants, with ground-truth outcomes and planted drivers, loaded through the real ingestion path.

**Independent Test**: `worklist generate --seed 42` twice gives identical data; `reports/data_profile.json` shows the volumes, defect rates, sparse share, silent-churn cohort and planted-driver table (quickstart §2 row 1).

### Tests for User Story 7

- [X] T023 [P] [US7] Write `tests/unit/test_synth_determinism.py`: two runs with the same seed on a small config produce identical content hashes for accounts, events and outcomes
- [X] T024 [P] [US7] Write `tests/unit/test_synth_profile.py`: on the default config, volumes are within ±10% of targets, all five sources are present, and there are non-zero duplicates, missing fields, CRM/billing conflicts, late (>1 day) and out-of-order events, naive timestamps and unmatched records; sparse accounts are 8–12%; silent churn is 12–18% of churners; the planted-driver table lists 6 drivers
- [X] T025 [P] [US7] Write `tests/unit/test_normalize.py`: UTC normalisation using the source timezone with the original kept in the payload; dedupe key from `source_record_id` or a content hash; `occurred_at > ingested_at + 1 day` → `invalid_payload`
- [X] T026 [P] [US7] Write `tests/unit/test_identity.py`: matching order (source ID before domain), free-mail domains never match, two candidate accounts → `ambiguous_match`, no candidate → `no_match`
- [X] T027 [P] [US7] Write `tests/unit/test_facts.py`: billing wins for ARR/renewal date, CRM wins for owner/segment; a conflict is recorded only beyond ±7 days / ±5% / any owner mismatch; status transitions to `renewal_overdue`, `churned` after grace, `outcome_unknown` when sources contradict
- [X] T028 [P] [US7] Write `tests/invariants/test_ingest_idempotent.py`: ingesting the same batch twice adds no rows and counts duplicates; a quarantined record matched in a later run gets `matched_at` = that cutoff and is invisible to earlier cutoffs

### Implementation for User Story 7

- [X] T029 [US7] Write the frozen generator config in `src/worklist/synth/config.py`: tenant sizes (≈2,600/1,400/800/200), event mix (product 60%, support 15%, email 12%, billing 8%, CRM 5%), defect rates, 85/15 term/monthly split, product change 18 months back, base annual churn 8–12%, the 6 planted drivers with log-odds effects, silent-churn share ~15%, default seed 42 (FR-029a)
- [X] T030 [US7] Implement the latent world in `src/worklist/synth/world.py`: accounts (segment, plan, seats, contract type/start, renewal schedule, domain, source IDs), owner assignment (~120 per CSM, ~3% unassigned), monthly hidden health trajectories over 3 years
- [X] T031 [US7] Implement drivers and outcomes in `src/worklist/synth/drivers.py`: the 6 planted drivers (usage decline, seat under-utilisation, ticket spikes with slow resolution, payment failures, champion departure, no CSM contact), logistic outcome plus noise at each renewal, downgrades, silent-churn archetype per spec Definitions, `renewal_outcome` rows with `recorded_at` = renewal date + grace (90 days term / 30 days monthly) and `is_silent_churn`
- [X] T032 [US7] Implement the five source emitters in `src/worklist/synth/emit.py`: envelopes and event types exactly as in contracts/source-events.md, derived from the latent world (usage from health, tickets from drivers, invoices from contracts, CRM and email activity)
- [X] T033 [US7] Implement defect injection in `src/worklist/synth/defects.py`: retry duplicates, missing fields, CRM/billing conflicts, heavy-tailed `ingested_at` lag, out-of-order delivery, `ticket.updated` before `ticket.created`, naive local timestamps for product events, free-mail/unknown domains, sparse accounts
- [X] T034 [US7] Implement the data profile report in `src/worklist/synth/profile.py`: volumes per source/tenant, defect counts, sparse share, churn rate, silent-churn count, and the planted-driver table (name, definition, prevalence, log-odds effect) → `reports/data_profile.json`
- [X] T035 [US7] Implement envelope normalisation in `src/worklist/ingest/normalize.py`: validation, UTC conversion with `tenant_settings.source_timezones`, dedupe key, invalid-payload detection (contracts/source-events.md §Ingest rules)
- [X] T036 [US7] Implement identity resolution in `src/worklist/ingest/identity.py`: match by `account_source_id`, then by normalised domain (free-mail excluded); insert `event_account` with `matched_at`; quarantine with a reason; `retry_quarantine(tenant, cutoff)` for FR-003a; aged count (>30 days)
- [X] T037 [US7] Implement fact resolution in `src/worklist/ingest/facts.py`: precedence, `account_fact_conflict` rows, account status transitions (`renewal_overdue`, `churned`, `outcome_unknown`)
- [X] T038 [US7] Implement feed delivery in `src/worklist/ingest/feed.py`: read `sim.pending_feed_event` with `ingested_at ≤ cutoff` not yet delivered, normalise, insert into `raw_event` with `ON CONFLICT DO NOTHING`, resolve identities, and return counts (ingested, duplicates, quarantined)
- [X] T039 [US7] Wire `worklist generate` in `src/worklist/cli.py` (with a writer in `src/worklist/synth/writer.py`): write accounts, source IDs and outcomes; write **all** events to `sim.pending_feed_event`; set `sim_clock` to the demo start date (end of history minus `--feed-days`); deliver the backlog through `ingest/feed.py` so history goes through the same ingestion path; write the profile report

**Checkpoint**: realistic, reproducible data is in the database through the real ingestion path.

---

## Phase 4: User Story 5 - Prove the model works (Priority: P2, prerequisite for scoring)

**Goal**: Point-in-time signals, a LightGBM model trained on pooled data with per-tenant calibration, a logistic baseline, a promotion gate, and an evaluation report that answers "how do you know it works".

**Independent Test**: `worklist train --promote` writes `reports/eval_<date>.json` with per-tenant metrics, the baseline comparison, the full-vs-18-month comparison, silent-churn lead times and planted-driver recovery; `/model` shows them for the signed-in tenant only.

### Tests for User Story 5

- [X] T040 [P] [US5] Write `tests/invariants/test_point_in_time.py`: adding events with `ingested_at > C`, links with `matched_at > C`, or outcomes with `recorded_at > C` does not change signals or labels at cutoff C
- [X] T041 [P] [US5] Write `tests/unit/test_promotion_gate.py`: non-inferior on all three and better on hit rate → promote; identical metrics → keep current; worse by >0.01 on any → reject; no current model → compared with the baseline; the decision reason is recorded (FR-013)
- [X] T042 [P] [US5] Write `tests/unit/test_bands.py`: high = top K **and** ≥ floor; a healthy book has fewer than K high; overflow above the floor goes to medium; the medium rule (≥5%, and top 3K or ≥ floor); K and floor read from tenant settings (FR-011)
- [X] T043 [P] [US5] Write `tests/unit/test_dataset_labels.py`: term churn = no renewal within 90 days after the renewal date; monthly churn = cancelled and not reactivated within 30 days; downgrade = negative label; only in-horizon snapshots are labelled
- [X] T044 [US5] Write `tests/model/test_success_criteria.py` (`@pytest.mark.model`): on the default seed, run train/evaluate and assert SC-001, 002, 003, 004, 011 and 012 using the report; on failure, print the report rather than retuning (FR-029a)

### Implementation for User Story 5

- [X] T045 [US5] Implement the signal registry in `src/worklist/signals/registry.py`: ~30 signals, each with name, sources, event types, `occurred_at` window, kind (count/trend/ratio/recency/absence), direction, plain-language reason template, and `days_to_renewal`; plus a `signal_defs_version` hash of the registry
- [X] T046 [US5] Implement point-in-time computation in `src/worklist/signals/compute.py`: for (tenant, cutoff), load matched events with `ingested_at ≤ C` and `matched_at ≤ C`, compute all signals into a DataFrame, `missing_sources` per account, stale-source detection per spec Definitions (max(2 days, 3× median gap over 30 days)), and account state/confidence inputs (record count in 180 days, account age)
- [X] T047 [US5] Implement dataset building in `src/worklist/model/dataset.py`: snapshots every 14 days per account, horizon filter, labels per T043, time split into train/valid/test (test = last ~6 months), and a `history_window` variant (`full` | `recent_18m`)
- [X] T048 [US5] Implement training in `src/worklist/model/train.py`: logistic baseline (median imputer + scaler + L2) and LightGBM with monotone constraints on obvious signals, each for both history windows, on pooled data; tenant-specific LightGBM only when the tenant has ≥1,000 labelled examples and ≥50 churns (FR-012a)
- [X] T049 [US5] Implement per-tenant calibration in `src/worklist/model/calibrate.py`: isotonic, or Platt for tenants with <200 labelled validation examples, fitted only on that tenant's validation data; stored in `tenant_calibrator`
- [X] T050 [US5] Implement banding and states in `src/worklist/model/bands.py`: `assign_states` (insufficient_data, not_due, renewal_overdue, no_model, scored + confidence normal/low) and `assign_bands` per FR-011 (shared by evaluation and scoring)
- [X] T051 [US5] Implement evaluation in `src/worklist/model/evaluate.py`: PR-AUC, ROC-AUC, Brier, 10 equal-frequency calibration bins (≥50 examples), weekly worklist replay over the test window for top-10 hit rate per CSM and lead time (using `bands.py`), silent-churn recall and lead time, planted-driver recovery (top 10 by mean |contribution|), per tenant (≥30 churns) and overall, a pass/fail flag per SC, and the full-vs-18-month comparison
- [X] T052 [US5] Implement the model registry in `src/worklist/model/registry.py`: serialise models to `model_version.artifact`, load the approved model per tenant (tenant-specific if approved, else global), the promotion gate per FR-013 (baseline when no current model), status transitions and retirement, decision reasons, `signal_defs_version` check
- [X] T053 [US5] Wire `worklist train`, `worklist evaluate` and `worklist retrain` in `src/worklist/cli.py`: build the dataset, train both windows, calibrate, evaluate, register candidates, apply the gate (`--promote`), write `reports/eval_<date>.json`, and record each retrain attempt with its outcome (FR-014a)
- [X] T054 [US5] Implement `GET /model` in `src/worklist/web/routes/model.py` and `src/worklist/web/templates/model.html`: this tenant's metrics plus pooled totals only (FR-025a), reliability chart as inline SVG, baseline and history-window comparisons, candidate history with decisions, last retrain outcome, and a notice if no retrain succeeded in 14 days

**Checkpoint**: an approved, evaluated model exists, and the evaluation answers "how do you know it works".

---

## Phase 5: User Story 1 - Monday prioritised worklist (Priority: P1) 🎯 MVP

**Goal**: A CSM signs in and sees their accounts grouped and ranked, with reasons, confidence, days to renewal and ARR, and can record actions.

**Independent Test**: After `worklist backfill` and one scoring run, sign in as a CSM and check the groups, ordering, run metadata and that only their accounts appear; snooze an account and see it move to Snoozed (quickstart §2 rows 2–3).

### Tests for User Story 1

- [X] T055 [P] [US1] Write `tests/unit/test_score.py`: contributions sum to the raw score's log-odds; top 3 reasons ordered by absolute contribution with rendered text; `signal_defs_version` mismatch → `no_model`; out-of-horizon → `not_due` with no probability
- [X] T056 [P] [US1] Write `tests/integration/test_worklist_page.py`: a CSM sees only their accounts; group order per FR-019; rows sorted by probability within groups; run ID, cutoff and model version shown; a failed latest run shows the last successful results with a banner (FR-018); the manager `owner` filter is ignored for CSMs
- [X] T057 [P] [US1] Write `tests/integration/test_actions.py`: snooze requires a future date and moves the account to Snoozed until it ends; dismiss requires a reason; contacted and meeting_booked are recorded with who and when; a CS manager can act on any tenant account; missing CSRF → 403

### Implementation for User Story 1

- [X] T058 [US1] Implement scoring in `src/worklist/model/score.py`: for (tenant, cutoff, run), compute signals, load the approved model and calibrator, compute raw score, calibrated probability and LightGBM `pred_contrib` contributions, reasons, states, confidence, bands and `rank_in_owner`; write `signal_snapshot` and `account_score` rows (with model and signal-definition versions)
- [X] T059 [US1] Implement the shared per-tenant run core in `src/worklist/agent/pipeline.py`: `run_tenant_cutoff(tenant, cutoff, run_type, steps)` creates or resumes the `pipeline_run` (unique per tenant, type and cutoff; a succeeded run is a no-op), runs the steps (feed ingest, quarantine retry, facts, scoring) in one tenant transaction, and records status and counts
- [X] T060 [US1] Wire `worklist backfill --from --to --every` in `src/worklist/cli.py`, using `run_tenant_cutoff` with `run_type=backfill` and no alerts, to build risk history
- [X] T061 [US1] Implement the worklist service in `src/worklist/worklist.py`: latest succeeded run per tenant, the groups from FR-019 (Renewal overdue, Act this week, Watch, Healthy, Insufficient data, Snoozed, Later renewals, No model yet), the active snooze overlay from `csm_action`, and manager queues (Unassigned, Outcome unknown)
- [X] T062 [US1] Implement `GET /` and `GET /worklist` in `src/worklist/web/routes/worklist.py` (CSM own accounts; manager CSM picker and queues) with `src/worklist/web/templates/worklist.html` and the row partial `src/worklist/web/templates/_row.html` (probability %, band, 3 reasons, confidence, days to renewal, ARR, latest action, "new" marker slot)
- [X] T063 [US1] Implement actions in `src/worklist/actions.py` and `POST /accounts/{account_id}/actions` in `src/worklist/web/routes/worklist.py`: validation (future snooze date, non-empty dismiss reason), store `csm_action`, return the updated row via HTMX, 422 on invalid input (FR-022a)

**Checkpoint**: MVP. A CSM can open the worklist on Monday and act on it.

---

## Phase 6: User Story 2 - Trace any number back to its source (Priority: P1)

**Goal**: Every displayed number links to its stored score record, model and signal-definition versions, signal values and contributions, and the source records behind each reason, including signals based on the absence of activity.

**Independent Test**: From any worklist row, open the account and drill into each reason; every number matches the stored record, and the source records fall inside the signal's window and the cutoffs (quickstart §2 row 4).

### Tests for User Story 2

- [X] T064 [P] [US2] Write `tests/integration/test_traceability.py`: the displayed probability equals `account_score.probability`; drill-down records satisfy `ingested_at ≤ cutoff`, `matched_at ≤ cutoff` and the `occurred_at` window; an absence signal returns the searched window, "no qualifying records", and the latest earlier record or "none since account start"; an older score page is unchanged after a newer run; fact conflicts are shown
- [X] T065 [P] [US2] Write `tests/invariants/test_reproducibility.py`: recomputing a stored score from `raw_event`, `event_account`, the referenced `model_version` and signal-definition version reproduces the probability and contributions exactly (FR-002)

### Implementation for User Story 2

- [X] T066 [US2] Implement drill-down queries in `src/worklist/signals/drilldown.py`: per signal kind, return the contributing raw events (capped at 50, with total count) or the absence/recency result (R12a)
- [X] T067 [US2] Implement account routes in `src/worklist/web/routes/accounts.py`: `GET /accounts/{id}`, `/accounts/{id}/scores/{score_id}`, `/accounts/{id}/signals/{signal_name}?score_id=`, and `GET /api/accounts/{id}/scores/{score_id}` (JSON per contracts/http.md)
- [X] T068 [US2] Create `src/worklist/web/templates/account.html` (facts with conflicts, current score with run/model/signal-definition versions and cutoff, full contribution list, risk-history inline SVG across runs, actions, alerts, drafts) and `src/worklist/web/templates/signal.html` (definition, window, value, records table, or the absence statement)

**Checkpoint**: every number on screen can be traced to its source.

---

## Phase 7: User Story 6 - Separate customer organisations (Priority: P1)

**Goal**: Prove and harden isolation across every route: cross-tenant and cross-CSM requests are indistinguishable from "not found", audited, and pooled-model artefacts leak nothing.

**Independent Test**: With 3+ tenants seeded, run the security suite; every route in the inventory is covered, and manual URL tampering returns 404 with an audit row (quickstart §2 row 8).

### Tests for User Story 6

- [X] T069 [P] [US6] Write `tests/security/test_cross_tenant.py`: for every route in the inventory that takes an ID, a tenant A user with tenant B IDs gets a 404 response byte-identical to a non-existent ID (HTML and JSON), plus an `access_audit` row `denied_cross_tenant`
- [X] T070 [P] [US6] Write `tests/security/test_role_access.py`: a CSM requesting another CSM's account, alert or draft → 404 + `denied_role`; a CS manager can access them; a CSM's `owner=` query parameter is ignored
- [X] T071 [P] [US6] Write `tests/security/test_no_session.py`: every protected HTML route redirects to `/login`; every `/api/*` route returns 401; expired sessions are rejected
- [X] T072 [P] [US6] Write `tests/security/test_disclosure.py`: health endpoints contain no tenant names or IDs; the `/model` page for tenant A contains no other tenant's names or per-tenant metrics; the demo list is hidden when `DEMO_MODE=false`

### Implementation for User Story 6

- [X] T073 [US6] Migration `src/worklist/db/migrations/versions/0004_audit_classify.py`: a `SECURITY DEFINER` function `classify_missing_resource(kind text, id uuid)` returning only `'other_tenant'` or `'missing'` (no data), executable by `worklist_web`, used to choose the audit outcome
- [X] T074 [US6] Implement `load_owned(kind, id, user, conn)` in `src/worklist/web/deps.py`: loads the account/alert/draft under RLS, enforces CSM ownership (via the account's current owner), and on a miss calls `classify_missing_resource` and `deny()`; replace direct lookups in `src/worklist/web/routes/accounts.py` and `src/worklist/web/routes/worklist.py` with it
- [X] T075 [US6] Complete `tests/security/route_inventory.py` with every route added so far and confirm the meta-test passes

**Checkpoint**: isolation is enforced by the database and verified on every route.

---

## Phase 8: User Story 3 - Unattended nightly agent with change alerts (Priority: P2)

**Goal**: A scheduled nightly job advances the clock, ingests, rescores, raises capped and deduplicated alerts with gathered evidence, catches up failed tenants, and shows run status, with no human involved.

**Independent Test**: `worklist nightly` on consecutive simulated days creates run rows and only material-change alerts; re-running a cutoff creates nothing new; 7 simulated nights all succeed (quickstart §2 row 6, §4).

### Tests for User Story 3

- [X] T076 [P] [US3] Write `tests/unit/test_triggers.py`: entered top K (vs. previous successful run), risk jump ≥0.15 vs. the score 7 days earlier, each severe-event condition within 60 days of renewal, snooze/dismiss suppression, and a new material change overriding it (spec Definitions)
- [X] T077 [P] [US3] Write `tests/unit/test_alert_caps.py`: at most 3 per CSM per night and 10 per rolling 7 days; priority order severe → entered top K → risk jump, then probability × ARR; suppressed count recorded; unassigned-account alerts addressed to managers
- [X] T078 [P] [US3] Write `tests/integration/test_nightly.py`: the clock advances once per invocation; re-running the same cutoff gives identical scores and no new alerts or drafts; an injected failure for one tenant → run `partial`, other tenants succeed, and the next run catches up the missed cutoff first; >3 missed → `needs_attention`; a stale source → low confidence, not "no activity"; lock held → exit code 3; no approved model → `no_model` states and no model alerts
- [X] T079 [P] [US3] Write `tests/integration/test_seven_nights.py` (`@pytest.mark.model`): 7 consecutive nightly runs on the default data all succeed, each within 30 minutes, with an average of ≤5 alerts per CSM per week (SC-007, SC-009)

### Implementation for User Story 3

- [X] T080 [US3] Implement the sim clock in `src/worklist/agent/clock.py`: read, and advance by one day exactly once per nightly invocation under the advisory lock
- [X] T081 [US3] Implement triggers and alerts in `src/worklist/agent/alerts.py`: compute triggers per spec Definitions from the current vs. previous/7-day-earlier scores and new severe events; condition fingerprint and `dedupe_key`; snooze/dismiss suppression with the new-material-change override; per-CSM caps and priority (FR-017a); `raised_for_user_id` (null for unassigned); suppressed counts
- [X] T082 [US3] Implement the evidence builder in `src/worklist/agent/evidence.py`: for each alert, a pack of facts with stable `fact_id`s (usage trend, recent tickets, billing events, last CSM contact, CRM changes), each with plain text and `raw_event_ids`, built only from data at or before the cutoff (FR-018a)
- [X] T083 [US3] Extend `src/worklist/agent/pipeline.py` with `nightly()`: Postgres advisory lock (exit 3 if held), advance clock, then per tenant catch up missed cutoffs oldest-first (max 3, else set `needs_attention` and jump to the latest), and run steps feed → quarantine retry → facts → score → alerts → evidence; per-tenant transactions; overall status succeeded/partial/failed and exit codes 0/2/1
- [X] T084 [US3] Wire `worklist nightly` (`--cutoff`, `--tenant`, `--no-advance`, `--no-llm`) in `src/worklist/cli.py`
- [X] T085 [US3] Implement the alerts inbox in `src/worklist/web/routes/alerts.py` (`GET /alerts`, `GET /alerts/{id}` marks read, `POST /alerts/{id}/acknowledge`) with `src/worklist/web/templates/alerts.html` and `alert.html` (trigger detail, evidence linked to source records, "originally raised for"); add the unread count to the base layout context and "new" markers in `_row.html` (FR-017b)
- [X] T086 [US3] Implement `GET /runs` (manager only) in `src/worklist/web/routes/runs.py` with `src/worklist/web/templates/runs.html` (status, cutoff, counts including suppressed alerts, aged quarantine and stale sources, catch-up flag); add the "latest run did not complete" banner to the base layout (FR-018)
- [X] T087 [US3] Add the new routes to `tests/security/route_inventory.py` and extend `tests/security/test_cross_tenant.py` and `test_role_access.py` parametrisation to cover alert IDs

**Checkpoint**: the system runs on its own every night and only raises alerts on material changes.

---

## Phase 9: User Story 4 - Drafted outreach, human-approved (Priority: P3)

**Goal**: Each alert gets exactly one draft (internal summary + customer email) grounded only in its evidence, from Claude with a validator and a template fallback; approval records who, when and the final text; nothing is sent by the system.

**Independent Test**: Open an alert and see a draft citing evidence facts; approve it to get the Copy and mailto options with the approval recorded; with no API key, a labelled template draft appears instead (quickstart §2 row 7).

### Tests for User Story 4

- [X] T088 [P] [US4] Write `tests/unit/test_draft_validator.py`: rejects schema errors, empty or unknown `facts_used`, numbers or dates not present in the evidence pack, and banned words in the email body (risk, score, churn, model, probability); accepts a valid draft
- [X] T089 [P] [US4] Write `tests/unit/test_evidence_minimisation.py`: the serialised pack contains only the allowed fields; no contact names, emails, raw payloads or ticket bodies; only one tenant (FR-023a)
- [X] T090 [P] [US4] Write `tests/integration/test_drafts.py` (LLM client mocked): one draft per alert, and re-runs don't regenerate it; fallback to a template with `fallback_reason` for disabled drafting, no key, timeout, API error and validation failure; approval by the owner or a manager records approver, time and final text; another CSM gets 404; the codebase has no outbound email path

### Implementation for User Story 4

- [X] T091 [US4] Implement template drafts in `src/worklist/agent/templates.py`: a deterministic internal summary and customer email built from the evidence facts, with `facts_used` listing the facts referenced
- [X] T092 [US4] Implement LLM drafting in `src/worklist/agent/drafts.py`: build the prompt per contracts/llm-draft.md (versioned prompt constant); call Claude via the `anthropic` SDK with `settings.llm_model` (default `claude-sonnet-5-5`), structured JSON output and a 20 s timeout (check current SDK usage with the `claude-api` skill before writing this); validate; fall back to the template; store `outreach_draft` with `generator`, `llm_model`, `evidence_hash` and `fallback_reason`
- [X] T093 [US4] Add the drafting step to `nightly()` in `src/worklist/agent/pipeline.py`: one draft per new alert, skipped if one exists, respecting `tenant_settings.llm_drafting_enabled` and `--no-llm`
- [X] T094 [US4] Implement `POST /drafts/{id}/approve` and `POST /drafts/{id}/discard` in `src/worklist/web/routes/drafts.py` (owner or manager only, CSRF), and add the draft panel to `src/worklist/web/templates/alert.html`: editable text, Template label with reason, cited facts highlighted, and after approval the Copy button plus a `mailto:` link
- [X] T095 [US4] Add the draft routes to `tests/security/route_inventory.py` and extend the cross-tenant and role-access tests to cover draft IDs

**Checkpoint**: all user stories are complete.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Deploy, document, and verify end to end

- [X] T096 [P] Write a `Dockerfile` (python:3.12-slim, uv install, non-root user, `uvicorn worklist.web.app:app`)
- [X] T097 [P] Write `render.yaml`: web service (pre-deploy `worklist db upgrade`), cron `worklist nightly` at `0 2 * * *`, cron `worklist retrain` at `0 3 * * 0`, managed Postgres 16, env vars from T002 (`DEMO_MODE=true` for the demo)
- [X] T098 Write `README.md`: problem framing (from SOLUTION_BRIEF.md), architecture diagram, decisions with links to research.md, planted-driver table and evaluation results from the reports, what was deliberately skipped, **what breaks first at 10x** (single-process feature computation in pandas, in-memory rate limiter, cron without an orchestrator, model artefacts in Postgres, RLS on a single database), and how to run it
- [X] T099 Run the quickstart end to end on a clean checkout, following `specs/001-renewal-risk-worklist/quickstart.md`, and fix any drift between the commands and the docs
- [X] T100 Run `ruff check`, `mypy src` and the full `pytest` (including `-m model`), and fix any failures
- [ ] T101 Deploy to Render: run `seed-demo`, `generate`, `train --promote` and `backfill`; confirm the cron jobs fire; confirm SC-013 by signing in as a CSM of each tenant on the live URL
- [X] T102 [P] Write `docs/walkthrough.md`: an outline for the slide deck and Loom (problem reframing, where I disagreed with the VP, architecture, how I know it works, isolation proof, what breaks at 10x)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories.
- **US7 (Phase 3)** depends on Foundational.
- **US5 (Phase 4)** depends on US7 (needs data).
- **US1 (Phase 5)** depends on US5 (needs an approved model and `bands.py`).
- **US2 (Phase 6)** depends on US1 (needs score records and the account route).
- **US6 (Phase 7)** depends on US1 and US2 (hardens their routes); its baseline is already in Phase 2.
- **US3 (Phase 8)** depends on US1 (the run core and scoring); independent of US2 and US6, but should run after US6 so new routes join a hardened pattern.
- **US4 (Phase 9)** depends on US3 (alerts and evidence).
- **Polish (Phase 10)** depends on all the story phases you include.

### Why this order differs from priority order

US5 is P2 but is a hard prerequisite for US1 (P1): the worklist ranks by a fitted model, which the exercise requires, so there is no meaningful MVP without it. US6's core guarantees (RLS, sessions, 404 + audit helper, route inventory) are in Foundational, so isolation exists from the first route; Phase 7 is the exhaustive proof.

### Within Each User Story

- Write the tests first and confirm they fail, then implement.
- Data and services before routes, routes before templates.
- Finish each story at its checkpoint before starting the next.

### Parallel Opportunities

- Phase 1: T003–T006 in parallel after T001.
- Phase 2: T013, T014 and T022 in parallel once T012 exists.
- US7: all test tasks T023–T028 in parallel; T030–T034 (synth) can run in parallel with T035–T038 (ingest) once T029 is done.
- US5: T040–T043 in parallel; T049 and T050 in parallel after T048.
- US6: T069–T072 in parallel.
- US3: T076–T079 in parallel; T081 and T082 in parallel.
- US4: T088–T090 in parallel; T091 and T092 in parallel.
- Polish: T096, T097 and T102 in parallel.

---

## Parallel Example: User Story 7

```bash
# Tests together:
Task: "T023 [US7] tests/unit/test_synth_determinism.py"
Task: "T024 [US7] tests/unit/test_synth_profile.py"
Task: "T025 [US7] tests/unit/test_normalize.py"
Task: "T026 [US7] tests/unit/test_identity.py"

# After T029 (config), two independent streams:
Task: "T030–T034 synth/world.py, drivers.py, emit.py, defects.py, profile.py"
Task: "T035–T038 ingest/normalize.py, identity.py, facts.py, feed.py"
```

## Parallel Example: User Story 3

```bash
Task: "T076 tests/unit/test_triggers.py"
Task: "T077 tests/unit/test_alert_caps.py"
Task: "T081 src/worklist/agent/alerts.py"
Task: "T082 src/worklist/agent/evidence.py"
```

---

## Implementation Strategy

### MVP First

1. Phase 1 + Phase 2 (isolation and sign-in from day one).
2. US7 → US5 → US1. **Stop and validate**: a CSM can sign in and act on a model-ranked worklist backed by an evaluated model. This already meets the non-negotiables except "runs without a human".
3. Deploy the MVP early (T096, T097, T101 partially) so the live URL exists.

### Incremental Delivery

1. + US2 → traceability (non-negotiable).
2. + US6 → isolation proof across all routes (non-negotiable).
3. + US3 → unattended agent (non-negotiable). **At this point all five non-negotiables are met.**
4. + US4 → drafted outreach. If time runs short, ship with template drafts only (skip T092), as the plan allows.
5. Polish → README, walkthrough, final deploy.

### Cut Line (if time runs short)

Never cut: Phases 1–8. Cut in this order: T092 (LLM drafting, keep templates) → T079 (7-night performance suite, run manually instead) → T054 charts (keep tables) → T102.

---

## Notes

- [P] = different files, no dependency on an incomplete task.
- Every route added must be registered in `tests/security/route_inventory.py`, or the meta-test fails.
- Never edit `src/worklist/synth/config.py` after the first evaluation without documenting why and regenerating reports (FR-029a).
- Commit after each task or logical group.
