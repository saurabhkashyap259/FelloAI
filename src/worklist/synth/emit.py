"""Five source emitters: observations of the latent world, shaped per contracts/source-events.md.

Events are dicts: {t (tenant index), source, rid (source record id or None), type, at (aware UTC
datetime, business time), payload}. Ingestion times and defects are added later in defects.py.
"""

from datetime import UTC, date, datetime, timedelta

import numpy as np

from worklist.synth.config import GeneratorConfig
from worklist.synth.world import Account, Cycle, history_start


class Emitter:
    def __init__(self, cfg: GeneratorConfig, rng: np.random.Generator, csm_emails: list[list[str]],
                 vendor_domains: list[str]) -> None:
        self.cfg, self.rng = cfg, rng
        self.csm_emails, self.vendor_domains = csm_emails, vendor_domains
        self.events: list[dict] = []
        self.hist_start = history_start(cfg)
        self.product_change = cfg.history_end - timedelta(days=30 * cfg.product_change_months_ago)
        self._seq = 0

    # --- helpers --------------------------------------------------------------------------
    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:07d}"

    def _at(self, day: date, hour: int | None = None) -> datetime:
        hour = int(self.rng.integers(8, 20)) if hour is None else hour
        return datetime(day.year, day.month, day.day, hour, int(self.rng.integers(0, 60)), tzinfo=UTC)

    def _emit(self, acc: Account, source: str, etype: str, day: date, payload: dict, rid: str | None = None,
              hour: int | None = None) -> None:
        if day < acc.active_from or day > self.cfg.history_end or day > acc.active_until + timedelta(days=30):
            return
        self.events.append({"t": acc.tenant_idx, "source": source, "rid": rid, "type": etype,
                            "at": self._at(day, hour), "payload": payload, "acc": acc.idx})

    def _days(self, start: date, end: date, rate_per_year: float) -> list[date]:
        span = (end - start).days
        if span <= 0:
            return []
        n = self.rng.poisson(rate_per_year * span / 365)
        return sorted(start + timedelta(days=int(d)) for d in self.rng.integers(0, span, size=n))

    def _owner_email(self, acc: Account, day: date) -> str | None:
        owner = acc.owner_idx
        if acc.owner_change and day >= acc.owner_change[0]:
            owner = acc.owner_change[1]
        return None if owner is None else self.csm_emails[acc.tenant_idx][owner]

    def _cycle_at(self, acc: Account, day: date) -> Cycle | None:
        for cyc in acc.cycles:
            if cyc.start <= day < cyc.renewal_date or (cyc.start <= day and cyc is acc.cycles[-1]):
                return cyc
        return None

    def end(self, acc: Account) -> date:
        return min(self.cfg.history_end, acc.active_until)

    # --- CRM ------------------------------------------------------------------------------
    def crm(self, acc: Account, conflict: dict) -> None:
        sf = acc.ids["crm"]

        def upsert(day: date, renewal: date, arr: float) -> None:
            renewal_shown = renewal + timedelta(days=conflict.get("renewal_offset", 0))
            arr_shown = conflict.get("stale_arr") or arr
            self._emit(acc, "crm", "account.upserted", day, {
                "sf_account_id": sf, "name": acc.name, "domain": acc.domain,
                "owner_email": self._owner_email(acc, day), "segment": acc.segment, "arr": arr_shown,
                "renewal_date": renewal_shown.isoformat()}, rid=self._id("sfu"))

        first = acc.cycles[0]
        upsert(acc.active_from, self._next_renewal(acc, acc.active_from), first.arr_before)
        self._emit(acc, "crm", "contact.changed", acc.active_from + timedelta(days=2), {
            "sf_account_id": sf, "contact_email": f"champion@{acc.domain}", "role": "champion",
            "change": "added"}, rid=self._id("sfc"))
        if acc.owner_change:
            day = acc.owner_change[0]
            upsert(day, self._next_renewal(acc, day), acc.arr)
        for cyc in acc.cycles:
            if acc.contract_type == "term" and cyc.outcome in ("renewed", "downgraded"):
                day = cyc.renewal_date + timedelta(days=int(self.rng.integers(1, 6)))
                upsert(day, cyc.renewal_date + timedelta(days=365), cyc.arr_after)
            if cyc.opp_at_risk:
                self._emit(acc, "crm", "opportunity.updated", cyc.opp_at_risk, {
                    "sf_account_id": sf, "stage": "renewal_at_risk", "amount": round(cyc.arr_before * 0.5, -2),
                    "close_date": cyc.renewal_date.isoformat()}, rid=self._id("sfo"))
            if cyc.outcome in ("renewed", "downgraded", "churned") and acc.contract_type == "term":
                self._emit(acc, "crm", "opportunity.updated", cyc.renewal_date + timedelta(days=3), {
                    "sf_account_id": sf, "stage": "closed_lost" if cyc.outcome == "churned" else "closed_won",
                    "amount": cyc.arr_after, "close_date": cyc.renewal_date.isoformat()}, rid=self._id("sfo"))
            if "champion_departure" in cyc.drivers:
                onset = cyc.drivers["champion_departure"]
                self._emit(acc, "crm", "contact.changed", onset, {
                    "sf_account_id": sf, "contact_email": f"champion@{acc.domain}", "role": "champion",
                    "change": "removed"}, rid=self._id("sfc"))
                if self.rng.random() < 0.3:
                    self._emit(acc, "crm", "contact.changed", onset + timedelta(days=int(self.rng.integers(10, 30))),
                               {"sf_account_id": sf, "contact_email": f"newlead@{acc.domain}", "role": "champion",
                                "change": "added"}, rid=self._id("sfc"))

    def _next_renewal(self, acc: Account, day: date) -> date:
        for cyc in acc.cycles:
            if cyc.renewal_date > day:
                if acc.contract_type == "monthly":
                    return day + timedelta(days=30 - (day - acc.contract_start).days % 30)
                return cyc.renewal_date
        return acc.cycles[-1].renewal_date

    # --- Billing --------------------------------------------------------------------------
    def billing(self, acc: Account) -> None:
        cus = acc.ids["billing"]
        monthly = acc.contract_type == "monthly"

        def sub(day: date, period_end: date, arr: float, cancel: bool) -> None:
            self._emit(acc, "billing", "subscription.updated", day, {
                "stripe_customer_id": cus, "plan": f"{acc.segment}_{acc.contract_type}",
                "mrr": round(arr / 12, 2), "current_period_end": period_end.isoformat(),
                "cancel_at_period_end": cancel}, rid=self._id("evt_s"))

        def invoice(day: date, amount: float) -> str:
            inv = self._id("in_")
            self._emit(acc, "billing", "invoice.paid", day, {
                "invoice_id": inv, "stripe_customer_id": cus, "amount": round(amount, 2), "currency": "usd"},
                rid=f"{inv}_paid")
            return inv

        if monthly:
            day = acc.contract_start
            cycles = iter(acc.cycles)
            cyc = next(cycles)
            notified = False
            while day <= self.end(acc):
                while cyc.renewal_date <= day and cyc is not acc.cycles[-1]:
                    cyc = next(cycles)
                arr = cyc.arr_before
                if day >= acc.active_from:
                    nxt = day + timedelta(days=30)
                    cancel = cyc.cancel_notice is not None and day >= cyc.cancel_notice
                    if acc.churn_date and nxt >= acc.churn_date:
                        sub(day, acc.churn_date, arr, True)
                        break
                    if day <= acc.active_from + timedelta(days=29) or (cancel and not notified):
                        sub(day, nxt, arr, cancel)  # Stripe sends updates on change, not every period
                        notified = notified or cancel
                    invoice(day, arr / 12)
                day += timedelta(days=30)
        else:
            sub(acc.active_from, acc.cycles[0].renewal_date, acc.cycles[0].arr_before, False)
            invoice(max(acc.active_from, acc.contract_start), acc.cycles[0].arr_before)
            for cyc in acc.cycles:
                if cyc.cancel_notice:
                    sub(cyc.cancel_notice, cyc.renewal_date, cyc.arr_before, True)
                if cyc.outcome in ("renewed", "downgraded"):
                    lag = int(self.rng.integers(0, 4))
                    day = cyc.renewal_date + timedelta(days=lag)
                    sub(day, cyc.renewal_date + timedelta(days=365), cyc.arr_after, False)
                    invoice(day, cyc.arr_after)
                elif cyc.outcome == "churned":
                    sub(cyc.renewal_date, cyc.renewal_date, 0.0, True)
        for cyc in acc.cycles:
            if "payment_failures" in cyc.drivers:
                onset = cyc.drivers["payment_failures"]
                inv = self._id("in_")
                attempts = int(self.rng.integers(1, 5))
                for a in range(1, attempts + 1):
                    self._emit(acc, "billing", "invoice.payment_failed", onset + timedelta(days=4 * (a - 1)), {
                        "invoice_id": inv, "stripe_customer_id": cus, "attempt": a}, rid=f"{inv}_f{a}")
                if attempts < 4 or self.rng.random() < 0.5:
                    self._emit(acc, "billing", "invoice.paid", onset + timedelta(days=4 * attempts + 2), {
                        "invoice_id": inv, "stripe_customer_id": cus, "amount": round(acc.arr / 12, 2),
                        "currency": "usd"}, rid=f"{inv}_paid")

    # --- Support --------------------------------------------------------------------------
    def support(self, acc: Account) -> None:
        rate = self.cfg.tickets_per_year[acc.segment] * (0.25 if acc.sparse else 1.0)
        for day in self._days(acc.active_from, self.end(acc), rate):
            pr = self.rng.choice(["low", "normal", "high", "urgent"], p=[0.3, 0.5, 0.17, 0.03])
            cyc = self._cycle_at(acc, day)
            sat_p = [0.45, 0.45, 0.10] if cyc is not None and cyc.bad_csat else [0.7, 0.25, 0.05]
            self._ticket(acc, day, str(pr), float(self.rng.uniform(0.3, 4)), sat_p)
        for cyc in acc.cycles:
            if "ticket_spike_slow_resolution" in cyc.drivers:
                onset = cyc.drivers["ticket_spike_slow_resolution"]
                for _ in range(int(self.rng.integers(3, 7))):
                    day = onset + timedelta(days=int(self.rng.integers(0, 30)))
                    priority = "urgent" if self.rng.random() < 0.3 else "high"
                    self._ticket(acc, day, priority, float(self.rng.uniform(5, 15)), [0.3, 0.3, 0.4])
            if cyc.bad_csat:
                for _ in range(int(self.rng.integers(1, 3))):
                    day = cyc.renewal_date - timedelta(days=int(self.rng.integers(30, 100)))
                    self._ticket(acc, day, "normal", float(self.rng.uniform(2, 8)), [0.0, 0.0, 1.0])

    def _ticket(self, acc: Account, day: date, priority: str, resolve_days: float, sat_p: list[float]) -> None:
        ticket = self._id("zt")
        requester = f"user{int(self.rng.integers(1, 40))}@{acc.domain}"
        org: str | None = acc.ids["support"]
        if self.rng.random() < 0.15:
            org = None  # missing org: matched by requester email domain
        if self.rng.random() < self.cfg.unmatched_rate * 3:
            requester = f"someone{int(self.rng.integers(1000))}@{self.rng.choice(self.cfg.free_mail_domains)}"
        self._emit(acc, "support", "ticket.created", day, {
            "ticket_id": ticket, "zendesk_org_id": org, "requester_email": requester, "priority": priority,
            "subject": "Issue reported"}, rid=f"{ticket}_c")
        if self.rng.random() < 0.25:
            self._emit(acc, "support", "ticket.updated", day + timedelta(days=max(1, int(resolve_days / 2))), {
                "ticket_id": ticket, "zendesk_org_id": org, "status": "pending", "priority": priority},
                rid=f"{ticket}_u")
        sat = self.rng.choice(["good", None, "bad"], p=sat_p)
        self._emit(acc, "support", "ticket.solved", day + timedelta(days=int(round(resolve_days))), {
            "ticket_id": ticket, "zendesk_org_id": org, "satisfaction": sat}, rid=f"{ticket}_s")

    # --- Email ----------------------------------------------------------------------------
    def email(self, acc: Account) -> None:
        vendor = self.vendor_domains[acc.tenant_idx]
        sparse = 0.2 if acc.sparse else 1.0
        quiet: list[tuple[date, date]] = [(c.drivers["no_csm_contact"], c.renewal_date) for c in acc.cycles
                                          if "no_csm_contact" in c.drivers]
        csm_days = self._days(acc.active_from, self.end(acc), self.cfg.csm_emails_per_year * sparse)
        for cyc in acc.cycles:  # renewal check-in ~60–90 days before renewal
            if acc.contract_type == "term" and self.rng.random() < 0.6:
                csm_days.append(cyc.renewal_date - timedelta(days=int(self.rng.integers(60, 95))))
        for day in csm_days:
            if any(a <= day <= b for a, b in quiet):
                continue
            payload = {"thread_id": self._id("th"), "from_domain": vendor, "to_domain": acc.domain,
                       "direction": "outbound", "is_csm": True}
            self._emit(acc, "email", "email.sent", day, payload, rid=self._id("msg"))
        for day in self._days(acc.active_from, self.end(acc), self.cfg.customer_emails_per_year * sparse):
            domain = acc.domain
            if self.rng.random() < self.cfg.unmatched_rate * 4:
                domain = str(self.rng.choice(self.cfg.free_mail_domains))
            self._emit(acc, "email", "email.received", day, {
                "thread_id": self._id("th"), "from_domain": domain, "to_domain": vendor,
                "direction": "inbound", "is_csm": True}, rid=self._id("msg"))
        for cyc in acc.cycles:
            if "champion_departure" in cyc.drivers:
                day = cyc.drivers["champion_departure"] + timedelta(days=int(self.rng.integers(5, 25)))
                self._emit(acc, "email", "email.bounced", day, {
                    "to_email": f"champion@{acc.domain}", "to_domain": acc.domain, "reason": "mailbox_unavailable"},
                    rid=self._id("msg"))

    # --- Product --------------------------------------------------------------------------
    def product(self, acc: Account, period_days: int) -> None:
        org = acc.ids["product"]
        seats = acc.seats
        self._emit(acc, "product", "seats.changed", acc.active_from, {
            "product_org_id": org, "seats_purchased": seats, "seats_assigned": int(seats * 0.85)})
        if acc.sparse:
            return  # low-engagement account: product never integrated
        offset = int(self.rng.integers(0, period_days))
        day = acc.active_from + timedelta(days=offset)
        sessions_per_user = float(self.rng.uniform(8, 20))
        while day <= self.end(acc):
            cyc = self._cycle_at(acc, day)
            util = acc.util
            if cyc is not None:
                if "seat_underutilisation" in cyc.drivers:
                    util = float(self.rng.uniform(0.15, 0.33))
                if "usage_decline" in cyc.drivers and day >= cyc.drivers["usage_decline"]:
                    onset = cyc.drivers["usage_decline"]
                    frac = min(1.0, (day - onset).days / max(1, (cyc.renewal_date - onset).days))
                    util *= 1 - cyc.decline_depth * frac
                if cyc.outcome == "downgraded" and day >= cyc.renewal_date:
                    seats = max(1, int(seats * 0.75))
            active = max(0, int(round(seats * util * float(self.rng.normal(1, 0.08)))))
            scale = 2.5 if day < self.product_change else 1.0
            sessions = int(active * sessions_per_user * scale * float(self.rng.normal(1, 0.1)))
            if self.rng.random() > 0.06:  # occasional missing periods (product outage / sync gaps)
                self._emit(acc, "product", "usage.summary", day, {
                    "product_org_id": org, "period_start": (day - timedelta(days=period_days - 1)).isoformat(),
                    "period_end": day.isoformat(), "active_users": active, "sessions": max(0, sessions),
                    "key_actions": max(0, int(sessions * 0.4))}, hour=23)
            day += timedelta(days=period_days)
