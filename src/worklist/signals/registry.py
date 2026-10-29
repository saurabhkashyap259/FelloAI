"""Signal definitions: name, sources, window, kind, direction and plain-language reason text.

Shared by training, scoring and the drill-down view. `SIGNAL_DEFS_VERSION` is a hash of the
registry; a model can only score snapshots built with the same version (FR-002).
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Signal:
    name: str
    source: str  # crm | email | support | product | billing | account
    kind: str  # count | trend | ratio | recency | absence | flag | attribute
    window_days: int
    label: str  # short plain-language definition
    reason_up: str  # text when the signal pushes risk up; {v} = formatted value
    reason_down: str = ""
    event_types: tuple[str, ...] = ()
    where: dict = field(default_factory=dict)  # payload filters used by the drill-down
    unit: str = ""
    monotone: int = 0  # LightGBM monotone constraint: +1 risk rises with value, -1 falls, 0 free


SIGNALS: tuple[Signal, ...] = (
    # --- product ---
    Signal("active_user_ratio", "product", "ratio", 120, "Active users as a share of purchased seats (latest rollup)",
           "Only {v} of purchased seats are active", "{v} of purchased seats are active",
           ("usage.summary", "seats.changed"), unit="pct"),
    Signal("usage_trend_60d", "product", "trend", 130,
           "Change in active users: latest rollup vs. the average 40–130 days earlier",
           "Active users changed {v} over the last ~2–4 months", "Active users changed {v} over the last ~2–4 months",
           ("usage.summary",), unit="pct_change"),
    Signal("sessions_trend_60d", "product", "trend", 130,
           "Change in sessions: latest rollup vs. the average 40–130 days earlier",
           "Sessions changed {v} over the last ~2–4 months", "Sessions changed {v} over the last ~2–4 months",
           ("usage.summary",), unit="pct_change"),
    Signal("sessions_per_user", "product", "ratio", 120, "Sessions per active user (latest rollup)",
           "Low engagement: {v} sessions per active user", "{v} sessions per active user", ("usage.summary",)),
    Signal("days_since_usage", "product", "recency", 365, "Days since the latest product usage rollup",
           "No product usage reported for {v} days", "Usage reported {v} days ago", ("usage.summary",), unit="days"),
    Signal("seat_utilisation", "product", "ratio", 365, "Assigned seats as a share of purchased seats",
           "Only {v} of purchased seats are assigned", "{v} of seats assigned", ("seats.changed",), unit="pct"),
    # --- support ---
    Signal("tickets_30d", "support", "count", 30, "Support tickets opened in the last 30 days",
           "{v} support tickets opened in the last 30 days", "{v} support tickets in the last 30 days",
           ("ticket.created",)),
    Signal("tickets_90d", "support", "count", 90, "Support tickets opened in the last 90 days",
           "{v} support tickets opened in the last 90 days", "{v} support tickets in the last 90 days",
           ("ticket.created",)),
    Signal("high_urgent_tickets_30d", "support", "count", 30, "High or urgent priority tickets in the last 30 days",
           "{v} high/urgent tickets in the last 30 days", "{v} high/urgent tickets in the last 30 days",
           ("ticket.created",), {"priority": ["high", "urgent"]}, monotone=1),
    Signal("high_urgent_tickets_90d", "support", "count", 90, "High or urgent priority tickets in the last 90 days",
           "{v} high/urgent tickets in the last 90 days", "{v} high/urgent tickets in the last 90 days",
           ("ticket.created",), {"priority": ["high", "urgent"]}, monotone=1),
    Signal("urgent_tickets_30d", "support", "count", 30, "Urgent tickets in the last 30 days",
           "{v} urgent tickets in the last 30 days", "{v} urgent tickets in the last 30 days",
           ("ticket.created",), {"priority": ["urgent"]}),
    Signal("ticket_trend", "support", "trend", 120, "Tickets in the last 30 days vs. the monthly rate in the 90 days before",
           "Ticket volume up sharply ({v}× the usual monthly rate)", "Ticket volume {v}× the usual monthly rate",
           ("ticket.created",)),
    Signal("median_resolution_days_90d", "support", "ratio", 90, "Median days to resolve tickets opened in the last 90 days",
           "Tickets take {v} days to resolve (median, last 90 days)", "Tickets resolved in {v} days (median)",
           ("ticket.created", "ticket.solved"), unit="days"),
    Signal("open_tickets", "support", "count", 180, "Tickets opened in the last 180 days and not yet solved",
           "{v} tickets still open", "{v} tickets open", ("ticket.created",)),
    Signal("bad_csat_90d", "support", "count", 90, "Tickets solved with a 'bad' satisfaction rating in the last 90 days",
           "{v} tickets rated 'bad' by the customer in the last 90 days", "{v} 'bad' ratings in 90 days",
           ("ticket.solved",), {"satisfaction": ["bad"]}),
    # --- billing ---
    Signal("payment_failures_90d", "billing", "count", 90, "Failed invoice payment attempts in the last 90 days",
           "{v} failed payment attempts in the last 90 days", "{v} failed payments in 90 days",
           ("invoice.payment_failed",), monotone=1),
    Signal("max_payment_attempt_90d", "billing", "count", 90, "Highest retry number on a failed payment (last 90 days)",
           "A payment failed on retry #{v}", "Highest failed retry #{v}", ("invoice.payment_failed",), monotone=1),
    Signal("cancel_at_period_end", "billing", "flag", 365, "Subscription is set to cancel at period end",
           "Subscription is set to cancel at the end of the period", "Subscription not set to cancel",
           ("subscription.updated",), monotone=1),
    Signal("days_since_invoice_paid", "billing", "recency", 400, "Days since the last paid invoice",
           "No paid invoice for {v} days", "Last invoice paid {v} days ago", ("invoice.paid",), unit="days"),
    # --- crm ---
    Signal("champion_removed_90d", "crm", "count", 90, "Champion contacts removed in CRM in the last 90 days",
           "Champion contact removed in the last 90 days", "No champion change", ("contact.changed",),
           {"change": ["removed"]}, monotone=1),
    Signal("net_contacts_change_90d", "crm", "count", 90, "Contacts added minus removed in the last 90 days",
           "Net contact change {v} in the last 90 days", "Net contact change {v}", ("contact.changed",)),
    Signal("opp_at_risk_90d", "crm", "count", 90, "Renewal opportunity marked at risk in the last 90 days",
           "Renewal opportunity marked at risk in CRM", "No at-risk opportunity", ("opportunity.updated",),
           {"stage": ["renewal_at_risk"]}, monotone=1),
    # --- email ---
    Signal("days_since_csm_email", "email", "absence", 365, "Days since the last CSM email to the customer",
           "No CSM contact in {v} days", "Last CSM contact {v} days ago", ("email.sent",), unit="days"),
    Signal("csm_emails_90d", "email", "absence", 90, "CSM emails to the customer in the last 90 days",
           "No CSM emails in the last 90 days", "{v} CSM emails in the last 90 days", ("email.sent",)),
    Signal("customer_emails_90d", "email", "count", 90, "Emails from the customer in the last 90 days",
           "{v} emails from the customer in the last 90 days", "{v} customer emails in 90 days", ("email.received",)),
    Signal("bounces_90d", "email", "count", 90, "Emails to the customer that bounced in the last 90 days",
           "{v} emails to the customer bounced in the last 90 days", "{v} bounces", ("email.bounced",), monotone=1),
    # --- account ---
    Signal("days_to_renewal", "account", "attribute", 0, "Days until the next renewal date",
           "Renewal in {v} days", "Renewal in {v} days", unit="days"),
    Signal("tenure_days", "account", "attribute", 0, "Days since the contract started",
           "Customer for {v} days", "Customer for {v} days", unit="days"),
    Signal("log_arr", "account", "attribute", 0, "Contract value (log scale)",
           "Contract value", "Contract value"),
    Signal("is_monthly", "account", "attribute", 0, "Monthly plan rather than a term contract",
           "Monthly plan (no long-term commitment)", "Term contract"),
)

BY_NAME = {s.name: s for s in SIGNALS}
FEATURES = [s.name for s in SIGNALS]
SOURCES = ("crm", "email", "support", "product", "billing")

SIGNAL_DEFS_VERSION = "sd-" + hashlib.sha256(
    json.dumps([asdict(s) for s in SIGNALS], sort_keys=True).encode()).hexdigest()[:12]


def format_value(signal: Signal, value: float | None) -> str:
    if value is None:
        return "unknown"
    if signal.unit == "pct":
        return f"{value * 100:.0f}%"
    if signal.unit == "pct_change":
        return f"{value * 100:+.0f}%"
    if signal.name == "ticket_trend":
        return f"{value:.1f}"
    if signal.name == "net_contacts_change_90d":
        return f"{value:+.0f}"
    if float(value).is_integer():
        return f"{value:.0f}"
    return f"{value:.1f}"


def reason_text(signal: Signal, value: float | None, contribution: float) -> str:
    template = signal.reason_up if contribution > 0 else (signal.reason_down or signal.reason_up)
    return template.format(v=format_value(signal, value))
