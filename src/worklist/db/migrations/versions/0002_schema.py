"""All tables from data-model.md (created from the SQLAlchemy metadata as of this revision)."""

from alembic import op

from worklist.db.tables import metadata

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    metadata.create_all(op.get_bind())
