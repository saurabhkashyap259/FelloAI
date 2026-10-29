# CLI Contract (`worklist`)

The same commands run locally, in tests and as scheduled jobs. All commands log JSON lines to stdout and exit non-zero on failure. Exit codes: `0` success, `1` failure, `2` partial (some tenants failed), `3` skipped (lock held or run already succeeded).

| Command | Purpose | Key options |
|---|---|---|
| `worklist db upgrade` | Apply migrations (roles, RLS policies, tables). | |
| `worklist generate` | Generate synthetic world + observations to the database: historical backlog into `raw_event`, future feed into `sim.pending_feed_event`, outcomes into `renewal_outcome`, plus a data profile report. | `--seed 42` `--accounts 5000` `--events 200000` `--tenants 4` `--history-days 1095` `--feed-days 30` `--out reports/data_profile.json` |
| `worklist seed-demo` | Create tenants, CSM/manager demo users and tenant settings. Idempotent. | `--password-from-env DEMO_PASSWORD` |
| `worklist train` | Build point-in-time training snapshots, train baseline + LightGBM on full history and on the last 18 months, calibrate per tenant, evaluate, and register candidate(s). Promotion follows the gate (against the baseline when no model is approved yet). Required once during setup before the first nightly run. | `--as-of` (default: sim clock) `--promote/--no-promote` `--report reports/eval_{date}.json` |
| `worklist nightly` | Advance sim clock 1 day once (unless `--no-advance`), then per tenant: catch up missed cutoffs oldest-first (max 3, else flag `needs_attention`), ingest due feed events, retry quarantine, dedupe, resolve identities and facts, compute signals, score, band, diff, apply alert caps, alert, gather evidence, draft. Per-tenant transactions; advisory lock; idempotent per cutoff. | `--cutoff YYYY-MM-DD` `--tenant` `--no-advance` `--no-llm` |
| `worklist retrain` | Scheduled weekly: `train --promote` with gate, records decision. | |
| `worklist backfill` | Run scoring for a range of past cutoffs (used to build risk history for the demo and to replay silent-churn cases). Never sends alerts. | `--from` `--to` `--every 7d` |
| `worklist evaluate` | Re-run the evaluation for an existing model version. | `--model-version` |

## Schedules (production)

| Job | Cron (UTC) | Command |
|---|---|---|
| nightly | `0 2 * * *` | `worklist nightly` |
| retrain | `0 3 * * 0` | `worklist retrain` |

## Guarantees

- Running `worklist nightly --cutoff D` twice yields identical `account_score` rows and no new alerts on the second run.
- If a run for cutoff D is `failed`/`partial`, re-running resumes the failed tenants only.
- `generate` with the same `--seed` produces byte-identical data. Generator config lives in `src/worklist/synth/config.py` and is frozen before evaluation (spec FR-029a).
