from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("objects", "0006_backfill_object_slugs"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS objects_supply_objects_overdue_idx
                ON objects_supply_objects (tenant_id, status, is_deleted, planned_end_date)
                WHERE is_deleted = FALSE;
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS objects_supply_objects_overdue_idx;
            """,
        ),
    ]
