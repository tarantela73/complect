from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("contracts", "0008_add_attachment_type")]

    operations = [
        migrations.RunSQL(
            sql="""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS contracts_contracts_cursor_idx
            ON contracts_contracts (tenant_id, created_at DESC, id DESC)
            WHERE is_deleted = FALSE;
            """,
            reverse_sql="DROP INDEX CONCURRENTLY IF EXISTS contracts_contracts_cursor_idx;",
        ),
    ]
