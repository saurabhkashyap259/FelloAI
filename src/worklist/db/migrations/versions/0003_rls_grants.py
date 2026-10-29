"""Forced Row-Level Security on every tenant table, least-privilege grants, and the few
SECURITY DEFINER functions the web role needs before a tenant context exists.

Policy: rows are visible only when `tenant_id = current_setting('app.tenant_id', true)::uuid`.
With no setting the expression is NULL and nothing matches (default deny). The owner role is
exempted so that SECURITY DEFINER functions (which run as the owner) and operator tooling work;
the web and job roles cannot assume it.
"""

from alembic import op

from worklist.db.tables import APPEND_ONLY_TABLES, TENANT_TABLES

revision = "0003"
down_revision = "0002"

TENANT_MATCH = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"

# Writes the web role may perform (everything else is read-only for it).
WEB_WRITES = {
    "csm_action": "INSERT",
    "alert": "UPDATE",
    "outreach_draft": "UPDATE",
}


def upgrade() -> None:
    owner = op.get_bind().exec_driver_sql("SELECT current_user").scalar()

    for table in TENANT_TABLES + ["sim.pending_feed_event"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_MATCH} OR current_user = '{owner}') "
            f"WITH CHECK ({TENANT_MATCH} OR current_user = '{owner}')"
        )

    # model_version: global rows (tenant_id null) are shared; tenant rows are isolated.
    op.execute("ALTER TABLE model_version ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE model_version FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON model_version "
        f"USING (tenant_id IS NULL OR {TENANT_MATCH} OR current_user = '{owner}') "
        f"WITH CHECK (tenant_id IS NULL OR {TENANT_MATCH} OR current_user = '{owner}')"
    )

    # --- job role: reads and writes tenant data; append-only tables get no UPDATE/DELETE.
    for table in TENANT_TABLES:
        privileges = "SELECT, INSERT" if table in APPEND_ONLY_TABLES else "SELECT, INSERT, UPDATE"
        op.execute(f"GRANT {privileges} ON {table} TO worklist_job")
    op.execute("GRANT SELECT, INSERT, UPDATE ON model_version TO worklist_job")
    op.execute("GRANT SELECT ON tenant TO worklist_job")
    op.execute("GRANT SELECT, INSERT, UPDATE ON sim_clock TO worklist_job")
    op.execute("GRANT SELECT, INSERT, UPDATE ON sim.pending_feed_event TO worklist_job")

    # --- web role: read tenant data, a few narrow writes, sessions, audit insert only.
    for table in TENANT_TABLES:
        op.execute(f"GRANT SELECT ON {table} TO worklist_web")
    for table, privilege in WEB_WRITES.items():
        op.execute(f"GRANT {privilege} ON {table} TO worklist_web")
    op.execute("GRANT SELECT (id, scope, tenant_id, algorithm, history_window, trained_at, windows, "
               "signal_defs_version, metrics, status, decision_reason, decided_at) "
               "ON model_version TO worklist_web")
    op.execute("GRANT SELECT ON tenant TO worklist_web")
    op.execute("GRANT SELECT ON sim_clock TO worklist_web")
    op.execute("GRANT SELECT, INSERT, DELETE ON session TO worklist_web")
    op.execute("GRANT INSERT ON access_audit TO worklist_web")
    # No grant on sim.pending_feed_event or SELECT on access_audit for the web role.

    # --- functions the web role calls without a tenant context.
    op.execute("""
        CREATE FUNCTION auth_lookup(p_email text)
        RETURNS TABLE (user_id uuid, tenant_id uuid, role text, password_hash text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT id, tenant_id, role, password_hash FROM app_user
          WHERE lower(email) = lower(p_email) AND active
        $$""")
    op.execute("""
        CREATE FUNCTION demo_accounts()
        RETURNS TABLE (tenant_name text, display_name text, email text, role text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT t.name, u.display_name, u.email, u.role
          FROM app_user u JOIN tenant t ON t.id = u.tenant_id
          WHERE u.is_demo AND u.active
          ORDER BY t.name, u.role DESC, u.display_name
        $$""")
    op.execute("""
        CREATE FUNCTION pipeline_stale_tenant_count()
        RETURNS integer
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
          SELECT count(*)::int FROM tenant t
          WHERE NOT EXISTS (
            SELECT 1 FROM pipeline_run r, sim_clock c
            WHERE r.tenant_id = t.id AND r.run_type = 'nightly' AND r.status = 'succeeded'
              AND r.cutoff_at >= (c."current_date"::timestamptz - interval '26 hours'))
        $$""")
    for fn in ("auth_lookup(text)", "demo_accounts()", "pipeline_stale_tenant_count()"):
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO worklist_web")
