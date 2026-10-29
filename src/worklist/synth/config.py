"""Frozen generator parameters (FR-029a).

Fixed and committed before any model evaluation. Do not tune these against held-out results;
any later change must be documented in the README with its reason, and all reports regenerated.
"""

from dataclasses import dataclass, field
from datetime import date

FROZEN_AT = "2026-10-28"


@dataclass(frozen=True)
class Driver:
    name: str
    definition: str
    base_logit: float  # activation log-odds per renewal cycle at average fragility
    fragility_weight: float  # how strongly latent fragility raises activation
    effect: float  # log-odds effect on churn when active
    signals: tuple[str, ...]  # signals expected to carry this driver (for driver recovery)


DRIVERS: tuple[Driver, ...] = (
    Driver("usage_decline", "Active users fall 35–65% over the ~60–120 days before renewal",
           -2.6, 0.9, 1.7, ("usage_trend_60d", "sessions_trend_60d", "active_user_ratio")),
    Driver("seat_underutilisation", "Under 35% of purchased seats active for the whole cycle",
           -2.5, 0.6, 1.1, ("active_user_ratio", "seat_utilisation")),
    Driver("ticket_spike_slow_resolution", "3–6 high/urgent tickets in a month, resolved in 5–15 days",
           -2.8, 0.8, 1.4, ("high_urgent_tickets_30d", "high_urgent_tickets_90d", "tickets_30d",
                            "median_resolution_days_90d", "ticket_trend")),
    Driver("payment_failures", "One or more failed invoice payment attempts, with retries",
           -3.2, 0.5, 1.5, ("payment_failures_90d", "max_payment_attempt_90d")),
    Driver("champion_departure", "Champion contact removed in CRM; mail to them bounces",
           -3.3, 0.4, 1.6, ("champion_removed_90d", "bounces_90d", "net_contacts_change_90d")),
    Driver("no_csm_contact", "No CSM email to the customer for 120+ days before renewal",
           -2.2, 0.5, 0.9, ("days_since_csm_email", "csm_emails_90d")),
)

SILENT_DRIVERS = ("usage_decline", "ticket_spike_slow_resolution", "bad_csat")


@dataclass(frozen=True)
class TenantSpec:
    name: str
    accounts: int


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    tenants: tuple[TenantSpec, ...] = (
        TenantSpec("Northwind Analytics", 2600),
        TenantSpec("Bluefin Health", 1400),
        TenantSpec("Copperleaf Logistics", 800),
        TenantSpec("Driftwood Studio", 200),
    )
    history_end: date = date(2026, 11, 25)  # last simulated day (end of the pending feed)
    history_days: int = 1095
    feed_days: int = 30  # sim clock starts at history_end - feed_days
    target_events: int = 200_000
    monthly_share: float = 0.15
    segments: tuple[tuple[str, float, int, int], ...] = (  # name, share, min seats, max seats
        ("smb", 0.60, 5, 30), ("mid_market", 0.30, 30, 150), ("enterprise", 0.10, 150, 800))
    price_per_seat: tuple[float, float] = (300.0, 650.0)
    unassigned_share: float = 0.03
    owner_change_share: float = 0.06
    product_change_months_ago: int = 18  # usage metrics redefined (sessions/key actions scale ×2.5 before)
    # Outcome model (log-odds)
    base_logit: float = -3.55
    fragility_effect: float = 0.5
    downgrade_share_of_renewals: float = 0.08
    silent_archetype_rate: float = 0.028  # per cycle; tuned so ~15% of churners are silent
    silent_effect: float = 3.2
    # Baseline activity
    tickets_per_year: dict = field(default_factory=lambda: {"smb": 0.6, "mid_market": 1.0, "enterprise": 1.8})
    csm_emails_per_year: float = 1.8
    customer_emails_per_year: float = 0.8
    sparse_share: float = 0.15  # low-engagement accounts with very few records
    # Defects
    duplicate_rate: float = 0.025
    missing_field_rate: float = 0.04
    conflict_share: float = 0.05
    late_share: float = 0.06  # events arriving 1–10 days late
    unmatched_rate: float = 0.012
    ambiguous_domain_share: float = 0.004
    clock_skew_rate: float = 0.0008
    free_mail_domains: tuple[str, ...] = ("gmail.com", "outlook.com", "yahoo.com")


DEFAULT = GeneratorConfig()
