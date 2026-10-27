# Solution Brief: Renewal Risk Worklist for CSMs

*Draft v0.1, written before any code.*

## 1. The actual problem

The request asks for a dashboard and automated emails. The underlying problem is different: **CSMs have finite attention and no reliable way to decide where to spend it.** Each CSM owns ~120 accounts and can realistically have meaningful conversations with 10–15 a week. The two lost renewals were not a visibility problem in general. They were a prioritisation problem: the signals existed in Zendesk and product usage, but nothing put those accounts at the top of someone's list early enough to act.

So the goal I'm building toward is:

> Every Monday, each CSM sees a short, ranked list of the accounts most likely to not renew, *why* the system thinks so, and what to do next, early enough before renewal that action can still change the outcome.

Success is measured as **fewer surprise churns**, meaning churned accounts that were never in a CSM's top list in the 90 days before renewal. A good-looking dashboard doesn't count as success on its own.

## 2. What I would build

**A Monday worklist, backed by a fitted risk model and an unattended nightly pipeline.**

1. **Ingestion and reconciliation.** Raw events from Salesforce, Gmail, Zendesk, product events and Stripe land in an append-only store with `tenant_id`, `source`, `event_time` and `ingested_at`. A reconciliation step resolves account identity across sources (domains, Stripe customer IDs, Salesforce IDs) and handles duplicates and late-arriving events.
2. **Point-in-time features.** Features are computed *as of* a date, using only events whose `ingested_at` was before that date. This prevents the model from learning from the future, which is the most common way churn models look great in testing and fail in production. Examples: usage trend over 30/90 days, active seats vs. purchased seats, ticket volume and severity, time to resolution, payment failures, days since last CSM contact, champion departure, days to renewal.
3. **Risk model.** The model is a gradient-boosted classifier predicting *probability of non-renewal within the next 90 days*, with calibrated probabilities. A logistic regression is kept as a baseline that the GBM has to beat. Per-account explanations (top contributing features) come from SHAP values. No LLM is involved in scoring.
4. **Data confidence.** Accounts with too little history get an explicit "insufficient data" state. They are not shown as green. An account with no data is an unknown and needs to be flagged as one.
5. **Nightly agent (no human trigger).** It runs on a schedule: ingest → reconcile → rescore → diff against last run → raise alerts only on *material changes* (e.g. moved into a CSM's top 10, a sharp usage drop, or a new P1 ticket on an account near renewal). For each alert, an LLM drafts a short internal brief and a suggested outreach email using **only the computed signals as input**. The CSM reviews, edits and sends. Nothing goes to a customer automatically.
6. **CSM worklist UI.** Ranked accounts per CSM, with the risk score, a band, top reasons, confidence, days to renewal and ARR. Each account opens a page showing every number traced back to the score run, model version, feature values and the source events behind them.

**Multi-tenancy:** Every table carries `tenant_id`, enforced at the database layer (Postgres row-level security). Any request without a tenant fails. Models are trained per tenant when data is sufficient, otherwise on a pooled model with per-tenant calibration. No tenant's raw data is ever visible to another.

**Traceability:** Every score is written to an immutable `score_run` record with the model version, feature snapshot, data cutoff time and explanation. The UI only reads from these records, so what a CSM sees is exactly what was computed. It is never recomputed on the fly.

## 3. What I would build first

In order, each step usable on its own:

1. Synthetic data generator with a known ground truth: churn drivers are planted deliberately so the model can be checked against them. Messiness is planted too: missing fields, duplicate contacts, out-of-order and late events, accounts with almost no history, timezone inconsistencies.
2. Point-in-time feature pipeline and a time-based backtest (train on older renewals, test on later ones).
3. Model plus evaluation: PR-AUC, calibration, and **precision@10 per CSM**, which is the metric that matches how the list is actually used. Plus lead time: how many days before renewal an at-risk account first appeared in the top 10.
4. Worklist UI with traceability.
5. Nightly agent with alerting and LLM-drafted outreach for CSM approval.

If time runs short, I'll cut from the bottom of this list. Polish comes last.

## 4. Where I disagree with the request

| Request | My position | Why |
|---|---|---|
| "If an account goes red, the system should email them." | Alert the **CSM** and draft the email. A human approves every send. | A false positive emails a happy customer saying "we're worried about you". A true positive sends a generic automated email at the moment the relationship most needs a person. Either way, the cost of a wrong send is high and hard to undo. |
| A red / yellow / green health score | A **ranked list with reasons**. Bands are kept for at-a-glance reading, but derived from CSM capacity, not fixed thresholds. | If 40 of 120 accounts are red, the colour doesn't tell a CSM where to start. Rank plus reason does. |
| "Health score" | A **probability of a defined outcome** (non-renewal in 90 days). | A health score with no defined outcome can't be validated. A probability can be backtested and calibrated. |
| "That way nothing slips." | The goal is fewer surprises, measured. | No model catches everything. Promising zero misses leads to over-alerting, and over-alerting leads to CSMs ignoring the alerts. |
| Use all 3 years of data | Use it, but carefully. | Older data reflects a different product and pricing. I'll weight or window it and test whether it helps rather than assuming it does. |

## 5. What I need from the business

- **Definition of churn.** Full non-renewal only, or also downgrades and seat reductions?
- **The two lost renewals.** Their account IDs and timelines, to check whether the system would have caught them and how early.
- **Source of truth** for renewal date, ARR and account ownership. Salesforce and Stripe usually disagree.
- **CSM capacity.** How many accounts can a CSM realistically act on per week? This sets the list length and the bands.
- **Playbooks.** What actions actually work (exec sponsor call, training session, discount)? This turns "at risk" into "do this".
- **Outreach policy.** Who is allowed to email customers, under what consent, and what tone and branding apply.
- **Tenant boundaries.** Can models be trained on pooled data across customer organisations, or must they stay strictly isolated?
- **Freshness.** Is nightly scoring enough, or do some signals (e.g. a P1 outage ticket) need same-day alerts?

## 6. Proposed stack (tentative)

Python, Postgres (with row-level security), scikit-learn / LightGBM, SHAP, FastAPI, a lightweight web UI, a scheduled job for the nightly agent, and an LLM (Claude) used only for drafting text from computed signals. Deployed as a single live environment with seeded data for multiple tenants.

## 7. Deliberately out of scope

Real connectors to the five sources (synthetic data stands in), automated sending to customers, SSO, and real-time streaming. The README will cover these, and what breaks first at 10x.
