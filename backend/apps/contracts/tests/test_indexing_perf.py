import pytest
from django.db import connection


@pytest.mark.django_db
class TestDatabaseIndexingPerformance:
    def test_query_uses_index_scan_for_status_and_tenant_filtering(self):
        """[LEVEL 2: QUERIES] Гарантирует использование B-Tree индексов для защиты от полного сканирования таблиц."""

        # Строим raw EXPLAIN запрос для проверки планировщика PostgreSQL
        explain_query = """
            EXPLAIN (FORMAT JSON) 
            SELECT id FROM contracts_contracts 
            WHERE tenant_id = '00000000-0000-0000-0000-000000000000'::uuid 
              AND status = 'ACTIVE' 
              AND is_deleted = FALSE;
        """

        with connection.cursor() as cursor:
            cursor.execute(explain_query)
            plan_data = cursor.fetchone()[0]

            # Парсим JSON-план от PostgreSQL
            root_node = plan_data[0]["Plan"]
            node_type = root_node["Node Type"]

            # Сканирование обязано идти через индекс (Index Scan или Bitmap Index Scan)
            # Если тест поймает Seq Scan — сборка на CI/CD упадет!
            assert "Index Scan" in node_type or "Bitmap" in node_type, (
                f"CRITICAL PERFORMANCE FLUTTER: СУБД использует {node_type} вместо эффективного Index Scan!"
            )
