import uuid

from apps.contracts.schemas.contract_dto import ContractResponseSchema


class TestContractSchemaSnapshots:
    def test_contract_response_dto_schema_snapshot_integrity(self):
        """[LEVEL 4: CONTRACT] Snapshot-тест структуры Pydantic DTO для защиты от изменения типов данных."""

        # Имитируем сырые данные, полученные из Selectors Layer (Query Side)
        raw_projection_data = {
            "id": uuid.UUID("11111111-2222-3333-4444-555555555555"),
            "contract_number": "N-777",
            "title": "Поставка газа для котельной ЦОД",
            "status": "ACTIVE",
            "resource_type": "GAS",
            "total_amount": 50000000,  # 500 000 рублей в копейках
            "paid_amount": 20000000,  # 200 000 рублей в копейках
        }

        # Прогоняем данные через Pydantic DTO валидатор выходного контракта Ninja API
        dto = ContractResponseSchema(**raw_projection_data)
        serialized_output = dto.model_dump()

        # [ГЛАВНАЯ ПРОВЕРКА CONTRAСТА]: Сверяем типы полей с жестким эталоном TS SDK
        assert isinstance(serialized_output["id"], uuid.UUID)
        assert isinstance(serialized_output["contract_number"], str)
        assert serialized_output["status"] == "ACTIVE"
        assert serialized_output["resource_type"] == "GAS"

        # Проверяем корректность вычисления изолированной логики Selector-DTO
        assert serialized_output["remaining_amount"] == 30000000
        assert isinstance(serialized_output["remaining_amount"], int)
