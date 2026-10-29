# Quickstart: Renewal Risk Worklist

Validates the feature end to end on a local machine. Commands are the target interface; see [contracts/cli.md](contracts/cli.md).

## Prerequisites

- Docker + Docker Compose
- Python 3.12 and `uv` (or pip)
- Optional: `ANTHROPIC_API_KEY` for LLM drafts (template drafts are used without it)

## 1. Start and set up

```bash
cp .env.example .env              # set SESSION_SECRET, DEMO_PASSWORD, optional ANTHROPIC_API_KEY
docker compose up -d db
uv sync --extra dev
uv run worklist db upgrade
uv run worklist seed-demo
uv run worklist generate --seed 42          # ~5,000 accounts, ~200,000 events, 4 tenants
uv run worklist train --promote             # writes reports/eval_<date>.json
uv run worklist backfill --from -180d --to today --every 7d   # risk history for the UI
uv run worklist nightly                     # first unattended-style run
uv run uvicorn worklist.web.app:app --reload
```

Open http://localhost:8000 and sign in with any demo account listed on the login page.

## 2. Validation checklist (maps to spec)

| Check | How | Expected |
|---|---|---|
| Data is realistic (Story 7) | Open `reports/data_profile.json` | ~5,000 accounts / ~200,000 events across 5 sources; non-zero duplicates, quarantined, late (>1 day) and out-of-order events; ~10% sparse accounts; silent-churn cohort present |
| Worklist (Story 1) | Sign in as a CSM | Only own accounts; groups Act this week / Watch / Healthy / Insufficient data; each row has reasons, confidence, days to renewal, ARR; run timestamp shown |
| Act on it (Story 1) | Snooze an account, then run `worklist nightly` | Account moves to Snoozed; no alert for it unless a new trigger fires |
| Traceability (Story 2) | Click an account → click a reason | Run ID, model version, cutoff, signal value, and the source events behind it |
| Model works (Story 5) | Visit `/model` or open the eval report | Beats baseline; top-10 hit rate, lead time, calibration, silent-churn recall and planted-driver recovery reported per tenant |
| Unattended (Story 3) | `uv run worklist nightly` again for the next day; `/runs` | New run row with counts; alerts only on material changes; rerun with same `--cutoff` creates no duplicates |
| Drafts (Story 4) | Open an alert | Evidence linked to source records; draft cites facts; approving records who/when/text and offers Copy / mailto; nothing is sent |
| Isolation (Story 6) | Sign in as tenant A; paste a tenant B account URL | 404; row appears in access audit |

## 3. Tests

```bash
uv run pytest                 # unit, invariant, integration, security
uv run pytest -m model        # slower: evaluation thresholds SC-001/002/004/011/012
```

## 4. Simulating unattended operation

```bash
for i in $(seq 1 7); do uv run worklist nightly; done   # 7 simulated nights (SC-007)
curl -s localhost:8000/healthz/pipeline
```

In the deployed environment the same commands run from Render Cron Jobs (`render.yaml`): nightly at 02:00 UTC, retrain on Sundays at 03:00 UTC.
