# Implementation Plan: Renewal Risk Worklist for CSMs

**Branch**: `001-renewal-risk-worklist` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/001-renewal-risk-worklist/spec.md`

## Summary

Give each CSM a ranked Monday worklist of accounts most likely not to renew in the next 90 days, with reasons, confidence and a full trace back to source data. It is backed by:

- a seeded synthetic data generator (5 sources, ~5,000 accounts, ~200,000 messy events, 4 tenants, planted churn drivers including a "silent churn" cohort);
- point-in-time signal computation (`ingested_at` cutoffs) and a LightGBM model trained on pooled data with per-tenant calibration, gated against a logistic-regression baseline;
- an unattended nightly pipeline (scheduled CLI job) that ingests the day's events, rescores, raises deduplicated alerts on material changes, gathers evidence and drafts outreach with Claude under a strict grounding contract (template fallback). A weekly gated retrain runs the same way;
- a server-rendered FastAPI web app with real sign-in, Postgres Row-Level Security for tenant isolation, and immutable score records as the only source for anything displayed.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: FastAPI, Jinja2 + HTMX, SQLAlchemy 2 + Alembic, psycopg 3, pandas, numpy, scikit-learn, LightGBM, anthropic SDK, pydantic 2, Typer, argon2-cffi (see [research.md](research.md) R15)
**Storage**: PostgreSQL 16 with forced Row-Level Security; model artifacts stored in Postgres (`bytea`)
**Testing**: pytest against real Postgres (docker compose), with marked slow `model` suite; ruff, mypy
**Target Platform**: Linux container; deployed on Render (web service + 2 cron jobs + managed Postgres)
**Project Type**: Web application (server-rendered) + batch jobs in one Python package
**Performance Goals**: Nightly run for ~5,000 accounts < 30 min (SC-007; target < 5 min); worklist page < 1 s server time; retrain < 10 min
**Constraints**: No cross-tenant data access (enforced in DB); no outbound email; LLM never scores; every displayed score immutable and traceable; deterministic generation by seed; idempotent jobs
**Scale/Scope**: 4 tenants, ~42 CSMs, ~5,000 accounts, ~200,000 events, ~30 signals, ~12 pages

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unratified template, so no project principles are binding. The exercise's non-negotiables and the spec's confirmed decisions are used as gates instead:

| Gate | Source | Pre-research | Post-design | Where satisfied |
|---|---|---|---|---|
| G1 Synthetic data: ~5k accounts, ~200k events, 5 sources, messy, out of order | Exercise / FR-028–030 | Pass | Pass | R3, R4, [contracts/source-events.md](contracts/source-events.md) |
| G2 Ranking from a fitted model, not LLM judgment | Exercise / FR-008 | Pass | Pass | R6; LLM confined to drafting ([contracts/llm-draft.md](contracts/llm-draft.md)) |
| G3 Runs without a human trigger | Exercise / FR-014, FR-014a | Pass | Pass | R9 cron jobs, idempotent runs |
| G4 Multiple customer organisations | Exercise / FR-025–027a | Pass | Pass | R2 forced RLS, R11 sessions, 404 + audit contract |
| G5 Everything traceable to computation | Exercise / FR-020–021 | Pass | Pass | Immutable `account_score`, `signal_snapshot`, `event_account.matched_at`, drill-down route |
| G6 No automatic customer contact | Clarification Q5 / FR-024 | Pass | Pass | No mail integration; approve = record + copy/mailto |
| G7 Simplicity: no component without a spec requirement | Engineering default | Pass | Pass | Single package, single DB, no queue/orchestrator; see Complexity Tracking |

No violations.

## Project Structure

### Documentation (this feature)

```text
specs/001-renewal-risk-worklist/
├── plan.md              # This file
├── research.md          # Phase 0: decisions R1–R15
├── data-model.md        # Phase 1: tables, RLS, state transitions
├── quickstart.md        # Phase 1: local run + validation checklist
├── contracts/
│   ├── http.md          # Pages and JSON routes
│   ├── cli.md           # Jobs and commands
│   ├── source-events.md # Raw event envelopes per source
│   └── llm-draft.md     # Evidence pack → draft contract
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit.tasks, not created here)
```

### Source Code (repository root)

```text
src/worklist/
├── config.py                 # settings from env
├── db/
│   ├── engine.py             # engine, tenant-scoped session (SET LOCAL app.tenant_id)
│   ├── tables.py             # SQLAlchemy table/ORM definitions
│   └── migrations/           # Alembic: roles, RLS policies, tables
├── synth/                    # Story 7
│   ├── config.py             # frozen generator parameters (FR-029a)
│   ├── world.py              # latent accounts, health trajectories, outcomes
│   ├── drivers.py            # planted churn drivers incl. silent churn
│   ├── emit.py               # five source emitters + defect injection
│   └── profile.py            # data profile report
├── ingest/
│   ├── feed.py               # sim clock + pending feed delivery (connector shim)
│   ├── normalize.py          # tz, envelope validation, dedupe key
│   ├── identity.py           # account matching, quarantine
│   └── facts.py              # ARR/renewal/owner precedence + conflicts
├── signals/
│   ├── registry.py           # signal definitions (name, sources, window, text template)
│   └── compute.py            # point-in-time computation + drill-down queries
├── model/
│   ├── dataset.py            # snapshot sampling + labels
│   ├── train.py              # baseline + LightGBM + per-tenant calibration
│   ├── evaluate.py           # metrics, top-10 replay, lead time, driver recovery
│   ├── registry.py           # model_version lifecycle + promotion gate
│   └── score.py              # scoring, contributions, reasons, bands, confidence
├── agent/
│   ├── pipeline.py           # nightly orchestration, locking, per-tenant runs
│   ├── alerts.py             # triggers, dedupe keys, snooze/dismiss handling
│   ├── evidence.py           # evidence pack builder
│   └── drafts.py             # Claude call, validator, template fallback
├── web/
│   ├── app.py                # FastAPI app, middleware (session, CSRF, audit)
│   ├── auth.py
│   ├── routes/               # worklist, accounts, alerts, drafts, model, runs, health
│   ├── templates/
│   └── static/
└── cli.py                    # Typer entrypoint `worklist`

tests/
├── unit/                     # signals, bands, validators, identity, dedupe
├── invariants/               # point-in-time, idempotency
├── integration/              # nightly run end to end, partial failure, alerts
├── security/                 # cross-tenant + role access on every route, no-tenant queries
└── model/                    # evaluation thresholds (marked slow)

reports/                      # generated data profile + eval reports (gitignored)
docker-compose.yml  Dockerfile  render.yaml  pyproject.toml  README.md
```

**Structure Decision**: A single Python package (`src/worklist`) containing the web app, the jobs and the ML code, because signal definitions are shared by training, scoring and the drill-down view. A separate frontend or service split would add a second deploy and contract with no benefit at this scale.

## Phasing (build order, each step usable on its own)

1. **Foundation**: DB roles + RLS migrations, tenant-scoped session, auth, seed-demo, security tests (Story 6). *Isolation first, because everything else writes tenant data.*
2. **Data**: synthetic generator + profile report + ingest/dedupe/identity/facts (Story 7).
3. **Model**: signals registry, point-in-time snapshots, baseline + LightGBM, calibration, evaluation, promotion gate (Story 5).
4. **Worklist + traceability**: scoring/bands/confidence, worklist and account pages, drill-down, CSM actions (Stories 1, 2).
5. **Unattended agent**: nightly pipeline, alerts, evidence packs, weekly retrain, run pages, health endpoint (Story 3).
6. **Drafts**: Claude drafting with validator + template fallback, approve/copy flow (Story 4).
7. **Ship**: Dockerfile, render.yaml, deploy, README (decisions, skipped items, what breaks at 10x), slides, Loom.

If time runs short, cut from step 6 backwards (template drafts only), never from steps 1–4.

## Key Risks

| Risk | Mitigation |
|---|---|
| Synthetic data too easy (model looks perfect) or too hard | Tune driver strength and noise so baseline PR-AUC is mediocre and the GBM is clearly but not absurdly better; publish the planted-driver table in the README |
| Leakage via late events or outcome timestamps | `ingested_at`/`matched_at`/`recorded_at` cutoffs everywhere + invariant tests |
| RLS bypass by superuser or table owner | App and job roles are non-owners; `FORCE ROW LEVEL SECURITY`; security test asserts zero rows without tenant setting |
| Alert fatigue | Dedupe keys, snooze/dismiss suppression, SC-009 measured in the 7-night simulation |
| LLM hallucination in drafts | Fact-ID + number/date validator; template fallback; LLM optional |
| Live demo goes stale | Pending feed covers 30+ simulated days; nightly cron advances it |

## Complexity Tracking

No constitution violations. Deliberate additions beyond the minimum, each tied to a requirement:

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Simulated clock + pending feed | Story 3 needs new data to arrive nightly for alerts to exist | Rescoring static data would never alert, so the unattended loop couldn't be shown |
| Per-tenant calibration | Clarification Q2; SC-004 honesty per tenant | A single global calibration is miscalibrated for tenants unlike the average |
| Separate `event_account` link table | Keeps `raw_event` append-only while allowing late matches | Updating `raw_event.account_id` breaks reproducibility of past runs |
