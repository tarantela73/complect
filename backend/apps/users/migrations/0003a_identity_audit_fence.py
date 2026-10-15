"""Release N fence expansion; independent of future contract/retirement work.

0003 is immutable. Apply with authority workers drained (see ADR-0024).
All objects use invoker privileges and a fixed search path. PUBLIC execution
does not convey table privileges; production runtime must not own these objects.
"""

from django.db import migrations

KEY_SQL = """
CREATE FUNCTION public.users_identity_fence_key(identity_uuid uuid)
RETURNS bigint LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
    SELECT ('x' || pg_catalog.encode(pg_catalog.substring(
        pg_catalog.sha256(pg_catalog.convert_to('iam-identity:', 'UTF8')
                         || pg_catalog.uuid_send(identity_uuid)), 1, 8), 'hex'))::bit(64)::bigint
$$;

CREATE FUNCTION public.users_audit_hold_precondition()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF (NEW.is_legal_hold AND (TG_OP = 'INSERT' OR NOT OLD.is_legal_hold))
       OR (TG_OP = 'INSERT' AND NEW.session_id IS NOT NULL) THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'iam_hold_read_committed_required' USING ERRCODE = '25001';
        END IF;
        IF NOT pg_try_advisory_xact_lock_shared(5278585310052439113) THEN
            RAISE EXCEPTION 'iam_retirement_fence_busy' USING ERRCODE = '55P03';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
-- Replace only the audit table's blocking retirement trigger. Canonical 0003
-- keeps its original function and all other table triggers unchanged.
DROP TRIGGER trg_users_iam_retirement_barrier_3 ON public.users_identity_security_audit;
CREATE TRIGGER trg_users_iam_retirement_barrier_3
BEFORE INSERT OR UPDATE OF is_legal_hold ON public.users_identity_security_audit
FOR EACH ROW EXECUTE FUNCTION public.users_audit_hold_precondition();
"""


def audit_guard_sql(event: str) -> str:
    # Migration-owned SQL only; no runtime implementation import.
    affected = (
        "SELECT n.identity_id, n.session_id FROM new_audit n "
        "WHERE n.is_legal_hold OR n.session_id IS NOT NULL"
        if event == "insert"
        else "SELECT n.identity_id, n.session_id FROM new_audit n "
        "JOIN old_audit o ON o.id = n.id WHERE n.is_legal_hold AND NOT o.is_legal_hold"
    )
    transition = "NEW TABLE AS new_audit" if event == "insert" else ("OLD TABLE AS old_audit NEW TABLE AS new_audit")
    return f"""
CREATE FUNCTION public.users_audit_hold_{event}_guard()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    identity_uuid uuid;
    session_uuid uuid;
    bound_identity uuid;
    evidence record;
    identity_set uuid[];
    session_bindings jsonb;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM ({affected}) a) THEN RETURN NULL; END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'iam_hold_read_committed_required' USING ERRCODE = '25001';
    END IF;
    SELECT array_agg(DISTINCT coalesce(a.identity_id, s.identity_id)
                     ORDER BY coalesce(a.identity_id, s.identity_id)),
           jsonb_object_agg(a.session_id::text, s.identity_id)
               FILTER (WHERE a.session_id IS NOT NULL)
    INTO identity_set, session_bindings FROM ({affected}) a
    LEFT JOIN public.users_identity_sessions s ON s.id = a.session_id;
    FOREACH identity_uuid IN ARRAY identity_set LOOP
        IF identity_uuid IS NULL THEN
            RAISE EXCEPTION 'audit_hold_identity_unresolved' USING ERRCODE = '23514';
        END IF;
        IF NOT pg_try_advisory_xact_lock(public.users_identity_fence_key(identity_uuid)) THEN
            RAISE EXCEPTION 'audit_hold_identity_fence_busy' USING ERRCODE = '55P03';
        END IF;
    END LOOP;
    -- DML already holds audit rows: never block on a session row in reverse order.
    FOR session_uuid IN SELECT DISTINCT a.session_id FROM ({affected}) a
                        WHERE a.session_id IS NOT NULL ORDER BY a.session_id LOOP
        SELECT s.identity_id INTO bound_identity FROM public.users_identity_sessions s
        WHERE s.id = session_uuid FOR KEY SHARE NOWAIT;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'audit_hold_session_missing' USING ERRCODE = '23514';
        END IF;
        IF (session_bindings ->> session_uuid::text)::uuid IS DISTINCT FROM bound_identity THEN
            RAISE EXCEPTION 'audit_hold_binding_changed' USING ERRCODE = '55P03';
        END IF;
        FOR evidence IN SELECT a.identity_id FROM ({affected}) a WHERE a.session_id = session_uuid LOOP
            IF evidence.identity_id IS NOT NULL AND evidence.identity_id <> bound_identity THEN
                RAISE EXCEPTION 'audit_hold_reference_mismatch' USING ERRCODE = '23514';
            END IF;
        END LOOP;
    END LOOP;
    RETURN NULL;
END;
$$;
CREATE TRIGGER trg_users_audit_hold_{event}
AFTER {event.upper()} ON public.users_identity_security_audit
REFERENCING {transition}
FOR EACH STATEMENT EXECUTE FUNCTION public.users_audit_hold_{event}_guard();
"""


SESSION_SQL = """
CREATE FUNCTION public.users_audit_session_binding_guard()
RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE session_uuid uuid; identity_uuid uuid;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.identity_id IS DISTINCT FROM OLD.identity_id THEN
            RAISE EXCEPTION 'identity_session_binding_immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF current_setting('transaction_isolation') <> 'read committed' THEN
        RAISE EXCEPTION 'iam_hold_read_committed_required' USING ERRCODE = '25001';
    END IF;
    IF TG_OP = 'DELETE' THEN
        session_uuid := OLD.id; identity_uuid := OLD.identity_id;
    ELSE
        session_uuid := NEW.id; identity_uuid := NEW.identity_id;
    END IF;
    -- The row lock is already held. Both barriers must be non-blocking.
    IF NOT pg_try_advisory_xact_lock_shared(5278585310052439113)
       OR NOT pg_try_advisory_xact_lock(public.users_identity_fence_key(identity_uuid)) THEN
        RAISE EXCEPTION 'audit_session_fence_busy' USING ERRCODE = '55P03';
    END IF;
    -- Preserve even unheld evidence: a later supported latch must not resolve
    -- an old UUID through a different session. Existing session PK handles duplicates.
    IF EXISTS (SELECT 1 FROM public.users_identity_security_audit WHERE session_id = session_uuid) THEN
        RAISE EXCEPTION 'audit_session_linkage_required' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_users_audit_session_binding
BEFORE INSERT OR DELETE OR UPDATE OF id, identity_id ON public.users_identity_sessions
FOR EACH ROW EXECUTE FUNCTION public.users_audit_session_binding_guard();
"""

REVERSE_SQL = """
DROP TRIGGER trg_users_audit_session_binding ON public.users_identity_sessions;
DROP FUNCTION public.users_audit_session_binding_guard();
DROP TRIGGER trg_users_audit_hold_update ON public.users_identity_security_audit;
DROP TRIGGER trg_users_audit_hold_insert ON public.users_identity_security_audit;
DROP FUNCTION public.users_audit_hold_update_guard();
DROP FUNCTION public.users_audit_hold_insert_guard();
DROP TRIGGER trg_users_iam_retirement_barrier_3 ON public.users_identity_security_audit;
CREATE TRIGGER trg_users_iam_retirement_barrier_3
BEFORE INSERT OR UPDATE OF is_legal_hold ON public.users_identity_security_audit
FOR EACH ROW EXECUTE FUNCTION public.users_iam_retirement_barrier_guard('is_legal_hold');
DROP FUNCTION public.users_audit_hold_precondition();
DROP FUNCTION public.users_identity_fence_key(uuid);
"""

PREFLIGHT_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.users_identity_security_audit a
        LEFT JOIN public.users_identity_sessions s ON s.id = a.session_id
        WHERE a.is_legal_hold AND (
            (a.identity_id IS NULL AND s.identity_id IS NULL)
            OR (a.session_id IS NOT NULL AND s.id IS NULL)
            OR (a.identity_id IS NOT NULL AND s.identity_id IS NOT NULL
                AND a.identity_id <> s.identity_id)
        )
    ) THEN
        RAISE EXCEPTION 'existing_held_audit_binding_requires_reconciliation'
            USING ERRCODE = '23514';
    END IF;
END;
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("users", "0003_identity_expand")]
    operations = [
        migrations.RunSQL(
            "SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '30s';"
            + KEY_SQL
            + audit_guard_sql("insert")
            + audit_guard_sql("update")
            + SESSION_SQL
            + PREFLIGHT_SQL,
            reverse_sql="SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '30s';" + REVERSE_SQL,
        ),
    ]
