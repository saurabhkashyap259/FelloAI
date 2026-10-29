# Renewal Risk Worklist

A ranked Monday worklist for Customer Success Managers. It shows which of their accounts are most likely not to renew in the next 90 days, why, how sure the system is, and where every number came from. An unattended nightly agent re-scores accounts and raises an in-app alert only when something materially changes. Each alert comes with the evidence the agent gathered and an outreach draft that a CSM approves and sends from their own mail client.

Built for the Fello take-home: synthetic data from 5 sources, a fitted model, an unattended process, multiple tenants, full traceability.

- Problem framing and trade-offs: [SOLUTION_BRIEF.md](SOLUTION_BRIEF.md)
- Spec, plan, research decisions, contracts, tasks: [specs/001-renewal-risk-worklist/](specs/001-renewal-risk-worklist/)
- Walkthrough outline: [docs/walkthrough.md](docs/walkthrough.md)

## What was decided

| Decision | Why | Where |
|---|---|---|
| Ranked per-CSM worklist instead of a RAG dashboard | A CSM can act on about 10 accounts a week. Bands are capacity-based: "high" means top 10 in the CSM's book **and** probability ≥ 15% | spec FR-011, `model/bands.py` |
| No automatic customer email | A wrong "you're at risk" email to a healthy customer is worse than no email. Drafts are approved by a human and sent from the CSM's own client | spec FR-024, `agent/drafts.py` |
| Churn = non-renewal (term: not renewed within 90 days of the renewal date; monthly: cancelled and not reactivated within 30 days). Downgrades are not churn | Clarification Q1 | `model/dataset.py` |
| One pooled LightGBM model with per-tenant isotonic/Platt calibration; tenant-specific models only with ≥1,000 examples and ≥50 churns, adopted only if they beat the pooled model | Small tenants can't train their own model; calibration keeps "30%" honest per tenant | research R6, `model/pipeline.py` |
| Point-in-time features: windows by when an event *happened*, cutoffs by when it was *received* and *matched* | Late and out-of-order data must never leak into the past | research R5, `signals/events.py`, invariant tests |
| Every displayed number comes from an immutable `account_score` row with run, model version and signal-definition version, and can be recomputed exactly | Traceability non-negotiable | `model/score.py::recompute_score`, `tests/invariants/test_reproducibility.py` |
| Postgres forced Row-Level Security on every tenant table; web and job roles are non-owners; a missing tenant setting means zero rows | Isolation that survives application bugs | `db/migrations/versions/0003_rls_grants.py`, `tests/security/` |
| Cross-tenant and cross-CSM requests get a byte-identical 404 plus an audit row; a meta-test fails if any route lacks an access rule | No probing for existence; every route covered | `web/deps.py::load_owned`, `tests/security/test_route_inventory.py` |
| The LLM (Claude Sonnet 5.5) only drafts text from a fact-ID evidence pack. A validator rejects unknown facts, invented numbers or dates, and words like "risk" or "churn" in the customer email; any failure falls back to a labelled template | The model ranks; the LLM writes. Hallucinations can't reach a customer | `agent/drafts.py`, contract `contracts/llm-draft.md` |
| Weekly gated retrain: the candidate must be no worse than the current model by more than 0.01 on hit rate, PR-AUC and Brier, and strictly better on hit rate or PR-AUC | Unattended retraining can't silently make things worse | spec FR-013, `model/registry.py` |
| A simulated clock and pending feed: the generator writes every event to `sim.pending_feed_event`, and each nightly run delivers what has "arrived" through the real ingest code | The unattended loop has something new to react to during a review, and late or out-of-order delivery is exercised for real | research R4, `ingest/feed.py` |
| The generator was frozen before evaluation and not retuned to pass the criteria | Otherwise the evaluation measures the generator, not the model | spec FR-029a, `synth/config.py` |

## Results (held-out last ~6 months, weekly worklist replay)

From `reports/eval_2026-10-26.json` (seed 42). 199 churned accounts fell in the test period, 39 of them silent churners. The pooled LightGBM (full history) was promoted over the logistic baseline by the gate.

| Criterion | Target | LightGBM | Baseline | Met? |
|---|---|---|---|---|
| SC-001 churners in the CSM's high band ≥60 days before renewal | ≥70% | 26% | 31% | **No** |
| Top-10 hit rate (churners that reached the high band at any point) | — | 83% | 82% | — |
| SC-002 churn rate in high band vs. all scored | ≥3× | 5.5× | 5.4× | Yes |
| SC-003 beats baseline on hit rate **and** median lead time | both | 83% vs 82%; 40.5 vs 42 days | — | **Partly** (hit rate only) |
| SC-004 largest calibration gap (bins ≥50) | ≤10 pts | 6.4 pts | 4.4 pts | Yes |
| SC-011 planted drivers among the top-10 signals | ≥5 of 6 | 4 of 6 (payment failures and champion departure missing) | — | **No** |
| SC-012 silent churners in the high band ≥30 days ahead | ≥70% | 74% | 77% | Yes |
| SC-007 nightly run time (~5,000 accounts) | <30 min | ~21 s per night, 7 of 7 nights succeeded | — | Yes |
| SC-009 alerts per CSM per week | ≤5 | 0.95 | — | Yes |

Per tenant (≥30 churns): Northwind 82% hit / 17% at 60 days / 6.0× lift; Bluefin 84% / 37% / 4.8×; Copperleaf 88% / 41% / 5.3×. Driftwood (8 churns) is too small to judge on its own. Every tenant-specific model lost to the pooled model on that tenant's held-out data and was rejected. Full history and the last 18 months tied on hit rate.

**Honest reading.**
- The worklist concentrates risk well: churn is 5.5× more likely in "Act this week" than across all scored accounts, and the percentages are calibrated.
- It does not give 60 days of warning for most churners. The generator plants drivers 55–160 days before renewal, so at 60–90 days out many haven't started yet.
- LightGBM is only marginally better than logistic regression on this data.
- Payment failures and champion departure each affect about 4% of renewals. They carry strong effects but are too rare to rank high by *mean* contribution.
- I froze the generator before evaluating (FR-029a), so these shortfalls are reported, not tuned away.
- The next steps would be a longer horizon (e.g. 180 days) with lead-time-weighted training, and denser usage data. Usage arrives as roughly monthly rollups because of the ~200k event budget.

Data (`reports/data_profile.json`): 5,000 accounts, 208,919 events (product 41%, billing 20%, support 14%, email 12%, CRM 12%). Defects: 2.5% duplicates, ~6% late by 1–10 days, out-of-order delivery, naive local timestamps, ~1% unmatched records, 5% CRM/billing conflicts, and 10% sparse accounts. Churn is 9.5% per term renewal; silent churn is 17% of churners.

| Planted driver | Prevalence | Log-odds effect | Churn with / without |
|---|---|---|---|
| Usage decline | 10.8% | +1.7 | 33% / 5% |
| Seat under-utilisation | 7.9% | +1.1 | 18% / 7% |
| Ticket spike, slow resolution | 9.7% | +1.4 | 28% / 6% |
| Payment failures | 4.3% | +1.5 | 26% / 7% |
| Champion departure | 3.8% | +1.6 | 27% / 7% |
| No CSM contact | 10.2% | +0.9 | 17% / 7% |

## What was deliberately skipped

- **Real connectors.** The generator stands in for Salesforce, Gmail, Zendesk, product analytics and Stripe, and delivers through the same ingest path a connector would use.
- **Sending email, CRM write-back, Slack or email alert delivery.** Alerts are in-app only.
- **SSO, self-service sign-up, mobile layouts.**
- **Automated data purging.** The retention policy is documented (raw records 3 years; scores, alerts, drafts and actions 2 years; access log 365 days), and v1 volumes stay within it.
- **Survival / time-to-event models.** A binary "churn at the next renewal within 90 days" target was simpler to calibrate and explain.
- **Owner conflicts between sources.** Only the CRM carries an owner, so FR-005's owner conflict can't occur with these sources.
- **Per-tenant tuning of K, floor and alert thresholds in the UI.** They are stored per tenant but edited only in the database.

## What breaks first at 10x (50k accounts, 2M events)

1. **Feature computation in one Python process.** Each tenant and cutoff loads its whole event history into pandas. A large tenant would need incremental, windowed aggregates in SQL (or a feature table updated as events arrive), not a full reload.
2. **Training builds every weekly snapshot from scratch.** That is about 90 s now and would be tens of minutes at 10x. Cache snapshots and train from a feature store.
3. **The LLM call runs inside the tenant's nightly transaction.** It is bounded (20 s timeout, at most 3 alerts per CSM per night), but at 10x the drafts belong in a separate queue with its own retries.
4. **Cron plus an advisory lock instead of an orchestrator.** It's fine for one job a night; with per-tenant schedules, backfills and retries it should move to a real orchestrator (Dagster, Temporal).
5. **The in-memory sign-in rate limiter** works only with a single web instance. It needs Redis or a database table.
6. **Model artefacts in Postgres `bytea`, and all tenants in one database.** Fine at a few MB. Larger models belong in object storage. Regulated tenants may need schema- or database-per-tenant.

## Run it locally

Requires Docker, Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env               # DEMO_MODE=true, optional ANTHROPIC_API_KEY for LLM drafts
docker compose up -d db            # Postgres 16 on localhost:55432
uv sync --extra dev
uv run worklist db upgrade         # roles, RLS policies, tables
uv run worklist seed-demo          # 4 tenants, 43 CSMs, 4 managers (demo accounts)
uv run worklist generate --seed 42 # ~5,000 accounts, ~209,000 events, profile in reports/
uv run worklist train --promote    # baseline + LightGBM, calibration, gate → reports/eval_<date>.json
uv run worklist backfill --from -180d --to today --every 7d   # risk history
uv run worklist nightly            # one unattended night (advances the simulated clock)
uv run uvicorn worklist.web.app:app --reload
```

Open http://localhost:8000 and pick a demo account on the sign-in page.

Tests: `uv run pytest` (167 tests: unit, invariant, integration, security) and `uv run pytest -m model` (slow: full-data model criteria and seven unattended nights).

## Deploy

`render.yaml` defines the web service (migrations run before deploy), two cron jobs (`worklist nightly` at 02:00 UTC, `worklist retrain` on Sundays at 03:00 UTC) and managed Postgres 16. After the first deploy, run `worklist seed-demo`, `generate`, `train --promote` and `backfill` once as one-off jobs.

## Layout

```text
src/worklist/
  synth/      generator: latent world, planted drivers, five emitters, defects, profile
  ingest/     normalise → dedupe → identity (source IDs, then domain) → facts by precedence
  signals/    30-signal registry, point-in-time computation, drill-down
  model/      dataset, baseline + LightGBM, calibration, bands, evaluation, gate, scoring
  agent/      nightly pipeline, alerts (triggers, caps, dedupe), evidence packs, drafts
  web/        FastAPI + Jinja2 + HTMX, sessions, RLS-scoped requests, routes
tests/        unit, invariants, integration, security (every route), model (slow)
```
