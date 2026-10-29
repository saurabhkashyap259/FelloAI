# Phase 0 Research: Renewal Risk Worklist

All Technical Context unknowns are resolved below. Each entry: **Decision / Rationale / Alternatives considered**.

---

## R1. Language and application shape

- **Decision**: Python 3.12, one repository, one deployable web app plus a job entrypoint that share the same package. Web UI is server-rendered (FastAPI + Jinja2 templates + HTMX for small interactions).
- **Rationale**: The modelling, data generation and web layer share types and code (signal definitions are used by training, scoring *and* the drill-down view). A server-rendered UI means one language, one deploy, no API/client drift, and every page reads directly from stored score records, which keeps traceability simple. The exercise grades judgment, not UI polish.
- **Alternatives considered**: Next.js/React SPA + Python API: nicer UX, but doubles the surface to build and defend in a week. Streamlit: fast, but weak for multi-user auth, tenant isolation and per-request scoping.

## R2. Primary store and tenant isolation

- **Decision**: PostgreSQL 16. Every tenant-owned table has `tenant_id NOT NULL`; Row-Level Security is `ENABLE`d and `FORCE`d on all of them, with policy `tenant_id = current_setting('app.tenant_id')::uuid`. The app connects as a non-owner, non-superuser role. Each request/job opens a transaction and runs `SET LOCAL app.tenant_id = …` derived from the server-side session (never from request input). If the setting is missing, `current_setting(..., true)` returns NULL and the policy matches nothing, so the default is *deny*.
- **Rationale**: Isolation enforced by the database survives application bugs (a forgotten `WHERE tenant_id = …` returns zero rows instead of leaking). This is the strongest defensible answer to a non-negotiable at low cost.
- **Alternatives considered**: App-level filtering only: one missed filter leaks data. Schema-per-tenant or database-per-tenant: stronger physical isolation, but painful migrations and pooled training, and overkill at 3–5 tenants. Noted in README as the path for regulated customers.

## R3. Synthetic data design

- **Decision**: A seeded, deterministic generator (`numpy.random.Generator(PCG64(seed))`) that simulates the *world first, then the observations*:
  1. **Latent world**: per tenant, accounts with segment, plan, seats, contract start, renewal cycle (annual/monthly), and a hidden monthly "health" state that drifts. Planted churn drivers: usage decline, seat under-utilisation, ticket spikes + slow resolution, payment failures, champion leaving (email bounce / CRM contact change), no CSM touch, and a "silent churn" archetype (support + usage signals only, no CRM/email warnings). Outcome at each renewal = logistic function of drivers + noise, so the model has learnable but imperfect signal (target base churn ~8–12% annual).
  2. **Observation layer** (five sources) emitted from the latent world with realistic defects: duplicates (webhook retries, ~2–3%), missing fields, conflicting renewal date/ARR between CRM and billing (~5%), late arrival (`ingested_at` lag drawn from a heavy-tailed distribution; some events days late), out-of-order delivery, timezone-naive timestamps from one source, unmatched records (unknown domain / deleted account, ~1%), sparse accounts (~10% with <5 events), and up to 3 years of history with a product change ~18 months ago that alters usage patterns.
- **Rationale**: Ground truth makes the system verifiable: we can check the model recovers planted drivers (SC-011) and that silent-churn accounts are caught (SC-012). Determinism makes tests and the demo reproducible.
- **Alternatives considered**: Faker-style independent random rows: no causal structure, so the model would learn nothing real and the evaluation would be meaningless. Public churn datasets: wrong shape (single table, no events, no multi-source mess).
- **Silent churn** (spec Definitions): ≈15% of churners are generated from the silent archetype: no CRM/email/billing warnings in the 120 days before renewal, and at least two of usage decline ≥30%/60d, 2+ high/urgent tickets, a "bad" CSAT. The flag is stored as ground truth and never used as a feature.
- **Driver documentation** (FR-029): the generator emits a planted-driver table (name, definition, prevalence, log-odds effect) into `reports/data_profile.json`; the README renders it.
- **Contract types**: ~85% annual term contracts, ~15% monthly plans.
- **Volumes**: 4 tenants (≈2,600 / 1,400 / 800 / 200 accounts ≈ 5,000), ≈200,000 events (product usage ≈60%, support ≈15%, email ≈12%, billing ≈8%, CRM ≈5%), ≈42 CSMs at ~120 accounts each.

## R4. Simulating "nightly arrival" in a live demo

- **Decision**: The generator writes two things: a **historical backlog** (everything with `ingested_at` ≤ the demo start date) and a **pending feed** (events whose `ingested_at` falls in the next N simulated days). A simulated business clock (`sim_clock` table, per deployment) advances one day per nightly run; the ingest step "delivers" feed events with `ingested_at` ≤ new clock date through the same ingestion code real connectors would call.
- **Rationale**: Without this, the nightly job would rescore unchanged data and never produce alerts, so the unattended loop could not be demonstrated. Using `ingested_at` as the delivery key also exercises late and out-of-order handling for real.
- **Alternatives considered**: Generate random new events at run time: non-deterministic, breaks ground truth. Real wall-clock only: nothing would change during a review window.

## R5. Point-in-time correctness

- **Decision**: Two timestamps on every raw event: `occurred_at` (business time) and `ingested_at` (system time). Signals as of cutoff `C` use events with `ingested_at ≤ C`, windowed by `occurred_at`. Training examples are snapshots at multiple historical cutoffs (every 14 days per account), each labelled by whether the account churned at a renewal falling in `(C, C+90d]`. Only accounts with a renewal in that window are labelled examples; the model therefore predicts "will churn at its next renewal, given that renewal is within 90 days", and `days_to_renewal` is an input signal. Accounts outside the horizon are **not scored** (state `not_due`, "Later renewals" group, spec FR-008), because the model has no training examples for them. Monthly plans: renewal date = current period end, so they are always inside the horizon; churn = cancelled and not reactivated within 30 days. Labels become available at `renewal_date + grace` (90 days term / 30 days monthly) via `renewal_outcome.recorded_at`, which is also cut off point-in-time.
- **Rationale**: Prevents leakage, the most common reason churn models look good offline and fail live. Using `ingested_at` for the cutoff means a late event never retroactively changes what the model "knew".
- **Alternatives considered**: One snapshot per account at a fixed date: too few examples and ignores the trajectory. Random train/test split: leaks future into past.

## R6. Model, baseline, calibration, explanations

- **Decision**:
  - **Model**: LightGBM binary classifier (≈30 signals, monotone constraints on a few obvious signals such as payment failures ↑ ⇒ risk ↑).
  - **Baseline**: L2 logistic regression on standardised signals. The GBM must beat it (FR-013 gate). A trivial "days since last login" ranker is also reported as a sanity floor.
  - **Calibration**: Per-tenant isotonic regression on the validation window (Platt scaling for tenants with <200 labelled examples). This implements the pooled-model + per-tenant calibration decision.
  - **Explanations**: LightGBM native `predict(pred_contrib=True)` (exact TreeSHAP contributions, no extra dependency), mapped to plain-language reason templates per signal.
  - **Tenant-specific model**: trained only when tenant has ≥ 1,000 labelled examples and ≥ 50 churn events; adopted only if it passes the promotion gate against the pooled model on that tenant's test window.
  - **History window**: each training run fits two candidates, one on full history and one on the most recent 18 months (the synthetic product change is ~18 months back), and keeps whichever passes the gate with the better top-10 hit rate (spec FR-012b).
  - **Isolation of pooled artefacts**: contributions are per-account; calibrators are fitted per tenant on that tenant's validation data only; the in-app model page shows the viewer's tenant metrics plus pooled totals, never another tenant's breakdown (spec FR-025a). Full per-tenant breakdowns exist only in operator report files.
- **Rationale**: GBMs handle mixed, missing and non-linear signals well with little tuning; native contributions give exact, cheap, reproducible explanations. Calibration is what makes "30% risk" mean something (SC-004).
- **Alternatives considered**: Survival models (Cox / time-to-event): conceptually a good fit for "when", but harder to explain and calibrate in the time available; noted as a future improvement. Deep models: no benefit at this size.

## R7. Evaluation protocol

- **Decision**: Rolling time-based split over the 3-year history: train on cutoffs before T1, calibrate/validate on [T1, T2), test on [T2, T3) (≈ last 6 months). Report per tenant and overall: PR-AUC, ROC-AUC, Brier score + reliability bins, **top-10 hit rate per CSM** (replaying weekly worklists in the test window), **lead time** (days before renewal a churned account first entered its CSM's top 10), silent-churn recall, baseline comparison, and planted-driver recovery (rank of planted drivers in mean |contribution|). Calibration uses 10 equal-frequency bins; bins with fewer than 50 examples are reported but excluded from SC-004. SC-002's base rate is the churn rate among all scored (in-horizon) accounts of the same tenant in the test period. Per-tenant criteria apply to tenants with ≥30 churn events in the test period. **Promotion gate** (spec FR-013): on the same latest test window, the candidate must be no worse than current by >0.01 on top-10 hit rate, PR-AUC and Brier, **and** strictly better on top-10 hit rate or PR-AUC (prevents promoting statistically identical models and churning rankings). With no current model, the comparison is against the logistic baseline. **Planted-driver recovery** (SC-011): ≥5 of the 6 planted drivers in the top 10 signals by mean |contribution| on the test window. **Generator freeze** (FR-029a): generator config is committed before the first evaluation; shortfalls are reported, not tuned away.
- **Rationale**: The metrics mirror how the system is used (a weekly top-10 list), not just generic classifier quality.
- **Alternatives considered**: AUC only: doesn't reflect a capacity-limited list and hides calibration problems.

## R8. Risk bands and confidence

- **Decision**: Bands are per CSM and capacity-based (spec FR-011): `high` = within the CSM's top K by calibrated probability (K default 10) **and** probability ≥ floor (default 0.15, so a CSM with a healthy book isn't told 10 accounts are "high"); `medium` = not high, probability ≥ 0.05, and (rank ≤ 3K **or** probability ≥ floor, which catches overflow when more than K accounts exceed the floor); else `low`. States and confidence per spec Definitions: `insufficient_data` if <5 matched records in 180 days or account age <60 days with no usage; `low` confidence if any normally-present source is stale (no data for > max(2 days, 3 × median delivery gap over the prior 30 days)) or >30% of signals missing; otherwise `normal`. Non-scored states: `not_due` (outside horizon), `renewal_overdue`, `no_model`.
- **Rationale**: Matches the brief: bands derive from capacity, unknowns are never green.
- **Alternatives considered**: Fixed thresholds (e.g. >0.5 = red): band sizes swing with base rate and give no guidance on where to start.

## R9. Unattended orchestration

- **Decision**: Two CLI jobs, `worklist nightly` and `worklist retrain`, triggered by the hosting platform's cron (Render Cron Jobs: nightly 02:00 UTC, retrain Sundays 03:00 UTC). Each run: acquires a Postgres advisory lock (no overlapping runs), creates a `pipeline_run` row keyed by `(run_type, cutoff_date)` with a unique constraint (idempotency: a second trigger for the same cutoff either resumes a failed run or exits as no-op), runs per tenant in its own transaction so one tenant's failure doesn't block others, and marks the run `succeeded` / `partial` / `failed`. Alerts have a unique `dedupe_key` so re-runs cannot duplicate them, and drafts are unique per alert. **Clock and catch-up** (spec FR-014b): the sim clock advances exactly once per nightly invocation, before tenant processing, so one tenant's failure never stalls others. At the start of each run, any tenant with failed/partial cutoffs is caught up oldest-first (max 3); beyond that the tenant is flagged `needs_attention` and resumes at the latest cutoff. **Alert caps** (FR-017a): triggers are computed for all accounts, then per CSM sorted by (trigger priority, probability × ARR); the first 3 (and within the rolling-7-day cap of 10) become alerts, the rest are counted as `alerts_suppressed`. **First deploy** (spec edge case "No approved model"): setup runs `train --promote`, where the first candidate is gated against the baseline; until a model is approved, accounts get state `no_model` and no model-based alerts fire. A `/admin/runs` page and a `GET /healthz/pipeline` endpoint expose last-run status (stale if no success in 26h).
- **Rationale**: External cron survives web-process restarts and sleeping instances, unlike an in-process scheduler. Idempotency and locking are what make "runs without a human" safe.
- **Alternatives considered**: APScheduler inside the web process: dies with the process, and runs twice with two web replicas. Airflow/Dagster/Temporal: real value at scale, too heavy for this scope; named in README as the 10x step.

## R10. Agentic evidence gathering and drafting (LLM use)

- **Decision**: The nightly agent is a deterministic pipeline with one bounded LLM step. For each new alert, deterministic code assembles an **evidence pack** (JSON): the score record, top contributions, and the specific source records behind them (last N tickets, usage trend points, billing events, last CSM contact), each with a stable `fact_id`. Claude (`claude-sonnet-5-5`, configurable) receives only that pack and returns structured output: `internal_summary`, `email_subject`, `email_body`, and `facts_used: [fact_id]`. A validator rejects any draft citing unknown fact IDs, or containing numbers or dates not present in the pack, and falls back to a deterministic template draft. The prompt, model ID and evidence pack hash are stored with the draft. With no API key configured, or with drafting disabled in the tenant's settings, the template path is used, so the system still works. **Data minimisation** (spec FR-023a): the pack contains account name, ARR, renewal date, fact texts and the CSM's first name only. No customer contact names, emails or raw payloads, and one request never mixes tenants. The Anthropic API does not train on API inputs by default; this is recorded as an assumption in the spec.
- **Rationale**: Satisfies "LLM never scores" (FR-008), makes every drafted claim traceable (FR-023), and keeps the agent's permissions minimal (read evidence, write drafts; no outbound email, no data writes).
- **Alternatives considered**: Free-form tool-calling agent querying the database: more "agentic"-looking, but unbounded, hard to make traceable and safe across tenants. Pure template drafts: safe, but the summaries add real CSM value. Kept as the fallback.

## R11. Authentication and sessions

- **Decision**: Email + password sign-in, passwords hashed with argon2 (`argon2-cffi`), server-side session ID in a signed, HttpOnly, SameSite=Lax cookie; session row stores `user_id`, `tenant_id`, `role`. CSRF token on all POST forms. Sessions expire 12h after sign-in and are deleted server-side on sign-out; sign-in is rate-limited to 10 attempts/min per IP and per email (in-memory limiter is sufficient for one web instance; noted in README as a 10x item). Demo accounts and the credential list on the sign-in page exist only when `DEMO_MODE=true` (spec FR-027b). The access audit log is in a table the web role can insert into but not select from; operators read it directly (FR-026a).
- **Rationale**: Real server-enforced identity is required for isolation to be meaningful (clarification Q3); this is the smallest correct version.
- **Alternatives considered**: OAuth/SSO (out of scope); JWT in localStorage (worse for XSS, no benefit here).

## R12. Identity resolution and source precedence

- **Decision**: Deterministic matching in priority order: (1) exact source ID map (Salesforce Account ID ↔ Stripe customer ID ↔ product org ID ↔ Zendesk org ID), (2) normalised email domain (lower-cased, `www.` stripped, free-mail domains like gmail.com never used for matching), (3) otherwise quarantine with reason. Dedupe key = `(tenant_id, source, source_record_id)`, falling back to a content hash for sources without stable IDs. Precedence for account facts: billing for ARR and renewal date, CRM for owner and segment; disagreements beyond a tolerance (renewal date ±7 days, ARR ±5%, any owner mismatch) are recorded as `account_fact_conflict` and shown on the account page. Quarantined records are retried every nightly run; a match inserts an `event_account` link with `matched_at` = that run's cutoff, so only later runs see it (spec FR-003a). Records unmatched >30 days are counted as aged.

## R12a. Absence-based signal drill-down

- **Decision**: Each signal in the registry declares `kind` ∈ {`count`, `trend`, `ratio`, `recency`, `absence`}. For `absence`/`recency` signals the drill-down query returns the window searched, an explicit "no qualifying records in window" result, and the latest qualifying record before the window (spec FR-021).
- **Rationale**: "No CSM contact in 47 days" is one of the strongest churn signals, and it has no record to link to; showing the search itself is the honest trace.
- **Rationale**: Predictable and explainable in the walkthrough; probabilistic entity resolution is unjustified at this scale.
- **Alternatives considered**: Fuzzy name matching: produces silent wrong merges, which is worse than quarantine.

## R13. Testing strategy

- **Decision**: pytest against a real Postgres (docker compose service; `pytest` fixtures create a fresh schema per session and wrap each test in a transaction). Test layers: **unit** (signal functions, band assignment, validators), **property/invariant** (point-in-time: signals at cutoff C are unchanged by inserting events with `ingested_at > C`; dedupe is idempotent), **integration** (full nightly run on a small seeded dataset: idempotent re-run, partial-failure handling, alert dedupe), **security** (for every route, user of tenant A requesting tenant B identifiers gets 404 and an audit log row; queries without `app.tenant_id` return zero rows; a meta-test enumerates the app's route table and fails if any route lacks an access-test case, per SC-008), **model** (evaluation on generated data meets SC-001/002/004/011/012 thresholds, run as a slower marked suite).
- **Rationale**: Each non-negotiable has at least one automated test that would fail if it broke.

## R14. Deployment

- **Decision**: Docker image; Render Blueprint (`render.yaml`) with a web service, two cron jobs and a managed Postgres. Migrations via Alembic on deploy. Seeding via `worklist seed` as a one-off job. Secrets (session key, `ANTHROPIC_API_KEY`) as environment variables.
- **Rationale**: One file describes the whole live system, and cron jobs are first-class, matching R9.
- **Alternatives considered**: Fly.io (needs a separate scheduler machine), a single VM (manual ops), Kubernetes (overkill).

## R15. Library choices (pinned in `pyproject.toml`)

FastAPI, Uvicorn, Jinja2, HTMX (vendored static file), SQLAlchemy 2 (Core for bulk/feature queries, ORM for app entities), Alembic, psycopg 3, pandas, numpy, scikit-learn, LightGBM, argon2-cffi, itsdangerous, anthropic, pydantic 2, Typer (CLI), pytest, ruff, mypy (non-strict).
