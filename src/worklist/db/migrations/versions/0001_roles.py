"""Roles: worklist_web (web app) and worklist_job (scheduled jobs); `sim` schema.

Tables are owned by the migrating (owner) role. The web and job roles are non-owners and
non-superusers, so forced RLS applies to them.
"""

from urllib.parse import urlparse

from alembic import op

from worklist.config import get_settings

revision = "0001"
down_revision = None


def _role_sql(url: str) -> str:
    parsed = urlparse(url.replace("+psycopg", ""))
    user, password = parsed.username, (parsed.password or "").replace("'", "''")
    return f"""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{user}') THEN
        CREATE ROLE {user} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}';
      END IF;
    END $$;
    """


def upgrade() -> None:
    settings = get_settings()
    op.execute(_role_sql(settings.database_url_web))
    op.execute(_role_sql(settings.database_url_job))
    op.execute("CREATE SCHEMA IF NOT EXISTS sim")
    op.execute("REVOKE ALL ON SCHEMA sim FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA sim TO worklist_job")
    op.execute("GRANT USAGE ON SCHEMA public TO worklist_web, worklist_job")
