"""`worklist seed-demo`: tenants, settings and demo users (FR-027b). Idempotent."""

from argon2 import PasswordHasher
from sqlalchemy import select

from worklist.config import get_settings
from worklist.db import tables as t
from worklist.db.engine import operator_conn
from worklist.log import log

# (name, accounts, CSMs). ~120 accounts per CSM; sizes match synth/config.py.
DEMO_TENANTS = [
    ("Northwind Analytics", 2600, 22),
    ("Bluefin Health", 1400, 12),
    ("Copperleaf Logistics", 800, 7),
    ("Driftwood Studio", 200, 2),
]

FIRST_NAMES = ["Priya", "Marcus", "Elena", "Tomás", "Aisha", "Jonas", "Mei", "Kwame", "Sofia", "Ravi",
               "Hannah", "Diego", "Yuki", "Omar", "Lena", "Arjun", "Chloe", "Ibrahim", "Nora", "Felix",
               "Zara", "Mateo"]

DEFAULT_TIMEZONES = {"crm": "UTC", "email": "UTC", "support": "UTC", "product": "America/New_York",
                     "billing": "UTC"}


def tenant_slug(name: str) -> str:
    return name.split()[0].lower()


def seed_demo(tenants: int = 4) -> int:
    settings = get_settings()
    if not settings.demo_mode:
        log("seed_demo.refused", reason="DEMO_MODE is not enabled")
        return 1
    hasher = PasswordHasher()
    password_hash = hasher.hash(settings.demo_password)
    created = 0
    with operator_conn() as conn:
        for name, _accounts, csm_count in DEMO_TENANTS[:tenants]:
            tenant_id = conn.execute(select(t.tenant.c.id).where(t.tenant.c.name == name)).scalar()
            if tenant_id is None:
                tenant_id = t.new_id()
                conn.execute(t.tenant.insert().values(id=tenant_id, name=name))
                conn.execute(t.tenant_settings.insert().values(tenant_id=tenant_id,
                                                               source_timezones=DEFAULT_TIMEZONES))
                created += 1
            slug = tenant_slug(name)
            users = [(f"manager@{slug}.demo", f"{FIRST_NAMES[-1]} (Manager)", "cs_manager")]
            users += [(f"csm{i + 1:02d}@{slug}.demo", FIRST_NAMES[i % len(FIRST_NAMES)], "csm")
                      for i in range(csm_count)]
            existing = set(conn.execute(select(t.app_user.c.email)
                                        .where(t.app_user.c.tenant_id == tenant_id)).scalars())
            for email, display, role in users:
                if email not in existing:
                    conn.execute(t.app_user.insert().values(
                        id=t.new_id(), tenant_id=tenant_id, email=email, display_name=display, role=role,
                        password_hash=password_hash, is_demo=True))
    log("seed_demo.done", tenants_created=created)
    return 0
