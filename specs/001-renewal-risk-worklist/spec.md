# Feature Specification: Renewal Risk Worklist for CSMs

**Feature Branch**: `001-renewal-risk-worklist`
**Created**: 2026-10-27
**Status**: Draft (pre-implementation checklist resolved)
**Input**: User description: "Create specs for SOLUTION_BRIEF.md" (source: [SOLUTION_BRIEF.md](../../SOLUTION_BRIEF.md), responding to the VP Customer Success request in the Fello take-home exercise)

## Clarifications

### Session 2026-10-27

- Q: What counts as churn (the model's prediction target)? → A: Non-renewal only — the account did not renew within 90 days of its renewal date; downgrades are recorded but not labelled as churn.
- Q: How are models trained across tenants? → A: One shared model trained on pooled signals from all tenants, with per-tenant calibration; a tenant-specific model is used only where the tenant has enough history and it beats the shared model on held-out data.
- Q: How do reviewers sign in to the live system? → A: Real sign-in with server-enforced sessions; pre-made demo accounts (CSM and CS manager per tenant) listed on the sign-in page for one-click access.
- Q: How often is the model retrained? → A: Weekly, by an unattended job, gated: the new model is promoted only if it is at least as good as the current one on held-out data. Nightly runs score with the current approved model.
- Q: What happens when a CSM approves an outreach draft? → A: The approval is recorded (who, when, final text) and the CSM copies the text or opens it in their own mail client; the system itself never sends email.

## Definitions

Terms used with a precise meaning throughout this spec. Defaults marked *(configurable)* are tenant settings.

- **Renewal horizon**: the 90 days before an account's next renewal date. Only accounts inside the horizon receive a risk estimate.
- **Churn**: for term contracts, the account did not renew within 90 days after its renewal date (grace period). For monthly plans, the subscription was cancelled and not reactivated within 30 days; a monthly plan's "renewal date" is its current period end, so monthly accounts are always inside the renewal horizon. Downgrades are recorded but are not churn.
- **Insufficient data**: fewer than 5 matched source records in the last 180 days, **or** account younger than 60 days with no product usage records.
- **Low confidence**: any source the account normally has data from is stale, **or** more than 30% of the account's signals are missing.
- **Stale source**: for a tenant, a source with no records received for longer than the larger of 2 days and 3× its median delivery gap over the previous 30 days.
- **Material change** (alert triggers):
  - **Entered top K**: the account is now in its owner's high band and was not in it at the previous successful run.
  - **Risk jump**: calibrated probability is at least 0.15 *(configurable)* higher, in absolute terms, than the account's score 7 days earlier.
  - **Severe event near renewal**: renewal within 60 days *(configurable)* **and** one of: an urgent-priority ticket; 2+ high-priority tickets opened within 7 days; a payment failure on the final retry; subscription set to cancel at period end; the champion contact removed with no replacement.
- **New material change** (overrides snooze/dismiss): a trigger of a different type than the one active when the action was recorded, or a further risk jump measured from the probability at the time of the action.
- **Silent churn**: a churned account that, in the 120 days before renewal, had no CRM warning (no opportunity downgrade, no champion removal), no email warning (no bounces, no escalation thread) and no payment failure, **and** had at least two of: usage decline ≥30% over 60 days, 2+ high/urgent tickets, a "bad" ticket satisfaction rating. Labelled at generation time as ground truth.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Monday prioritised worklist (Priority: P1)

A Customer Success Manager (CSM) who owns ~120 accounts opens the system on Monday morning and sees their own accounts ranked by likelihood of not renewing. Each row shows the risk estimate, a simple risk band, the top reasons behind it, how confident the system is, days until renewal, and the account's contract value. The CSM works from the top of the list instead of guessing from a spreadsheet.

**Why this priority**: This is the core value. It replaces "guess who to call" with a defensible ordering. Without it, nothing else matters.

**Independent Test**: Load a tenant with seeded accounts and history, run scoring once, sign in as a CSM, and verify that the list contains only that CSM's accounts, is ordered by risk, and shows reasons and confidence for each row.

**Acceptance Scenarios**:

1. **Given** a CSM owns 120 accounts, **When** they open their worklist, **Then** they see only their own accounts; accounts inside the renewal horizon are ordered from highest to lowest risk, each with a risk estimate, band, top 3 reasons, confidence level, days to renewal and contract value.
2. **Given** an account has insufficient data, **When** it appears in the worklist, **Then** it is shown in an "Insufficient data" group, never as low risk.
3. **Given** a CSM can realistically act on ~10–15 accounts per week, **When** bands are assigned, **Then** the high band contains at most K accounts (default 10) and no account with risk below the high-band floor (default 15%), so a CSM with a healthy book is not told to act on low-risk accounts.
4. **Given** the worklist was produced by last night's run, **When** the CSM views it, **Then** the page states when the scores were computed and the data cutoff they reflect.
5. **Given** a CSM has dealt with an account, **When** they record an action (contacted, meeting booked, snoozed until a date, dismissed with a reason), **Then** the action is saved with who and when, shown on the account, and the account's position reflects it (snoozed accounts move to a "Snoozed" group until the snooze ends).
6. **Given** an account's next renewal is more than 90 days away, **When** it appears in the worklist, **Then** it is shown in a "Later renewals" group with its signals and days to renewal, but without a risk estimate.
7. **Given** an account's renewal date has passed with no renewal recorded, **When** it appears in the worklist, **Then** it is shown at the top in a "Renewal overdue" group for its owner, without a model score.

---

### User Story 2 - Trace any number back to its source (Priority: P1)

From any account in the worklist, the CSM (or their manager) opens a detail view that explains exactly where the score came from: which scoring run produced it, which model version, the input signal values used, how much each signal pushed the risk up or down, and the underlying source events (tickets, usage changes, payment events, emails, CRM changes) behind those signals.

**Why this priority**: The exercise makes traceability non-negotiable, and CSMs won't act on a score they can't explain to a customer or their manager.

**Independent Test**: Pick any account in the worklist, open its detail view, and confirm every displayed number matches the stored record of the scoring run and links to real source events from the seeded data.

**Acceptance Scenarios**:

1. **Given** an account shows a risk estimate of X, **When** the CSM opens its detail view, **Then** they see the scoring run ID, timestamp, model version, signal-definition version, data cutoff, and the exact signal values used to produce X.
2. **Given** a reason such as "support tickets up sharply in last 30 days" is shown, **When** the CSM drills into it, **Then** they see the signal's definition and window and the specific source records that contributed to it.
3. **Given** a reason based on the absence of activity (e.g. "no CSM contact in 47 days"), **When** the CSM drills into it, **Then** they see the window searched, a statement that no qualifying records were found in it, and the most recent qualifying record before the window (or "none since account start").
4. **Given** a newer scoring run exists, **When** the CSM views an older run's result, **Then** the older result is shown unchanged, as originally computed.
5. **Given** CRM and billing disagree on a key fact, **When** the account is viewed, **Then** the chosen value, its source, and the conflicting value are shown.

---

### User Story 3 - Unattended nightly agent with change alerts (Priority: P2)

Every night, without anyone triggering it, the system ingests new data from all five sources, reconciles it, rescores every account, compares results with the previous run, and raises an alert to the owning CSM only on a material change (see Definitions). This is the agentic part of the system: for each material change it gathers the relevant evidence across sources (recent tickets, usage trend, billing events, last contact) into the alert, so the CSM gets an investigated case rather than a bare notification. The agent may observe, summarise and draft; it may not contact customers or change account data. Alerts are delivered in-app: an alerts inbox with an unread count, and a "new" marker on affected worklist rows.

**Why this priority**: Satisfies the "runs without a human" requirement and addresses the root cause of the lost renewals: signals that existed but nobody was watching. It depends on Stories 1–2 being in place.

**Independent Test**: Inject new events that should push a specific account into a CSM's high band, let the scheduled run execute without manual action, and confirm exactly one alert appears in the right CSM's inbox with the reason attached.

**Acceptance Scenarios**:

1. **Given** the schedule time arrives, **When** no human has triggered anything, **Then** a full run executes and its outcome (succeeded, partial, failed) is recorded with counts of events ingested and accounts scored.
2. **Given** an account enters its owner's high band since the last successful run, **When** the run completes, **Then** one alert appears in that CSM's alerts inbox naming the account, the change, and the main reasons.
3. **Given** an account's risk changed but no trigger fired, **When** the run completes, **Then** no alert is raised.
4. **Given** a run fails partway, **When** CSMs open their worklist, **Then** they see the last successful results, clearly labelled with their date, and a notice that the latest run did not complete.
5. **Given** the same run is triggered twice for the same cutoff, **When** both complete, **Then** no duplicate alerts or drafts are created and results are identical.
6. **Given** an alert is raised, **When** the CSM opens it, **Then** it includes the evidence the agent gathered, each item linked to its source records.
7. **Given** a CSM has snoozed or dismissed an account, **When** later runs find no new material change, **Then** no alert is raised for it; **When** a new material change occurs, **Then** an alert is raised despite the snooze.
8. **Given** more triggers fire for a CSM in one night than the alert cap allows, **When** the run completes, **Then** only the highest-priority alerts are raised, the rest are counted as suppressed in the run report, and the affected accounts remain visible in the worklist.
9. **Given** an account's tenant failed in last night's run, **When** the next nightly run starts, **Then** it first catches up the missed cutoff(s) for that tenant in order, then processes the new cutoff.

---

### User Story 4 - Drafted outreach, human-approved (Priority: P3)

For each alert, the system prepares a short internal summary for the CSM and a suggested outreach email to the customer, written only from the signals the system computed. The CSM reviews, edits, and decides whether to send. Nothing is ever sent to a customer by the system.

**Why this priority**: It saves CSM time and partially honours the VP's request for automated outreach, without the risk of emailing healthy customers by mistake. It is the most polish-dependent piece, so it comes last.

**Independent Test**: Trigger an alert for a seeded account and confirm a draft is produced that references only facts present in that account's alert evidence, and that no message leaves the system.

**Acceptance Scenarios**:

1. **Given** an alert exists, **When** the CSM opens it, **Then** they see a drafted internal summary and a drafted customer email, each linked to the evidence it was based on.
2. **Given** a draft exists, **When** the CSM has not approved it, **Then** it is never marked approved and nothing is offered for sending.
3. **Given** a draft mentions a fact, **When** that fact is checked against the alert's evidence, **Then** it matches; drafts must not contain claims the system did not compute.
4. **Given** the CSM edits and approves a draft, **When** they approve it, **Then** the approval is recorded with who, when and the final text, and the CSM can copy the text or open it in their own mail client. The system itself sends nothing.
5. **Given** the drafting service is unavailable, disabled for the tenant, or produces a draft that fails validation, **When** an alert is created, **Then** a template draft built only from the evidence is stored instead, labelled as such.

---

### User Story 5 - Prove the model works (Priority: P2)

A reviewer (or a CS leader) can see how well the risk model performed on past data it was not trained on: how many accounts that actually churned had appeared in their CSM's high band, how early they appeared before renewal, and whether the risk estimates are honest (e.g. accounts given ~30% risk churned roughly 30% of the time). The model must beat a simple baseline to be used.

**Why this priority**: "How do you know it works" is central to the evaluation, and the VP's promise that "nothing slips" needs to be replaced with a measured claim.

**Independent Test**: Run the evaluation on a held-out later time period of the synthetic data and confirm the report shows the metrics below, comparison against a baseline, and recovery of the churn drivers deliberately planted in the synthetic data.

**Acceptance Scenarios**:

1. **Given** a trained model, **When** evaluation runs, **Then** it trains only on earlier periods and tests on a later period, never mixing them.
2. **Given** evaluation results, **When** a reviewer reads the report, **Then** it shows top-10 hit rate per CSM, lead time before renewal, calibration, and comparison with a simple baseline, per tenant and overall.
3. **Given** a new model does not pass the promotion gate (FR-013), **When** a run tries to adopt it, **Then** the current model stays in use and the reason is recorded.
4. **Given** the synthetic data contains silent-churn accounts like the two lost renewals, **When** the system is replayed over the months before their renewal, **Then** the report shows whether and how many days in advance each one entered its CSM's high band.
5. **Given** the evaluation is run, **When** a reviewer reads the report, **Then** it shows whether training on full history or only the most recent 18 months performed better, and which was used.

---

### User Story 6 - Separate customer organisations (Priority: P1)

Several customer organisations (tenants) use the same system. Each tenant's users see only their own tenant's accounts, scores, alerts and drafts. No screen, export, alert, draft, report or health response ever contains another tenant's data.

**Why this priority**: Non-negotiable in the exercise, and a data leak between customers is the most damaging failure possible.

**Independent Test**: Seed at least three tenants, sign in as a user of each, and confirm no data from other tenants is reachable through any route, link, or by changing identifiers in a request.

**Acceptance Scenarios**:

1. **Given** users from tenant A and tenant B, **When** each opens the worklist, **Then** each sees only their own tenant's accounts.
2. **Given** a user of tenant A, **When** they request a resource belonging to tenant B by its identifier, **Then** they receive the same "not found" response as for an identifier that does not exist, on both pages and data endpoints, and the attempt is logged.
3. **Given** a request carries no signed-in session, **When** it reaches any protected route, **Then** it is rejected (redirect to sign-in for pages; unauthorised for data endpoints).
4. **Given** a CSM, **When** they request an account owned by another CSM in the same tenant, **Then** they receive "not found" and the attempt is logged.
5. **Given** a CS manager, **When** they view the tenant, **Then** they see all accounts, an "Unassigned" queue, and alerts for unassigned accounts.

---

### User Story 7 - Realistic synthetic data (Priority: P1, enabling)

An engineer (or reviewer) can generate a realistic dataset of roughly 5,000 accounts and 200,000 events across five sources (CRM, email, support, product usage, billing), spread across several tenants, with known churn outcomes and the messiness of real data.

**Why this priority**: Required by the exercise, and every other story depends on it. Its realism is graded.

**Independent Test**: Run the generator with a fixed seed twice and confirm identical output; inspect the data profile report showing volumes per source, the share of accounts with sparse data, rates of duplicates, missing fields, late and out-of-order events, and the planted-driver table.

**Acceptance Scenarios**:

1. **Given** the generator runs with a fixed seed, **When** it completes, **Then** it produces ~5,000 accounts and ~200,000 events across all five sources and multiple tenants, reproducibly.
2. **Given** the generated data, **When** profiled, **Then** it contains duplicates, missing fields, conflicting values between sources, events arriving after later events, timestamps without timezone, unmatched records, and a meaningful share of accounts with very little history.
3. **Given** the generated data, **When** outcomes are inspected, **Then** each account has a known renewal outcome produced by documented, deliberately planted drivers plus noise.
4. **Given** the generated data, **When** inspected, **Then** it includes a labelled set of silent-churn accounts (see Definitions), mirroring the two lost renewals described by the VP.

---

### Edge Cases

- **Sparse accounts**: accounts with insufficient data get an "Insufficient data" state, not a low-risk score.
- **New accounts**: accounts with no renewal history but enough data are scored with low confidence and labelled as such.
- **Accounts outside the renewal horizon**: shown in "Later renewals" without a risk estimate; severe events still appear on the account page.
- **Late-arriving events**: an event that arrives days after it happened is counted from when it arrives; past scoring runs are never rewritten.
- **Out-of-order events**: signal windows use when the event happened; data cutoffs use when it was received.
- **Timezones**: all event times are normalised to UTC; timestamps without a timezone are interpreted in the source's configured timezone, and the original value is kept.
- **Duplicate records**: the same event from the same source counted twice must not inflate signals.
- **Conflicting sources**: CRM and billing disagree on renewal date (more than 7 days) or contract value (more than 5%); the precedence rule applies and the conflict is shown on the account.
- **Unmatched records**: quarantined and counted, re-attempted on every nightly run, and reported as aged after 30 days unmatched.
- **Account with no owner**: shown in the tenant's "Unassigned" queue to CS managers; its alerts go to all CS managers of the tenant.
- **Owner change**: the account and its open alerts appear for the new owner from the next run; each alert keeps a record of who it was originally raised for.
- **Renewal overdue**: renewal date passed with no renewal recorded → "Renewal overdue" group for the owner, not model-scored. If no outcome is recorded 90 days after the renewal date, the account is marked churned per the churn definition; if billing and CRM contradict each other on whether it renewed, it goes to the CS manager's "Outcome unknown" queue for review.
- **Source outage**: a stale source does not fail the run; its signals are marked stale, affected accounts get low confidence, and missing data is never treated as "no activity".
- **Alert fatigue**: an account that stays in the high band does not re-alert unless a new material change occurs; per-CSM alert caps apply (FR-017a).
- **No approved model** (e.g. first deploy before training, or a tenant's model invalidated): accounts are shown with state "No model yet"; the nightly run still ingests and computes signals but raises no model-based alerts.
- **Failed or rejected retrain**: the current model stays in use; the model page shows the last retrain outcome and reason.

## Requirements *(mandatory)*

### Functional Requirements

**Data ingestion and quality**

- **FR-001**: System MUST accept records from five sources (CRM, email, support tickets, product usage events, billing) for multiple tenants, recording for each record its tenant, source, when it happened, and when it was received.
- **FR-001a**: System MUST normalise all event times to UTC, interpreting timestamps without a timezone in the source's configured timezone, and keep the original value with the record.
- **FR-002**: System MUST keep received records unchanged (append-only). Every score MUST reference the model version and signal-definition version used, and both MUST be retained as long as any score references them, so any past score can be recomputed from stored data and match.
- **FR-003**: System MUST link records to accounts using, in order: (1) exact source identifiers (CRM, billing, product, support IDs), (2) normalised email domain, never using free-mail domains. Records matching no account, or more than one, MUST be quarantined with a reason.
- **FR-003a**: Quarantined records MUST be re-attempted on every nightly run. A later match MUST only affect runs whose cutoff is after the match time; past results are unchanged. Records unmatched for over 30 days MUST be reported as aged.
- **FR-004**: System MUST detect and remove duplicate records (same source record ID, or identical content for sources without IDs) before computing signals.
- **FR-005**: System MUST resolve account facts by precedence (billing for contract value and renewal date; CRM for owner and segment; configurable) and record a visible conflict when sources disagree by more than 7 days on renewal date or more than 5% on contract value, or on owner.
- **FR-006**: System MUST report data quality per run: records received per source, duplicates removed, records quarantined and aged, stale sources.

**Signals and scoring**

- **FR-007**: System MUST compute account signals "as of" a data cutoff using only records received, and account links made, before that cutoff.
- **FR-008**: For each account inside the renewal horizon, the system MUST estimate the probability of churn at its next renewal using a model fitted on historical data. Accounts outside the horizon MUST NOT receive a risk estimate. An LLM MUST NOT be used to score or rank accounts.
- **FR-009**: System MUST give each scored account its top 3 contributing reasons, in plain language and linked to the signals behind them.
- **FR-010**: System MUST assign each account a state and confidence per the Definitions: `insufficient data`, `low confidence` or normal. Insufficient-data accounts MUST NOT be scored as healthy.
- **FR-011**: System MUST assign risk bands per CSM: **high** = among the CSM's top K by probability (K default 10) **and** probability ≥ the high-band floor (default 15%); **medium** = not high, probability ≥ 5%, and either within the CSM's top 3K or probability ≥ the floor; **low** = all other scored accounts. K and floor are configurable per tenant.

**Evaluation**

- **FR-012**: System MUST evaluate each model on a later time period than it was trained on, and report per tenant and overall: top-10 hit rate per CSM, lead time before renewal, calibration (10 equal-frequency bins), PR-AUC, Brier score, silent-churn recall and lead time, planted-driver recovery, and comparison against a logistic-regression baseline.
- **FR-012a**: System MUST train one shared model on pooled signals from all tenants and calibrate its risk estimates separately per tenant. A tenant-specific model MUST only be trained when that tenant has at least 1,000 labelled examples and 50 churn events, and only used if it passes the promotion gate against the shared model on that tenant's held-out data.
- **FR-012b**: Evaluation MUST compare training on full history against training on the most recent 18 months only, and use whichever passes the promotion gate with the better top-10 hit rate. The comparison MUST be reported.
- **FR-013**: Promotion gate: a candidate model is promoted only if, on the same latest held-out period, it is no worse than the current model by more than 0.01 on top-10 hit rate and on PR-AUC, no worse by more than 0.01 on Brier score, **and** strictly better on top-10 hit rate or PR-AUC. When there is no current model, the candidate must beat the logistic-regression baseline under the same rule. The decision and its reason MUST be recorded.

**Unattended operation**

- **FR-014**: System MUST run the full pipeline (ingest, reconcile, compute signals, score, compare, alert, gather evidence, draft) nightly without human action, using the currently approved model.
- **FR-014a**: System MUST retrain the model weekly without human action, evaluate the candidate per FR-013, and record each attempt, its outcome and reason. If no retrain has succeeded in 14 days, CS managers MUST see a notice.
- **FR-014b**: Each nightly run MUST advance the data cutoff by one day once, regardless of per-tenant outcomes. A tenant that failed a previous cutoff MUST be caught up (missed cutoffs processed in order, at most 3) before the new cutoff; beyond 3 missed cutoffs the tenant is marked as needing attention and processing resumes at the latest cutoff.
- **FR-015**: System MUST record every run per tenant with start and end time, status, data cutoff, model version, and counts.
- **FR-016**: Re-running the pipeline for the same cutoff MUST produce the same scores and MUST NOT create duplicate alerts or drafts.
- **FR-017**: System MUST raise alerts only on material changes (see Definitions), and MUST NOT raise a repeat alert for the same unchanged condition.
- **FR-017a**: System MUST cap alerts per CSM at 3 per night and 10 per rolling 7 days. When the cap is exceeded, alerts are prioritised by trigger (severe event near renewal, then entered top K, then risk jump), then by probability × contract value; the rest are counted as suppressed in the run report.
- **FR-017b**: Alerts MUST be delivered in-app (alerts inbox with unread count and "new" markers on worklist rows). Alerts are shown to the account's current owner and keep a record of whom they were originally raised for. Alerts for unassigned accounts go to all CS managers in the tenant.
- **FR-018**: If a run fails, the system MUST keep showing the last successful results, labelled with their date, and show that the latest run did not complete.
- **FR-018a**: For each alert, the unattended agent MUST gather supporting evidence from all relevant sources and attach it to the alert with links to source records. The agent MUST NOT contact customers or modify account data.

**Worklist and traceability**

- **FR-019**: CSMs MUST be able to view a worklist of only their own accounts, grouped as Renewal overdue, Act this week (high), Watch (medium), Healthy (low), Insufficient data, Snoozed, Later renewals, and No model yet. Within groups, scored accounts are ordered by risk and show risk estimate, band, top reasons, confidence, days to renewal and contract value.
- **FR-020**: Every displayed score MUST come from a stored, unchangeable scoring record that includes run ID, timestamp, model version, signal-definition version, data cutoff, signal values and their contributions.
- **FR-021**: Users MUST be able to drill from any reason to its signal definition, window, and the source records that contributed to it. For absence-based signals, the drill-down MUST show the window searched, that no qualifying records were found, and the most recent qualifying record before the window (or "none since account start").
- **FR-022**: Users MUST be able to see an account's risk history across past runs.
- **FR-022a**: CSMs and CS managers MUST be able to record an action on an account (contacted, meeting booked, snoozed until a future date, dismissed with a non-empty reason). Actions MUST be stored with who and when, shown on the account, and suppress repeat alerts until a new material change occurs.
- **FR-022b**: Recorded actions and dismissal reasons MUST be kept as feedback available for future evaluation of the model and alert rules.

**Outreach drafts**

- **FR-023**: For each alert, the system MUST create exactly one draft (an internal summary and a suggested customer email) using only facts from that alert's evidence, with each draft linked to the evidence it used. Re-running a cutoff MUST NOT regenerate existing drafts.
- **FR-023a**: Only the evidence pack (account name, contract value, renewal date, plain-language facts, CSM first name) MAY be sent to an external drafting service. Customer contact names, email addresses and raw record contents MUST NOT be sent, and a request MUST never contain more than one tenant's data. Drafting via an external service MUST be switchable off per tenant.
- **FR-023b**: If the drafting service is disabled, unavailable, slower than 20 seconds, or returns a draft that cites facts not in the evidence or numbers/dates not in the evidence, the system MUST store a template draft built only from the evidence, labelled as a template, and record why.
- **FR-024**: System MUST NOT send email to customers. On approval it MUST record who approved, when, and the final text, and let the user copy the text or open it in their own mail client. A draft may be approved by the account's owner or a CS manager of the tenant.

**Multi-tenancy and access**

- **FR-025**: Every record, score, alert, draft, action and report MUST belong to exactly one tenant, and users MUST only be able to access their own tenant's data. This MUST be enforced by the data store, not only by application code.
- **FR-025a**: Artefacts of the pooled model MUST NOT expose another tenant's data: per-account reasons are computed only from that account's signals, calibration is per tenant, and in-app evaluation views show only the viewer's tenant metrics and pooled aggregate totals.
- **FR-026**: Requests without a signed-in session MUST be rejected. Requests for a resource in another tenant, or (for CSMs) owned by another CSM, MUST receive the same "not found" response as a non-existent resource, on pages and data endpoints alike, and MUST be logged.
- **FR-026a**: The access log MUST be readable only by system operators (not tenant users), and entries kept for at least 365 days.
- **FR-027**: System MUST support two roles per tenant. **CSM**: sees and acts on own accounts and their alerts. **CS manager**: sees all accounts, alerts and runs in the tenant, sees the Unassigned and Outcome unknown queues, can record actions, acknowledge alerts and approve drafts on any tenant account.
- **FR-027a**: Users MUST sign in; tenant and role MUST be determined by the server from the signed-in session, never from values supplied by the browser. Sessions MUST expire 12 hours after sign-in and be invalidated server-side on sign-out. Sign-in MUST be limited to 10 attempts per minute per IP address and per email, with a generic error that does not reveal whether an account exists.
- **FR-027b**: Demo accounts (at least one CSM and one CS manager per tenant) and their credentials MUST be created and listed on the sign-in page only when demo mode is enabled. Demo tenants contain synthetic data only.
- **FR-027c**: Unauthenticated health endpoints MUST reveal only overall status and a count of stale tenants: no tenant names, identifiers or data volumes.

**Synthetic data**

- **FR-028**: System MUST include a reproducible generator producing ~5,000 accounts and ~200,000 events across all five sources and at least three tenants of different sizes.
- **FR-029**: Generated data MUST include planted churn drivers with noise (usage decline, seat under-utilisation, support ticket spikes with slow resolution, payment failures, champion departure, lack of CSM contact), sparse accounts, duplicates, missing fields, conflicting values, timestamps without timezone, unmatched records, late and out-of-order events, and history spanning up to three years. The data profile report MUST list each planted driver with its definition, prevalence and effect on churn odds.
- **FR-029a**: Generator parameters MUST be fixed and committed before model evaluation begins. If success criteria are not met, the shortfall MUST be reported as is; any later generator change MUST be documented with its reason and all reports regenerated. Generator parameters MUST NOT be tuned against held-out evaluation results.
- **FR-030**: Generated data MUST include a labelled set of silent-churn accounts (see Definitions), about 15% of churned accounts, and the evaluation MUST report how early each was flagged.

**Retention**

- **FR-031**: Retention policy: raw records 3 years rolling; signal snapshots, scores, alerts, drafts and actions 2 years; access log 365 days. Automated purging is out of scope for v1; the policy is documented and data volumes in v1 stay within it.

### Key Entities

- **Tenant**: A customer organisation using the system. Owns all other data; boundary for access. Holds settings (high-band size and floor, alert thresholds, drafting on/off).
- **User**: A person in a tenant with a role (CSM or CS manager).
- **Account**: A customer of the tenant. Has an owner (CSM, or none), renewal date, contract value, contract type (term or monthly), and links to identities in each source.
- **Source Record**: A raw event from one of the five sources, with when it happened and when it was received. Never modified.
- **Account Link**: The match between a source record and an account, with method and match time. Never modified.
- **Quarantined Record**: A source record that couldn't be linked to exactly one account, kept with the reason and age.
- **Signal Snapshot**: The set of computed signal values for one account as of one data cutoff, with the signal-definition version.
- **Model Version**: A fitted risk model with its training period, history window choice, evaluation results, status and promotion decision.
- **Scoring Run**: One execution of the pipeline for one tenant: timing, status, cutoff, model version, data-quality and alert counts (raised and suppressed).
- **Account Score**: The result for one account in one run: state, risk estimate, band, confidence, reasons with contributions. Unchangeable once written.
- **Alert**: A material change for one account in one run, with trigger, evidence, original and current recipient, and status.
- **Outreach Draft**: Text prepared for an alert (LLM or template), linked to the evidence it used, with approval status, approver and final text.
- **CSM Action**: Something a CSM or manager did about an account (contacted, meeting booked, snoozed, dismissed with reason), with who and when.
- **Renewal Outcome**: Whether an account renewed, churned, or downgraded at a renewal date. Only "churned" is the model's positive label; "downgraded" counts as renewed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

All model criteria are measured on the held-out test period (the most recent ~6 months of history), by replaying weekly worklists, per tenant (for tenants with at least 30 churn events in the period) and overall.

- **SC-001**: At least 70% of accounts that churned appeared in their CSM's high band at some point at least 60 days before their renewal date. *(60 days: the minimum time for a meaningful save play, such as an executive call plus a remediation plan; it falls inside the 90-day horizon.)*
- **SC-002**: The churn rate among high-band accounts is at least 3× the churn rate among all scored accounts (accounts inside the renewal horizon) in the same tenant and period.
- **SC-003**: The model beats the logistic-regression baseline on top-10 hit rate and median lead time.
- **SC-004**: Risk estimates are honest: in each of 10 equal-frequency probability bins with at least 50 examples, the observed churn rate is within 10 percentage points of the mean predicted probability.
- **SC-005**: A CSM can go from opening the system to knowing their first three accounts to contact, and why, in under 2 minutes.
- **SC-006**: 100% of displayed scores can be traced to a stored scoring record, and every reason to a signal definition plus source records (or an explicit "no records in window" result).
- **SC-007**: The nightly run completes without human action on every scheduled night in a 7-day test, and finishes within 30 minutes for ~5,000 accounts.
- **SC-008**: Zero cross-tenant data exposure in access tests covering every route in the application's route inventory; adding a route without an access test fails the test suite.
- **SC-009**: Each CSM receives on average no more than 5 alerts per week (hard cap 10 per FR-017a), so alerts stay worth reading.
- **SC-010**: Zero emails are sent by the system; 100% of approved drafts have a recorded approver, time and final text.
- **SC-011**: At least 5 of the 6 planted churn drivers appear among the model's top 10 signals by mean absolute contribution.
- **SC-012**: At least 70% of silent-churn accounts enter their CSM's high band at least 30 days before renewal. *(30 days rather than 60, because by definition their warning signs appear later and in fewer sources; 30 days is the minimum for any intervention.)*
- **SC-013**: The system is available at a live address with seeded data for multiple tenants, and a reviewer can sign in as a CSM of any tenant and act on the worklist without setup.

## Assumptions

- **Churn definition** (confirmed): see Definitions. Downgrades and seat reductions are recorded as outcomes but are not counted as churn in v1.
- **Renewal horizon**: 90 days, early enough for a CSM to act. Accounts further out are listed but not scored; getting earlier warnings (e.g. a 180-day horizon) is a v2 trade-off against accuracy.
- **CSM capacity**: A CSM can meaningfully act on ~10–15 accounts per week; the default high band size is 10 and is configurable per tenant.
- **Daily freshness is enough**: Nightly scoring is sufficient for v1; same-day alerting for critical events is out of scope.
- **In-app alerts only**: Email, Slack or digest delivery of alerts is out of scope for v1.
- **Synthetic data only**: Real connectors to the five source systems are out of scope; the generator stands in for them, delivering data through the same ingestion path real connectors would use.
- **Source of truth**: Billing for contract value and renewal date, CRM for owner and segment; configurable.
- **Older data**: Up to three years of history is available; whether to use all of it is decided by evaluation (FR-012b), not assumed.
- **Pooled learning** (confirmed): see FR-012a and FR-025a.
- **Authentication** (confirmed): Real sign-in with server-enforced sessions. SSO and self-service sign-up are out of scope.
- **External drafting service**: The drafting provider is used under terms that exclude training on submitted data; only the minimal evidence pack is sent (FR-023a). Tenants can switch it off.
- **No sending by the system** (confirmed): Outreach is drafted only; approval is recorded and the user sends from their own mail client.
- **Deliverables outside this spec**: The exercise also requires a short slide deck, a README (what was decided, what was deliberately skipped, what breaks first at 10x scale), a repository, and a recorded walkthrough. These document the system and are not product features, but the system must be built so that each decision in them can be traced to code and evaluation results.
- **Out of scope for v1**: real-time streaming, SSO, mobile layouts, CRM write-back, automated customer emails, alert delivery outside the app, automated data purging, and playbook recommendations beyond drafted outreach.
