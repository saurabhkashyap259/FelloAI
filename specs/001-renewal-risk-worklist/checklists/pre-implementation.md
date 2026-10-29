# Pre-Implementation Checklist: Renewal Risk Worklist for CSMs

**Purpose**: Unit tests for the requirements: check that the spec and plan are complete, unambiguous and consistent in the four highest-risk areas before generating tasks.
**Created**: 2026-10-27
**Feature**: [spec.md](../spec.md) · [plan.md](../plan.md) · [research.md](../research.md) · [data-model.md](../data-model.md)
**Focus**: Model & evaluation · Multi-tenant security · Unattended agent & alerts · Data & traceability
**Depth**: Standard pre-implementation gate · **Audience**: author, before `/speckit.tasks`

Reference key: `Spec §FR-xxx / §SC-xxx`, `Research Rn`, `Data-model §table`, `Contract <file>`.

## Model & Evaluation: Requirement Clarity

- [x] CHK001 - Is it specified what the risk estimate means for accounts with **no renewal in the next 90 days**: scored, scored-but-deprioritised, or excluded? (Research R5 labels only accounts with a renewal in the window.) [Ambiguity, Spec §FR-008, Research R5]
  - **Resolved**: Not scored: state `not_due`, "Later renewals" group → Spec Definitions (Renewal horizon), FR-008, Story 1 #6; Research R5
- [x] CHK002 - Is the churn definition specified for **monthly-renewing** subscriptions, where "did not renew within 90 days of renewal date" is ambiguous? [Gap, Spec §Clarifications Q1, Research R3]
  - **Resolved**: Monthly churn = cancelled, not reactivated within 30 days; always inside horizon → Spec Definitions (Churn); Research R5
- [x] CHK003 - Is the "minimum-data threshold" for `insufficient data` quantified in the spec, or only in research? [Clarity, Spec §FR-010, Research R8]
  - **Resolved**: <5 matched records in 180 days, or age <60 days with no usage → Spec Definitions, FR-010
- [x] CHK004 - Is "enough history" for a tenant-specific model quantified in the spec (Research R6 uses ≥1,000 examples and ≥50 churns)? [Clarity, Spec §FR-012a]
  - **Resolved**: ≥1,000 labelled examples and ≥50 churns → FR-012a
- [x] CHK005 - Is "performs at least as well" in the promotion gate defined with named metrics, tolerances and a tie-break when metrics disagree? [Clarity, Spec §FR-013, Research R7]
  - **Resolved**: Non-inferiority (0.01) on top-10 hit rate, PR-AUC, Brier + strictly better on hit rate or PR-AUC; baseline when no current model → FR-013; Research R7

## Model & Evaluation: Consistency and Measurability

- [x] CHK006 - Are the lead-time windows consistent across criteria (SC-001 uses 60 days, SC-012 uses 30 days, the prediction horizon is 90 days), and is the difference intentional and explained? [Consistency, Spec §SC-001, §SC-012, §FR-008]
  - **Resolved**: 90-day horizon = scoring window; 60 days (SC-001) = minimum save-play lead time; 30 days (SC-012) for silent churn, with reasons inline
- [x] CHK007 - Does FR-011 (high band sized to capacity) agree with the probability floor added in Research R8, given that Story 1 scenario 3 says bands come from capacity "rather than a fixed score cutoff"? [Conflict, Spec §FR-011, Research R8]
  - **Resolved**: High = top K **and** ≥ floor; Story 1 #3 reworded; medium covers overflow → FR-011; Research R8
- [x] CHK008 - Is the "overall churn rate" baseline in SC-002 defined per tenant, per CSM, or globally, and over which period? [Clarity, Spec §SC-002]
  - **Resolved**: Churn rate among all in-horizon scored accounts, same tenant and period → SC-002
- [x] CHK009 - Are the calibration bins, the minimum accounts per bin, and the evaluation period specified for SC-004's "within 10 percentage points"? [Measurability, Spec §SC-004]
  - **Resolved**: 10 equal-frequency bins, ≥50 examples per bin, test period, per tenant (≥30 churns) and overall → SC-004 + preamble
- [x] CHK010 - Is it specified how planted-driver recovery (SC-011) is measured, e.g. drivers expected within the top N by mean absolute contribution? [Measurability, Spec §SC-011]
  - **Resolved**: ≥5 of 6 planted drivers in top 10 by mean absolute contribution → SC-011; drivers listed in FR-029
- [x] CHK011 - Do the requirements state what happens if the success criteria are **not** met on generated data, so the data isn't silently retuned until the model passes? [Gap, Assumption, Spec §SC-001–SC-004]
  - **Resolved**: Generator frozen before evaluation; shortfalls reported, never tuned on held-out results → FR-029a
- [x] CHK012 - Is the selection rule for "silent churn" accounts defined precisely enough (which sources must and must not show signals) to be checked? [Clarity, Spec §FR-030, Story 7 scenario 4]
  - **Resolved**: Precise silent-churn definition (sources absent / signals present, 120-day window, ~15% of churners) → Spec Definitions, FR-030; Research R3
- [x] CHK013 - Is the treatment of older history (down-weighting or exclusion after the simulated product change) stated as a requirement with an evaluation, rather than only an assumption? [Assumption, Spec §Assumptions "Older data", Research R3]
  - **Resolved**: Now a requirement: full vs. 18-month comparison, reported → FR-012b, Story 5 #5
- [x] CHK014 - Are the planted churn drivers required to be documented in a defined place and format, so the README and the evaluation can reference them? [Completeness, Spec §FR-029]
  - **Resolved**: Planted-driver table (definition, prevalence, effect) in the data profile report → FR-029; Research R3

## Multi-Tenant Security: Completeness

- [x] CHK015 - Are CS manager permissions fully specified: can they record actions, approve drafts or acknowledge alerts for accounts owned by a CSM? [Gap, Spec §FR-027]
  - **Resolved**: Managers can record actions, acknowledge alerts, approve drafts on any tenant account → FR-027, FR-022a, FR-024
- [x] CHK016 - Is it specified who can see and act on **unassigned** accounts and their alerts? [Gap, Spec §Edge Cases "Account with no owner"]
  - **Resolved**: Unassigned queue for CS managers; their alerts go to all CS managers → Edge Cases, FR-017b, Story 6 #5
- [x] CHK017 - Do the requirements say whether sending tenant data to an external LLM provider is allowed, and which fields may leave the system? [Gap, Spec §FR-023, Contract llm-draft.md]
  - **Resolved**: Minimal evidence pack only; no contact names, emails or raw content; one tenant per request; per-tenant off switch → FR-023a; contract llm-draft.md
- [x] CHK018 - Is it required that pooled-model artefacts (explanations, reasons, calibration data, evaluation reports) never expose another tenant's records or statistics? [Gap, Spec §FR-012a, §Clarifications Q2]
  - **Resolved**: Per-account reasons, per-tenant calibration, in-app views limited to own tenant + pooled totals → FR-025a; Research R6
- [x] CHK019 - Are session lifetime, logout and sign-in rate limits specified in the spec, or only in the contract? [Completeness, Spec §FR-027a, Contract http.md]
  - **Resolved**: 12h expiry, server-side sign-out, 10/min per IP and per email, generic error → FR-027a
- [x] CHK020 - Is it specified who can view the access-audit log and how long entries are kept? [Gap, Spec §FR-026]
  - **Resolved**: Operators only (web role insert-only), ≥365 days → FR-026a; Data-model access_audit
- [x] CHK021 - Is it an explicit, bounded requirement that publicly listing demo credentials is acceptable only for the demo deployment, and can be turned off? [Assumption, Spec §Clarifications Q3, Research R11]
  - **Resolved**: Demo accounts and listing only in demo mode; synthetic data only → FR-027b

## Multi-Tenant Security: Clarity and Consistency

- [x] CHK022 - Is the response to a cross-tenant request specified the same way for pages and JSON (spec scenario says "refused", the contract specifies 404)? [Consistency, Spec §Story 6 scenario 2, Contract http.md]
  - **Resolved**: Same "not found" for pages and data endpoints → Story 6 #2, FR-026
- [x] CHK023 - Do the unauthenticated health endpoints have a requirement limiting what they may reveal about tenants? [Coverage, Contract http.md §healthz]
  - **Resolved**: Overall status + stale-tenant count only → FR-027c; contract http.md
- [x] CHK024 - Is SC-008's "every screen and data request" scoped to an enumerable list, so the claim can be verified? [Measurability, Spec §SC-008]
  - **Resolved**: Scoped to the route inventory, with a meta-test for new routes → SC-008; contract http.md §Route inventory

## Unattended Agent & Alerts: Clarity

- [x] CHK025 - Is "severe event close to renewal" defined (which event types, which priorities, how close)? [Ambiguity, Spec §FR-017, Data-model §tenant_settings]
  - **Resolved**: Urgent ticket / 2+ high in 7 days / final payment failure / cancel at period end / champion removed, within 60 days of renewal → Spec Definitions
- [x] CHK026 - Is "sharp risk increase" quantified in the spec, and is it absolute or relative? [Ambiguity, Spec §FR-017]
  - **Resolved**: Absolute +0.15 vs. score 7 days earlier, configurable → Spec Definitions; Data-model tenant_settings
- [x] CHK027 - Is the alert delivery channel specified (in-app only, email to the CSM, or digest), and when the CSM is expected to see it? [Gap, Spec §Story 3 scenario 2]
  - **Resolved**: In-app inbox + unread count + "new" row markers; other channels out of scope → FR-017b, Story 3, Assumptions
- [x] CHK028 - Is "new material change" for overriding a snooze or dismissal defined, for example as a different trigger or a further threshold? [Ambiguity, Spec §FR-022a, Story 3 scenario 7]
  - **Resolved**: Different trigger type, or a further risk jump from the probability at action time → Spec Definitions

## Unattended Agent & Alerts: Coverage and Recovery

- [x] CHK029 - Do the requirements reconcile the alert triggers with SC-009 (≤5 alerts per CSM per week): is there a cap or prioritisation when triggers exceed it? [Conflict, Spec §FR-017, §SC-009]
  - **Resolved**: Hard caps 3/night and 10/rolling 7 days, priority order, suppressed alerts counted → FR-017a, Story 3 #8, SC-009
- [x] CHK030 - Is the behaviour defined when some tenants fail in a run: does the simulated clock still advance, and how do retries interact with the next night's cutoff? [Gap, Recovery, Spec §FR-016, §FR-018, Research R4]
  - **Resolved**: Clock advances once per run; catch-up of up to 3 missed cutoffs, then needs_attention → FR-014b, Story 3 #9; Research R9
- [x] CHK031 - Is the first-deploy state specified, before any model is approved, consistently with the "Model unavailable" edge case? [Coverage, Spec §Edge Cases]
  - **Resolved**: Setup trains a model gated against the baseline; until then state `no_model`, no model-based alerts → Edge Cases, FR-013; contract cli.md
- [x] CHK032 - Are requirements defined for a failed or rejected weekly retrain (who is told, and what the UI shows)? [Gap, Exception Flow, Spec §FR-014a]
  - **Resolved**: Recorded with reason; model page shows last outcome; managers see a notice after 14 days without success → FR-014a, Edge Cases
- [x] CHK033 - Is it specified whether drafts are regenerated, kept or versioned when a run is re-executed for the same cutoff? [Gap, Spec §FR-016, §FR-023]
  - **Resolved**: Exactly one draft per alert; re-runs never regenerate → FR-023, FR-016; Data-model outreach_draft
- [x] CHK034 - Is the LLM fallback (template draft on failure, timeout or validation failure) stated in the spec, or only in the contract? [Completeness, Spec §FR-023, Contract llm-draft.md]
  - **Resolved**: Template fallback is now in the spec → FR-023b, Story 4 #5
- [x] CHK035 - Is it specified that alerts follow the owner at scoring time when ownership changes between runs, and what happens to already-open alerts? [Coverage, Spec §Edge Cases "Owner change"]
  - **Resolved**: Shown to the current owner; alert keeps who it was originally raised for → Edge Cases, FR-017b; Data-model alert

## Data & Traceability: Completeness

- [x] CHK036 - Are the identity-matching rules and source-precedence tolerances (±7 days, ±5%) part of the requirements, or only of research? [Completeness, Spec §FR-003, §FR-005, Research R12]
  - **Resolved**: Matching order, free-mail exclusion and tolerances now in the spec → FR-003, FR-005
- [x] CHK037 - Is re-processing of quarantined records (a later match) specified, including how it affects past results? [Gap, Spec §FR-003, Data-model §event_account]
  - **Resolved**: Retried nightly; a later match only affects later cutoffs; aged after 30 days → FR-003a; Data-model quarantined_event
- [x] CHK038 - Is "stale source" quantified (how long without data, relative to the source's normal cadence)? [Clarity, Spec §Edge Cases "Source outage", Research R8]
  - **Resolved**: Stale = no data for > max(2 days, 3× median delivery gap over the prior 30 days) → Spec Definitions
- [x] CHK039 - Is drill-down defined for signals based on the **absence** of events (e.g. "no CSM contact in 47 days"), where there is no single record to point to? [Gap, Spec §FR-021]
  - **Resolved**: Shows the window searched, "no records found", and the latest record before the window → FR-021, Story 2 #3; Research R12a
- [x] CHK040 - Is reproducibility required to cover signal-definition versions and model artefacts, not only raw data (FR-002)? [Completeness, Spec §FR-002, Data-model §signal_snapshot]
  - **Resolved**: Scores reference model and signal-definition versions, retained while referenced → FR-002, FR-020
- [x] CHK041 - Is a retention period specified for raw events, score history and drafts? [Gap, Spec §FR-022]
  - **Resolved**: Retention policy defined; automated purging out of scope for v1 → FR-031
- [x] CHK042 - Is the owner and workflow for accounts in `pending_review` (renewal passed with no outcome) specified? [Gap, Spec §Edge Cases "Renewal already passed"]
  - **Resolved**: Owner's "Renewal overdue" group; churned automatically after the grace period; contradictions go to the manager's "Outcome unknown" queue → Edge Cases, Story 1 #7
- [x] CHK043 - Is timezone normalisation of event times stated as a requirement, given that it determines which window an event falls into? [Gap, Contract source-events.md §Ingest rules]
  - **Resolved**: UTC normalisation, source timezone for naive timestamps, original value kept → FR-001a, Edge Cases

## Notes

- All 43 items resolved on 2026-10-27 by updating spec.md (new Definitions section; FR-001a, 003a, 012b, 014b, 017a/b, 023a/b, 025a, 026a, 027b/c, 029a, 031; revised FR-002/003/005/008/010–013/019/021–024/026/027a; SC-001/002/004/006/008/009/011/012) and aligning research.md, data-model.md and the contracts.
- Defaults chosen here (thresholds, caps, windows) are tenant-configurable where marked in the spec and can be revisited with `/speckit.clarify`.
- 43 items; 100% carry a traceability reference.
