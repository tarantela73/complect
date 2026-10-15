"""Refresh the explicit application grants, as the migrator only."""

from __future__ import annotations

import os

from django.apps import apps
from django.db import connection, transaction

RUNTIME_GROUP = os.getenv("POSTGRES_APP_RUNTIME_ROLE", "complect_app_runtime")
RUNTIME_LOGIN = os.getenv("POSTGRES_APP_RUNTIME_LOGIN", "complect_app_local")
MIGRATOR = os.getenv("POSTGRES_USER", "complect_migrator")
EXCLUDED = {"spatial_ref_sys"}


def _ident(value: str) -> str:
    return connection.ops.quote_name(value)


def _tables() -> list[str]:
    """Return current migrated tables, including models absent from live registry."""
    from django.db.migrations.executor import MigrationExecutor

    names: set[str] = set()
    model_sources = [apps, MigrationExecutor(connection).loader.project_state().apps]
    for model_source in model_sources:
        for model in model_source.get_models(include_auto_created=True):
            table = model._meta.db_table
            if model._meta.managed and table not in EXCLUDED and "." not in table:
                names.add(table)
    existing = set(connection.introspection.table_names())
    names.intersection_update(existing)
    if "django_migrations" in existing:
        names.add("django_migrations")
    return sorted(names)


def _assert_principal_contract(cursor) -> None:
    cursor.execute("SELECT current_user, session_user")
    identity = cursor.fetchone()
    if not identity or identity != (MIGRATOR, MIGRATOR):
        raise RuntimeError(
            "runtime grant refresh requires current_user=session_user="
            f"{MIGRATOR!r}; got {identity!r}"
        )
    cursor.execute(
        "SELECT has_schema_privilege(%s, 'public', 'USAGE'), "
        "has_database_privilege(%s, current_database(), 'CONNECT')",
        [RUNTIME_LOGIN, RUNTIME_LOGIN],
    )
    row = cursor.fetchone()
    if not row or not row[0] or not row[1]:
        raise RuntimeError("runtime login lacks application schema/database access")

    cursor.execute(
        """
        SELECT g.rolcanlogin, g.rolinherit, g.rolsuper, g.rolbypassrls,
               g.rolcreatedb, g.rolcreaterole, g.rolreplication,
               l.rolcanlogin, l.rolinherit, l.rolsuper, l.rolbypassrls,
               l.rolcreatedb, l.rolcreaterole, l.rolreplication,
               pg_has_role(%s, %s, 'member')
        FROM pg_roles g CROSS JOIN pg_roles l
        WHERE g.rolname = %s AND l.rolname = %s
        """,
        [RUNTIME_LOGIN, RUNTIME_GROUP, RUNTIME_GROUP, RUNTIME_LOGIN],
    )
    row = cursor.fetchone()
    if not row:
        raise RuntimeError(
            "runtime group/login role is missing: "
            f"group={RUNTIME_GROUP!r}, login={RUNTIME_LOGIN!r}"
        )
    unsafe_group = row[0] or not row[1] or any(row[2:7])
    unsafe_login = not row[7] or not row[8] or any(row[9:14])
    if unsafe_group or unsafe_login or not row[14]:
        raise RuntimeError(
            "runtime group/login or membership contract is unsafe: "
            f"group={RUNTIME_GROUP!r}, login={RUNTIME_LOGIN!r}"
        )

    # The login must reach exactly its task-owned group. This rejects
    # pg_read_all_data/write_all_data and any other transitive privilege role.
    cursor.execute(
        """
        WITH RECURSIVE reachable(role_oid) AS (
            SELECT oid FROM pg_roles WHERE rolname = %s
            UNION
            SELECT m.roleid FROM pg_auth_members m
            JOIN reachable r ON m.member = r.role_oid
        )
        SELECT COALESCE(
            array_agg(r.rolname ORDER BY r.rolname)
                FILTER (WHERE r.rolname NOT IN (%s, %s)), ARRAY[]::name[]
        ),
        bool_or(r.rolsuper OR r.rolbypassrls OR r.rolcreatedb
                OR r.rolcreaterole OR r.rolreplication)
        FROM reachable x JOIN pg_roles r ON r.oid = x.role_oid
        """,
        [RUNTIME_LOGIN, RUNTIME_LOGIN, RUNTIME_GROUP],
    )
    extra_roles, privileged = cursor.fetchone()
    if extra_roles:
        raise RuntimeError(f"runtime login reaches unexpected roles: {extra_roles}")
    if privileged:
        raise RuntimeError("runtime login reaches a privileged role")

    # Ownership bypasses the grant contract and RLS checks.
    cursor.execute(
        """
        WITH RECURSIVE reachable(role_oid) AS (
            SELECT oid FROM pg_roles WHERE rolname = %s
            UNION
            SELECT m.roleid FROM pg_auth_members m
            JOIN reachable r ON m.member = r.role_oid
        )
        SELECT EXISTS (
            SELECT 1 FROM pg_database d JOIN reachable r ON r.role_oid = d.datdba
            WHERE d.datname = current_database()
        ) OR EXISTS (
            SELECT 1 FROM pg_namespace n JOIN reachable r ON r.role_oid = n.nspowner
            WHERE n.nspname = 'public'
        ) OR EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN reachable r ON r.role_oid = c.relowner
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S')
        )
        """,
        [RUNTIME_LOGIN],
    )
    if cursor.fetchone()[0]:
        raise RuntimeError("runtime login/group reaches an application owner role")

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(
                COALESCE(c.relacl, acldefault('r', c.relowner))
            ) a
            JOIN pg_roles grantee ON grantee.oid = a.grantee
            WHERE n.nspname = 'public' AND grantee.rolname = %s
        ) OR EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            CROSS JOIN LATERAL aclexplode(a.attacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE n.nspname = 'public'
              AND a.attnum > 0 AND NOT a.attisdropped
              AND grantee.rolname = %s
        )
        """,
        [RUNTIME_LOGIN, RUNTIME_LOGIN],
    )
    if cursor.fetchone()[0]:
        raise RuntimeError("runtime login table or column privileges outside its group")


def refresh_runtime_grants() -> list[str]:
    """Reconcile the task-owned runtime group to the explicit table contract."""
    names = _tables()
    with transaction.atomic(using="default"), connection.cursor() as cursor:
        _assert_principal_contract(cursor)
        cursor.execute(
            """
            SELECT c.relname, a.attname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid
            CROSS JOIN LATERAL aclexplode(a.attacl) acl
            JOIN pg_roles grantee ON grantee.oid = acl.grantee
            WHERE n.nspname = 'public'
              AND a.attnum > 0 AND NOT a.attisdropped
              AND grantee.rolname = %s
            """,
            [RUNTIME_GROUP],
        )
        for table, column in cursor.fetchall():
            cursor.execute(
                f"REVOKE ALL ({_ident(column)}) ON TABLE public.{_ident(table)} "
                f"FROM {_ident(RUNTIME_GROUP)}"
            )
        sequence_tables = [
            table
            for table in names
            if table != "django_migrations"
            and table != "common_audit_integrity_logs"
        ]
        cursor.execute(
            f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
            f"FROM {_ident(RUNTIME_GROUP)}"
        )
        cursor.execute(
            f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
            f"FROM {_ident(RUNTIME_GROUP)}"
        )
        for table in names:
            qualified = f"public.{_ident(table)}"
            if table == "django_migrations":
                privilege = "SELECT"
            elif table in {"common_audit_chain", "users_identity_security_audit"}:
                privilege = "SELECT, INSERT"
            elif table == "common_audit_integrity_logs":
                privilege = "SELECT"
            else:
                privilege = "SELECT, INSERT, UPDATE"
            cursor.execute(
                f"GRANT {privilege} ON TABLE {qualified} TO {_ident(RUNTIME_GROUP)}"
            )
            if table == "users_identity_security_audit":
                cursor.execute(
                    f"GRANT UPDATE (is_legal_hold) ON TABLE {qualified} "
                    f"TO {_ident(RUNTIME_GROUP)}"
                )

        cursor.execute(
            """
            SELECT CASE WHEN a.attnum > 0 AND NOT a.attisdropped THEN
                pg_get_serial_sequence(
                    quote_ident(n.nspname) || '.' || quote_ident(c.relname), a.attname
                )
            END
            FROM pg_class c
            JOIN pg_attribute a ON a.attrelid = c.oid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = ANY(%s)
              AND a.attnum > 0 AND NOT a.attisdropped
              AND (
                  a.attidentity IN ('a', 'd')
                  OR CASE WHEN a.attnum > 0 AND NOT a.attisdropped THEN
                      pg_get_serial_sequence(
                          quote_ident(n.nspname) || '.' || quote_ident(c.relname),
                          a.attname
                      )
                  END IS NOT NULL
              )
            """,
            [sequence_tables],
        )
        sequences = [row[0] for row in cursor.fetchall() if row[0]]
        for sequence in sequences:
            cursor.execute(
                f"GRANT USAGE ON SEQUENCE {sequence} TO {_ident(RUNTIME_GROUP)}"
            )
    return names


if __name__ == "__main__":
    import django

    django.setup()
    print("runtime grants refreshed: " + ", ".join(refresh_runtime_grants()))
