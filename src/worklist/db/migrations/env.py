from alembic import context
from sqlalchemy import create_engine

from worklist.config import get_settings

engine = create_engine(get_settings().database_url_owner)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
