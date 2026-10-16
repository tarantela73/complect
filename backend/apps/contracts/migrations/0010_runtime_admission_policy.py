"""Admit the canonical runtime group to the existing Contracts RLS boundary."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0009_contracts_cursor_index"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            DO $$
            DECLARE
                runtime_role pg_roles%ROWTYPE;
                contracts_table pg_class%ROWTYPE;
                tenant_policy pg_policy%ROWTYPE;
                schema_owner oid;
                canonical_tenant_qual text := '(tenant_id = (NULLIF(current_setting('
                    || '''app.current_tenant_id''::text, true), ''''::text))::uuid)';
            BEGIN
                SELECT * INTO runtime_role
                FROM pg_roles
                WHERE rolname = 'complect_app_runtime';
                IF NOT FOUND
                   OR runtime_role.rolcanlogin
                   OR NOT runtime_role.rolinherit
                   OR runtime_role.rolsuper
                   OR runtime_role.rolbypassrls
                   OR runtime_role.rolcreatedb
                   OR runtime_role.rolcreaterole
                   OR runtime_role.rolreplication THEN
                    RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: invalid complect_app_runtime role'
                        USING ERRCODE = 'P0001';
                END IF;

                SELECT c.* INTO contracts_table
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'contracts_contracts';
                SELECT n.nspowner INTO schema_owner FROM pg_namespace n WHERE n.nspname = 'public';
                IF contracts_table.oid IS NULL OR NOT contracts_table.relrowsecurity
                   OR NOT contracts_table.relforcerowsecurity
                   OR contracts_table.relowner = runtime_role.oid
                   OR schema_owner = runtime_role.oid THEN
                    RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: Contracts RLS baseline is invalid'
                        USING ERRCODE = 'P0001';
                END IF;

                SELECT p.* INTO tenant_policy
                FROM pg_policy p
                WHERE p.polrelid = contracts_table.oid
                  AND p.polname = 'contract_tenant_isolation_policy';
                IF NOT FOUND
                   OR tenant_policy.polpermissive
                   OR tenant_policy.polcmd IS DISTINCT FROM '*'
                   OR tenant_policy.polroles IS DISTINCT FROM ARRAY[0::oid]
                   OR pg_get_expr(tenant_policy.polqual, tenant_policy.polrelid)
                      IS DISTINCT FROM canonical_tenant_qual
                   OR pg_get_expr(tenant_policy.polwithcheck, tenant_policy.polrelid)
                      IS DISTINCT FROM canonical_tenant_qual THEN
                    RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: tenant isolation policy changed'
                        USING ERRCODE = 'P0001';
                END IF;
            END
            $$;

            CREATE POLICY contract_runtime_admission_policy
            ON public.contracts_contracts
            AS PERMISSIVE
            FOR ALL
            TO complect_app_runtime
            USING (true)
            WITH CHECK (true);
            """,
            reverse_sql="""
            DO $$
            DECLARE
                contracts_table pg_class%ROWTYPE;
                tenant_policy pg_policy%ROWTYPE;
                admission_policy pg_policy%ROWTYPE;
                runtime_role oid;
                schema_owner oid;
                canonical_tenant_qual text := '(tenant_id = (NULLIF(current_setting('
                    || '''app.current_tenant_id''::text, true), ''''::text))::uuid)';
            BEGIN
                SELECT c.* INTO contracts_table
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = 'contracts_contracts';
                SELECT oid INTO runtime_role FROM pg_roles WHERE rolname = 'complect_app_runtime';
                SELECT nspowner INTO schema_owner FROM pg_namespace WHERE nspname = 'public';
                SELECT p.* INTO tenant_policy FROM pg_policy p
                WHERE p.polrelid = contracts_table.oid
                  AND p.polname = 'contract_tenant_isolation_policy';
                SELECT p.* INTO admission_policy FROM pg_policy p
                WHERE p.polrelid = contracts_table.oid
                  AND p.polname = 'contract_runtime_admission_policy';
                IF contracts_table.oid IS NULL OR runtime_role IS NULL THEN
                    RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: Contracts table is missing'
                        USING ERRCODE = 'P0001';
                END IF;
                IF tenant_policy.oid IS NULL OR admission_policy.oid IS NULL
                   OR NOT contracts_table.relrowsecurity
                   OR NOT contracts_table.relforcerowsecurity
                   OR contracts_table.relowner = runtime_role
                   OR schema_owner = runtime_role
                   OR tenant_policy.polpermissive
                   OR tenant_policy.polcmd IS DISTINCT FROM '*'
                   OR tenant_policy.polroles IS DISTINCT FROM ARRAY[0::oid]
                   OR pg_get_expr(tenant_policy.polqual, tenant_policy.polrelid)
                      IS DISTINCT FROM canonical_tenant_qual
                   OR pg_get_expr(tenant_policy.polwithcheck, tenant_policy.polrelid)
                      IS DISTINCT FROM canonical_tenant_qual
                   OR NOT admission_policy.polpermissive
                   OR admission_policy.polcmd IS DISTINCT FROM '*'
                   OR admission_policy.polroles IS DISTINCT FROM ARRAY[
                       (SELECT oid FROM pg_roles WHERE rolname = 'complect_app_runtime')
                   ]
                   OR pg_get_expr(admission_policy.polqual, admission_policy.polrelid)
                      IS DISTINCT FROM 'true'
                   OR pg_get_expr(admission_policy.polwithcheck, admission_policy.polrelid)
                      IS DISTINCT FROM 'true' THEN
                    RAISE EXCEPTION 'BLOCKED_POLICY_DRIFT: refusing unsafe Contracts reverse'
                        USING ERRCODE = 'P0001';
                END IF;
            END
            $$;
            DROP POLICY contract_runtime_admission_policy
            ON public.contracts_contracts;
            """,
        ),
    ]
