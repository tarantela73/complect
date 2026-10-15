from django.db import connections

IAM_RETIREMENT_BARRIER_KEY = 0x49414D5F52455449


def acquire_iam_retirement_shared(*, using: str = "default") -> None:
    """Block irreversible IAM retirement while a legal hold is being latched."""
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock_shared(%s)", [IAM_RETIREMENT_BARRIER_KEY])


def acquire_iam_retirement_exclusive(*, using: str = "default") -> None:
    """Block legal-hold latches while the separately gated retirement runs."""
    with connections[using].cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [IAM_RETIREMENT_BARRIER_KEY])
