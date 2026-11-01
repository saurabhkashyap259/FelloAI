# Loom script (about 10 minutes)

Spoken lines are in quotes. **Do** lines are what to click or type. Times are targets, not limits.

## Before recording

- [ ] Open https://worklist-web.onrender.com in a clean browser window. Zoom to 125% so text is readable on video.
  - Fallback if the live site misbehaves: `docker compose up -d db`, then `uv run uvicorn worklist.web.app:app --port 8010` and use http://localhost:8010 (password `demo-pass-123`).
- [ ] Open `docs/solution-brief.pptx` in presenter mode on the same screen.
- [ ] Open a terminal in the repo at a large font size. Load the live database settings off camera, and never `cat` that file on screen:
  ```bash
  set -a; . ./.env.render; set +a
  ```
- [ ] Sign out of the app so the recording starts at the sign-in page.
- [ ] Demo accounts used below (password: your `DEMO_PASSWORD` from Render):
  - `csm01@northwind.demo`: has an alert on **Dunder Foods 419** (plus one on Zorg Capital 2157, which the script doesn't use) and a CRM/billing conflict on **Soylent Systems 1**.
  - `manager@northwind.demo`: for `/runs` and `/model`.
  - `csm01@bluefin.demo`: for the cross-tenant check.
- [ ] Drafts will show the **Template** label because no `ANTHROPIC_API_KEY` is set. That's fine; it's explained below. Don't add a key just before recording: the live Claude path hasn't been tried against the real API yet.

---

## 1. The ask, reframed (0:00–1:00) · slides 1–2

**Do:** slide 1.

"Hi, I'm Saurabh. This is my take-home for the Staff Engineer role. The request was a red, yellow and green health dashboard, plus automatic emails to accounts that turn red. I built something different, and I'll explain why. Everything you'll see is running live on Render, and the link is in the README."

**Do:** slide 2.

"Two renewals were lost while the warning signs sat in support tickets and product usage. That isn't a visibility problem. The signals existed. Nobody was watching across five systems. A CSM has about 120 accounts and can really act on about ten a week. So what they need is a short ranked list every Monday: which accounts are most likely not to renew, why, and how sure the system is."

## 2. Where I disagreed (1:00–2:00) · slide 3

**Do:** slide 3.

"I pushed back on three things. First, a colour doesn't tell a CSM who to call first. If 40 accounts are red, where do you start? So the bands follow capacity: 'Act this week' means top ten in your book and at least a 15% chance of not renewing.

Second, no automatic emails to customers. If the model is wrong, we've just told a healthy customer we think they're about to leave. So the system drafts, and a CSM approves and sends from their own email. The system never sends anything.

Third, 'nothing slips' can't be promised. Instead I measure how many churners the list catches and how early, and I report the misses."

## 3. How it works (2:00–2:45) · slides 4–5

**Do:** slide 4.

"Five stages. Ingest takes five messy sources, removes duplicates and matches records to accounts. Signals are computed as of a cutoff using only data received before it, so nothing from the future leaks into training. One LightGBM model is shared across tenants and calibrated per tenant. A nightly agent runs on its own. And the worklist only reads stored scores. Claude writes drafts; it never scores."

**Do:** slide 5.

"The data is synthetic, so I planted six churn drivers and know the right answers. It's also deliberately messy: duplicates, late data, timezone problems, and CRM and billing disagreeing on renewal dates. I froze the generator before I evaluated anything."

## 4. Demo: Monday morning (2:45–5:00) · browser

**Do:** switch to the browser (https://worklist-web.onrender.com) and sign in as `csm01@northwind.demo`.

"This is what a CSM sees on Monday."

**Do:** point at the line at the top showing the run, cutoff and model version.

"Every page says which run, data cutoff and model version it came from. Nothing is calculated on the fly."

**Do:** scroll through the groups: Renewal overdue, Act this week, Watch, Insufficient data.

"The groups are 'Renewal overdue', 'Act this week', 'Watch' and so on. Note 'Insufficient data': accounts with too little history are flagged as unknown, never shown as healthy."

**Do:** in 'Act this week', click one of the reasons on a row.

"Each reason opens its definition, the time window, and the actual source records behind it. For an absence signal like 'no CSM contact', it shows how long it's been since the last contact."

**Do:** go back and open **Soylent Systems 1** (search the page with Cmd+F if needed).

"On the account page, billing and the CRM disagree: billing says the renewal is January 26th, the CRM says February 6th. The system picks billing by a fixed rule and shows the conflict, rather than hiding it. Below that is the full list of what pushed the score up or down, and the risk history over time."

**Do:** back to the worklist. On a row in 'Act this week', choose **Snooze until…** in the action dropdown, pick a date next week, and submit.

"If a CSM has already handled something, they snooze it, and it moves to 'Snoozed'. That's recorded, and it stops the agent re-alerting on the same thing."

## 5. Demo: the unattended agent (5:00–7:00) · browser + terminal

**Do:** open Alerts, then the alert on **Dunder Foods 419**.

"Overnight the agent raised this alert: a severe event on an account close to renewal. It only alerts when something changes, and it's capped at three per CSM a night and ten a week. Across seven simulated nights it averaged under one alert per CSM per week."

**Do:** point at the evidence list, then the draft.

"Here's the evidence it gathered, each item linked to its records, and a draft email. This one says 'Template' because I haven't set an API key in this environment. With a key, Claude writes the draft, but it only sees these numbered facts. A validator rejects any fact, number or date that isn't in the evidence, and the word 'risk' in a customer email. If anything fails, it falls back to this labelled template."

**Do:** click **Approve text**, then point at the **Copy** and **Open in mail client** buttons.

"Approving doesn't send anything. The CSM copies it or opens it in their own mail client."

**Do:** sign out, sign in as `manager@northwind.demo`, open `/runs`.

"Managers see every nightly run: what came in, duplicates dropped, records quarantined because they couldn't be matched, and alerts raised or suppressed."

**Do:** in the terminal (with the live settings already loaded, see the checklist), run:

```bash
uv run worklist nightly --no-advance; echo "exit code $?"
```

"This is the live database. Running the same night again does nothing. It exits with code 3, meaning skipped. It also catches up at most three missed nights before flagging that someone needs to look."

## 6. How I know it works (7:00–8:45) · `/model` + slides 6–7

**Do:** open `/model` in the browser.

"The model is evaluated on a later period than it was trained on, by replaying the weekly worklists the way a CSM would have seen them. Retraining is weekly and gated: a new model is only adopted if it's no worse than the current one.

One honest note: the slides show my local training run. The live one came out slightly different, for example Bluefin's own model was promoted here and rejected locally, because training reads rows without a fixed order. Every target still passes or fails the same way, and the README explains the one-line fix."

**Do:** slide 6.

"Five targets are met. Churn is five and a half times more likely in 'Act this week' than across all accounts. The percentages are honest: the largest calibration gap is about six points. Silent churners, like the two lost renewals, are caught 30 days ahead 74% of the time. And the agent is fast and quiet."

**Do:** slide 7.

"Two targets are missed, and I want to be upfront about them. Only 26% of churners reach the top list 60 days ahead. Mostly that's because my generator starts the warning signs 55 to 160 days before renewal, so at 60 days many haven't started. And the model finds four of the six planted drivers, not five. The two it misses are rare. I could have retuned the generator until these passed, but then I'd be measuring the generator, not the model. Next I'd try a longer horizon and denser usage data."

## 7. Isolation and traceability (8:45–9:30) · browser + slide 9

**Do:** as the manager, open an account and copy its URL. Sign out, sign in as `csm01@bluefin.demo`, paste the URL.

"This is a Northwind account URL, opened as a Bluefin user. It returns the same 'not found' page as an ID that doesn't exist, so you can't even tell it exists. The attempt is logged."

**Do:** slide 9.

"Isolation is enforced by Postgres row-level security, not just the app, so an application bug can't leak another tenant's data. A test fails if anyone adds a route without an access rule. And every number on screen comes from a stored score that can be recomputed exactly."

## 8. Limits and close (9:30–10:00) · slide 10

**Do:** slide 10.

"I deliberately skipped real connectors and any automatic sending. At ten times the size, the first thing to break is feature computation in a single Python process, then training time, then LLM calls inside the nightly job. The README has the full list. The code and spec are on GitHub, and the live system is at worklist-web.onrender.com. Thanks for watching."

---

## If something goes wrong on camera

- **The live site is slow or errors:** switch to the local fallback in the checklist; the data is the same.
- **Sign-in fails on live:** check the password is your Render `DEMO_PASSWORD`, not `demo-pass-123`.
- **The account isn't in the list:** use Cmd+F, or open it from the Alerts page instead.
- **Over time:** cut section 3 to slide 4 only, and skip the terminal step in section 5.
