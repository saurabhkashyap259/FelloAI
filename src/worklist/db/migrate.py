"""Programmatic Alembic upgrade (used by `worklist db upgrade` and tests). Works from an installed
package, so it does not depend on alembic.ini being present."""

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade(revision: str = "head") -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    command.upgrade(cfg, revision)
