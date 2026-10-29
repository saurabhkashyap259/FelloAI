# Walkthrough outline (slides + Loom)

About 10 minutes. Each section names what to show on screen.

## 1. The ask, reframed (1 min)

- The VP asked for a red/yellow/green dashboard plus automatic emails to customers that turn red.
- The real problem: two renewals were lost even though the warning signs existed, because nobody was watching them across five systems. A CSM with about 120 accounts cannot read five systems every week.
- What I built instead: a ranked Monday worklist per CSM, an unattended nightly agent that only speaks up when something changes, and outreach drafts that a human approves.

## 2. Where I disagreed with the VP (1 min)

| VP asked for | Built instead | Why |
|---|---|---|
| Health-score dashboard (RAG) | Per-CSM ranked worklist with reasons | A colour doesn't say who to call first; capacity-based bands do |
| Auto-email red customers | Drafts a CSM approves; the system sends nothing | A false positive emails a healthy customer about "risk" |
| "Nothing slips" | Measured hit rate and lead time, with stated misses | A promise you can't measure becomes blame later |

## 3. Demo: Monday morning (2 min)

1. Sign in as `csm01@northwind.demo` from the demo list.
2. Worklist groups: Renewal overdue, Act this week, Watch, and so on. Point out the run ID, cutoff and model shown at the top.
3. Click a reason → the signal drill-down shows its definition, window and source records. For an absence signal, show "no CSM contact in N days".
4. Open the account: facts with a CRM/billing conflict, the full contribution list, the risk history chart.
5. Snooze an account and watch it move to Snoozed.

## 4. Demo: the unattended agent (2 min)

1. `/runs` as a manager: one row per night, with ingested, duplicates, quarantined (aged) and alerts raised/suppressed.
2. `/alerts`: open one. Show the trigger, the evidence the agent gathered (each item linked to its records), and the draft. A Template label appears when the LLM was off or failed validation.
3. Approve → copy or open in the mail client. Nothing is sent by the system.
4. Terminal: `worklist nightly` twice for the same cutoff; the second run is a no-op (exit 3).

## 5. How I know it works (2 min)

- `/model` and `reports/eval_*.json`: evaluated on a later period than training, by replaying weekly worklists.
- Show the success criteria that pass and the ones that **don't**. SC-001 (60 days of lead) and SC-011 (driver recovery) miss. Explain why: the generator plants drivers 55–160 days before renewal, and two drivers are rare. I froze the generator before evaluating and did not retune it (FR-029a).
- Calibration chart: "30% means 30%".
- Silent-churn cohort (the two lost renewals): recall at 30 days.

## 6. Isolation and traceability (1 min)

- Postgres forced RLS on every tenant table. The web and job roles cannot see rows without a tenant set.
- Paste another tenant's account URL → the same 404 as a made-up ID, plus an audit row.
- The test suite enumerates every route; adding a route without an access rule fails CI.

## 7. What breaks first at 10x, and what I skipped (1 min)

Taken from the README: pandas feature computation in one process, the in-memory rate limiter, cron without an orchestrator, model artefacts in Postgres, and a single database for all tenants.
