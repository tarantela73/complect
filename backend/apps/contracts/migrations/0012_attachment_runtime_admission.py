"""Admit the existing runtime commands through the attachment tenant boundary."""

from django.db import migrations

_TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid"
_POLICIES = (
    ("contract_attachment_runtime_select", "SELECT", "r"),
    ("contract_attachment_runtime_insert", "INSERT", "a"),
    ("contract_attachment_runtime_update", "UPDATE", "w"),
)


def _guard_sql(*, admitted: bool) -> str:
    """Reject catalog drift rather than replacing any pre-existing policy."""
    admission_checks = ""
    if admitted:
        for name, command, code in _POLICIES:
            qual = "NULL" if command == "INSERT" else "canonical_tenant_qual"
            check = "NULL" if command == "SELECT" else "canonical_tenant_qual"
            admission_checks += f"""
            SELECT p.* INTO admission_policy FROM pg_policy p
            WHERE p.polrelid = attachment_table.oid AND p.polname = '{name}';
            IF NOT FOUND OR NOT admission_policy.polpermissive
               OR admission_policy.polcmd IS DISTINCT FROM '{code}'
               OR admission_policy.polroles IS DISTINCT FROM ARRAY[runtime_role.oid]
               OR pg_get_expr(admission_policy.polqual, admission_policy.polrelid)
                    IS DISTINCT FROM {qual}
               OR pg_get_expr(admission_policy.polwithcheck, admission_policy.polrelid)
                    IS DISTINCT FROM {check} THEN
                RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: invalid {name}'
                    USING ERRCODE = 'P0001';
            END IF;
            """
    return f"""
    DO $$
    DECLARE
        runtime_role pg_roles%ROWTYPE;
        attachment_table pg_class%ROWTYPE;
        tenant_policy pg_policy%ROWTYPE;
        admission_policy pg_policy%ROWTYPE;
        schema_owner oid;
        canonical_tenant_qual text := '(tenant_id = (NULLIF(current_setting('
            || '''app.current_tenant_id''::text, true), ''''::text))::uuid)';
    BEGIN
        SELECT * INTO runtime_role FROM pg_roles WHERE rolname = 'complect_app_runtime';
        IF NOT FOUND OR runtime_role.rolcanlogin OR NOT runtime_role.rolinherit
           OR runtime_role.rolsuper OR runtime_role.rolbypassrls
           OR runtime_role.rolcreatedb OR runtime_role.rolcreaterole
           OR runtime_role.rolreplication THEN
            RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: invalid complect_app_runtime role'
                USING ERRCODE = 'P0001';
        END IF;
        SELECT c.* INTO attachment_table FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'contracts_attachments';
        SELECT nspowner INTO schema_owner FROM pg_namespace WHERE nspname = 'public';
        IF attachment_table.oid IS NULL OR NOT attachment_table.relrowsecurity
           OR NOT attachment_table.relforcerowsecurity
           OR attachment_table.relowner = runtime_role.oid
           OR schema_owner = runtime_role.oid THEN
            RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: attachment RLS baseline invalid'
                USING ERRCODE = 'P0001';
        END IF;
        SELECT p.* INTO tenant_policy FROM pg_policy p
        WHERE p.polrelid = attachment_table.oid
          AND p.polname = 'contract_attachment_tenant_policy';
        IF NOT FOUND OR tenant_policy.polpermissive
           OR tenant_policy.polcmd IS DISTINCT FROM '*'
           OR tenant_policy.polroles IS DISTINCT FROM ARRAY[0::oid]
           OR pg_get_expr(tenant_policy.polqual, tenant_policy.polrelid)
                IS DISTINCT FROM canonical_tenant_qual
           OR pg_get_expr(tenant_policy.polwithcheck, tenant_policy.polrelid)
                IS DISTINCT FROM canonical_tenant_qual
           OR (SELECT count(*) FROM pg_policy WHERE polrelid = attachment_table.oid)
                <> {4 if admitted else 1} THEN
            RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: attachment tenant policies changed'
                USING ERRCODE = 'P0001';
        END IF;
        {admission_checks}
    END $$;
    """


_ADMIT_SQL = f"""
CREATE POLICY contract_attachment_runtime_select ON public.contracts_attachments
    AS PERMISSIVE FOR SELECT TO complect_app_runtime USING ({_TENANT});
CREATE POLICY contract_attachment_runtime_insert ON public.contracts_attachments
    AS PERMISSIVE FOR INSERT TO complect_app_runtime WITH CHECK ({_TENANT});
CREATE POLICY contract_attachment_runtime_update ON public.contracts_attachments
    AS PERMISSIVE FOR UPDATE TO complect_app_runtime
    USING ({_TENANT}) WITH CHECK ({_TENANT});
"""


class Migration(migrations.Migration):
    dependencies = [("contracts", "0011_legacy_document_links")]

    # runtime_role_grants already grants SELECT/INSERT/UPDATE. AttachmentRepository
    # reads, attaches and CAS soft-detaches; DELETE has no admission or new grant.
    operations = [
        migrations.RunSQL(
            sql=_guard_sql(admitted=False) + _ADMIT_SQL + _guard_sql(admitted=True),
            reverse_sql=(
                _guard_sql(admitted=True)
                + "\n".join(f"DROP POLICY {name} ON public.contracts_attachments;" for name, _, _ in _POLICIES)
                + _guard_sql(admitted=False)
            ),
        ),
    ]
