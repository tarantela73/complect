from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0002_fix_audit_integrity_models"),  # Зависимость от базовых схем
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- 1. Дропаем старую плоскую таблицу логов (если была в легаси)
            DROP TABLE IF EXISTS common_audit_integrity_logs CASCADE;

            -- 2. Создаем мастер-таблицу с декларативным секционированием по диапазону времени
            CREATE TABLE common_audit_integrity_logs (
                id BIGINT NOT NULL,
                tenant_id UUID NOT NULL,
                timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                action_type VARCHAR(100) NOT NULL,
                user_id UUID NOT NULL,
                request_id UUID NOT NULL,
                correlation_id UUID NOT NULL,
                payload_json JSONB NOT NULL,
                previous_hash VARCHAR(64) NOT NULL,
                current_hash VARCHAR(64) NOT NULL
            ) PARTITION BY RANGE (timestamp);

            -- 3. Создаем композитный уникальный индекс, включающий поле партиционирования
            CREATE UNIQUE INDEX idx_audit_logs_id_timestamp ON common_audit_integrity_logs (id, timestamp);
            CREATE INDEX idx_audit_logs_tenant_ts ON common_audit_integrity_logs (tenant_id, timestamp);

            -- 4. Вручную закладываем дефолтную и стартовые секции на май и июнь 2026 года
            CREATE TABLE audit_logs_y2026m05 PARTITION OF common_audit_integrity_logs
                FOR VALUES FROM ('2026-05-01 00:00:00+00') TO ('2026-06-01 00:00:00+00');

            CREATE TABLE audit_logs_y2026m06 PARTITION OF common_audit_integrity_logs
                FOR VALUES FROM ('2026-06-01 00:00:00+00') TO ('2026-07-01 00:00:00+00');
            """,
            reverse_sql="""
            DROP TABLE IF EXISTS common_audit_integrity_logs CASCADE;
            """,
        )
    ]
