"""Canonical identity creation across the approved pre/post retirement schema.

Pre-0005 columns are compatibility shadows, never authority. Their original
neutral defaults satisfy the retained NOT NULL schema without restoring them
to the runtime model. No existing identity or shadow value is read or changed.
"""
from django.core.exceptions import ValidationError
from django.db import connection

from apps.users.models import User


def create_identity(**values):
    if not connection.in_atomic_block:
        raise RuntimeError('IDENTITY_CREATION_TRANSACTION_REQUIRED')
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(cursor, User._meta.db_table)
        columns = {column.name for column in description}
    neutral_shadows = {
        'tenant_id': None, 'device_fingerprint': '', 'mfa_enabled': False, 'mfa_secret': '',
    }
    retained = set(neutral_shadows) & columns
    if not retained:
        return User.objects.create(**values)
    if retained != set(neutral_shadows):
        raise ValidationError('IDENTITY_CREATION_SCHEMA_DRIFT')
    identity = User(**values)
    fields = [field for field in User._meta.local_concrete_fields if not field.primary_key]
    field_names = {field.column for field in fields}
    shadow_names = [name for name in neutral_shadows if name not in field_names]
    names = [field.column for field in fields] + shadow_names
    parameters = [field.get_db_prep_save(getattr(identity, field.attname), connection) for field in fields]
    parameters.extend(neutral_shadows[name] for name in shadow_names)
    quote = connection.ops.quote_name
    statement = (
        f'INSERT INTO {quote(User._meta.db_table)} ({", ".join(map(quote, names))}) '
        f'VALUES ({", ".join(["%s"] * len(parameters))}) RETURNING {quote(User._meta.pk.column)}'
    )
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        primary_key = cursor.fetchone()[0]
    return User.objects.get(pk=primary_key)
