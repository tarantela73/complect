from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0001_initial"),  # Наша базовая схема таблиц
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- 1. Активируем Row Level Security для таблицы договоров
            ALTER TABLE contracts_contracts ENABLE ROW LEVEL SECURITY;
            ALTER TABLE contracts_contracts FORCE ROW LEVEL SECURITY;

            -- 2. Удаляем старую политику, если она существовала
            DROP POLICY IF EXISTS contract_tenant_isolation_policy ON contracts_contracts;

            -- 3. Создаем жесткую политику изоляции:
            -- Строка доступна только если её tenant_id совпадает с сессионной переменной app.current_tenant_id
            CREATE POLICY contract_tenant_isolation_policy ON contracts_contracts
                AS RESTRICTIVE
                USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
            """,
            reverse_sql="""
            DROP POLICY IF EXISTS contract_tenant_isolation_policy ON contracts_contracts;
            ALTER TABLE contracts_contracts DISABLE ROW LEVEL SECURITY;
            """,
        )
    ]
