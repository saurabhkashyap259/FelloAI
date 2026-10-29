"""classify_missing_resource(kind, id): tells the audit log whether a resource the caller could not
see exists in another tenant. Returns only 'other_tenant' or 'missing', never data.
"""

from alembic import op

revision = "0004"
down_revision = "0003"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION classify_missing_resource(p_kind text, p_id uuid)
        RETURNS text
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE found_tenant uuid;
        BEGIN
          IF p_kind = 'account' THEN SELECT tenant_id INTO found_tenant FROM account WHERE id = p_id;
          ELSIF p_kind = 'alert' THEN SELECT tenant_id INTO found_tenant FROM alert WHERE id = p_id;
          ELSIF p_kind = 'draft' THEN SELECT tenant_id INTO found_tenant FROM outreach_draft WHERE id = p_id;
          ELSIF p_kind = 'score' THEN SELECT tenant_id INTO found_tenant FROM account_score WHERE id = p_id;
          END IF;
          IF found_tenant IS NOT NULL
             AND found_tenant IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN
            RETURN 'other_tenant';
          END IF;
          RETURN 'missing';
        END $$""")
    op.execute("REVOKE ALL ON FUNCTION classify_missing_resource(text, uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION classify_missing_resource(text, uuid) TO worklist_web")
