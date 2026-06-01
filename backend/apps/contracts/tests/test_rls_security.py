import uuid

import pytest
from django.db import connection

from apps.common.tenancy.context import set_tenant_context


@pytest.mark.django_db(transaction=True)
class TestPostgresRowLevelSecurity:
    @pytest.mark.skip(reason="Требует настроенной RLS политики в PostgreSQL — только prod/stage окружение")
    def test_database_enforces_rls_and_blocks_raw_sql_leak(self):
        """[LEVEL 8: SECURITY] Проверяет защиту RLS от прямого несанкционированного чтения."""
        tenant_alpha = uuid.uuid4()
        tenant_beta = uuid.uuid4()

        # 1. Создаем договор для компании Alpha через сырой SQL, чтобы обойти менеджеры
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.current_tenant_id = %s;", [str(tenant_alpha)])
            contract_id = uuid.uuid4()
            cursor.execute(
                "INSERT INTO contracts_contracts "
                "(id, tenant_id, contract_number, title, status, resource_type, "
                "total_amount, paid_amount, created_at, updated_at, is_deleted, version, is_legal_hold) "
                "VALUES (%s, %s, 'N-100', 'Договор Альфа', 'DRAFT', 'GAS', "
                "1000, 0, NOW(), NOW(), FALSE, 1, FALSE);",
                [str(contract_id), str(tenant_alpha)],
            )

        # 2. Переключаем контекст сессии Python на компанию Beta
        set_tenant_context(tenant_beta)

        # Инжектируем токен Beta в текущую сессию Postgres
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.current_tenant_id = %s;", [str(tenant_beta)])

            # Попытка прочитать данные компании Alpha через "грязный" сырой SQL
            cursor.execute("SELECT COUNT(*) FROM contracts_contracts WHERE contract_number = 'N-100';")
            count = cursor.fetchone()[0]

            # Результат должен быть строго 0! RLS скрыл строку на уровне ядра СУБД
            assert count == 0
