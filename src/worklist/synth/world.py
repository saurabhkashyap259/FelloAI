"""The latent world: accounts, owners, renewal cycles. Drivers and outcomes are in drivers.py.

The world is simulated first; the five sources are then observations of it (emit.py), with defects
injected afterwards (defects.py). Everything derives from one seeded numpy Generator.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from worklist.synth.config import GeneratorConfig

NAME_A = ["Acme", "Globex", "Initech", "Umbrella", "Stark", "Wayne", "Hooli", "Vandelay", "Soylent", "Tyrell",
          "Cyberdyne", "Wonka", "Gringotts", "Monarch", "Oscorp", "Pied Piper", "Aperture", "Black Mesa", "Nakatomi",
          "Duff", "Prestige", "Massive", "Dunder", "Sterling", "Bluth", "Wernham", "Gekko", "Kramerica", "Sirius",
          "Zorg", "Virtucon", "Abstergo", "Ollivander", "Lunar", "Polaris", "Quantum", "Ironclad", "Evergreen",
          "Summit", "Harbor"]
NAME_B = ["Labs", "Systems", "Group", "Health", "Logistics", "Foods", "Media", "Partners", "Retail", "Energy",
          "Capital", "Works", "Robotics", "Studios", "Clinics", "Freight", "Supply", "Insurance", "Motors", "Bank"]


@dataclass
class Cycle:
    renewal_date: date
    start: date
    drivers: dict[str, date] = field(default_factory=dict)  # driver name -> onset date
    silent: bool = False
    bad_csat: bool = False
    outcome: str = "renewed"  # renewed | downgraded | churned | pending (after history end)
    arr_before: float = 0.0
    arr_after: float = 0.0
    fragility: float = 0.0
    cancel_notice: date | None = None  # subscription set to cancel at period end
    opp_at_risk: date | None = None
    decline_depth: float = 0.0


@dataclass
class Account:
    idx: int
    tenant_idx: int
    name: str
    domain: str
    segment: str
    seats: int
    arr: float
    contract_type: str
    contract_start: date
    active_from: date  # first day with observations (max of contract start and history start)
    fragility: float
    sparse: bool
    owner_idx: int | None
    owner_change: tuple[date, int | None] | None
    util: float
    ids: dict[str, str]
    cycles: list[Cycle] = field(default_factory=list)
    churn_date: date | None = None  # renewal date of the churned cycle

    @property
    def active_until(self) -> date:
        return self.churn_date or date(9000, 1, 1)


def history_start(cfg: GeneratorConfig) -> date:
    return cfg.history_end - timedelta(days=cfg.history_days)


def build_accounts(cfg: GeneratorConfig, rng: np.random.Generator, csm_counts: list[int]) -> list[Account]:
    start = history_start(cfg)
    accounts: list[Account] = []
    used_domains: set[str] = set()
    idx = 0
    for t_idx, spec in enumerate(cfg.tenants):
        n_csm = csm_counts[t_idx]
        for i in range(spec.accounts):
            seg_names = [s[0] for s in cfg.segments]
            seg_i = rng.choice(len(cfg.segments), p=[s[1] for s in cfg.segments])
            segment, _, smin, smax = cfg.segments[seg_i]
            seats = int(rng.integers(smin, smax + 1))
            arr = round(seats * rng.uniform(*cfg.price_per_seat), -2)
            monthly = rng.random() < cfg.monthly_share
            # Contract start: up to 2 years before history, until 20 days before its end.
            span = (cfg.history_end - timedelta(days=20) - (start - timedelta(days=730))).days
            contract_start = start - timedelta(days=730) + timedelta(days=int(rng.integers(0, span)))
            base = f"{NAME_A[rng.integers(len(NAME_A))]} {NAME_B[rng.integers(len(NAME_B))]}"
            name = f"{base} {i + 1}"
            domain = base.lower().replace(" ", "") + f"{i + 1}-{t_idx}.com"
            used_domains.add(domain)
            owner = None if rng.random() < cfg.unassigned_share else int(i % n_csm)
            change = None
            if owner is not None and rng.random() < cfg.owner_change_share:
                lo = max(contract_start, start)
                days = (cfg.history_end - lo).days
                if days > 60:
                    change = (lo + timedelta(days=int(rng.integers(30, days))), int(rng.integers(n_csm)))
            prefix = f"{t_idx}{idx:05d}"
            accounts.append(Account(
                idx=idx, tenant_idx=t_idx, name=name, domain=domain, segment=segment if segment in seg_names
                else "smb", seats=seats, arr=arr if not monthly else round(arr * 1.15, -2),
                contract_type="monthly" if monthly else "term", contract_start=contract_start,
                active_from=max(contract_start, start), fragility=float(rng.normal()),
                sparse=rng.random() < cfg.sparse_share, owner_idx=owner, owner_change=change,
                util=float(np.clip(rng.beta(6, 3), 0.4, 0.95)),
                ids={"crm": f"001{prefix}", "billing": f"cus_{prefix}", "product": f"org_{prefix}",
                     "support": f"zd_{prefix}"},
            ))
            idx += 1
    # A few account pairs share a domain (subsidiaries), so domain matching is ambiguous for them.
    n_shared = int(len(accounts) * cfg.ambiguous_domain_share)
    for a, b in rng.choice(len(accounts), size=(n_shared, 2), replace=False):
        if accounts[a].tenant_idx == accounts[b].tenant_idx:
            accounts[b].domain = accounts[a].domain
    return accounts


def renewal_dates(acc: Account, until: date) -> list[tuple[date, date]]:
    """(cycle start, renewal date) pairs. Term: yearly. Monthly: evaluated as quarterly cycles."""
    out = []
    step = 365 if acc.contract_type == "term" else 91
    start = acc.contract_start
    while True:
        renewal = start + timedelta(days=step)
        out.append((start, renewal))
        if renewal > until:
            return out
        start = renewal
