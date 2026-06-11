import uuid

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.tenancy.context import set_tenant_context
from apps.objects.models.object_stage import OPTIONAL_STAGES, ObjectStage, ObjectStageKey, ObjectStageStatus
from apps.objects.models.supply_object import ResourceType, SupplyObjectType
from apps.objects.schemas.object_dto import SupplyObjectCreateRequest
from apps.objects.services.object_service import ObjectCreateService
from apps.objects.services.stage_service import StageUpdateService


def _create_object(tenant_id: uuid.UUID, name: str = "Тест"):
    set_tenant_context(tenant_id)
    req = SupplyObjectCreateRequest(
        name=name,
        object_type=SupplyObjectType.BUILDING,
        resource_type=ResourceType.GAS,
    )
    return ObjectCreateService.execute(req)


@pytest.mark.django_db(transaction=True)
class TestObjectStageAutoCreate:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_creates_exactly_10_stages(self):
        obj = _create_object(self.tenant_id)
        count = ObjectStage.objects.filter(object_id=obj.id).count()
        assert count == 10

    def test_three_stages_are_optional(self):
        obj = _create_object(self.tenant_id)
        optional_count = ObjectStage.objects.filter(object_id=obj.id, is_optional=True).count()
        assert optional_count == 3

    def test_optional_stages_are_correct_keys(self):
        obj = _create_object(self.tenant_id)
        optional_keys = set(
            ObjectStage.objects.filter(object_id=obj.id, is_optional=True).values_list("stage_key", flat=True)
        )
        expected = {key.value for key in OPTIONAL_STAGES}
        assert optional_keys == expected

    def test_all_stages_start_as_pending(self):
        obj = _create_object(self.tenant_id)
        non_pending = ObjectStage.objects.filter(object_id=obj.id).exclude(status=ObjectStageStatus.PENDING).count()
        assert non_pending == 0

    def test_stages_ordered_by_order_field(self):
        obj = _create_object(self.tenant_id)
        orders = list(ObjectStage.objects.filter(object_id=obj.id).order_by("order").values_list("order", flat=True))
        assert orders == sorted(orders)
        assert orders[0] == 10   # REQUEST_RECEIVED
        assert orders[-1] == 100  # CLOSURE

    def test_stages_belong_to_correct_tenant(self):
        obj = _create_object(self.tenant_id)
        stages = ObjectStage.objects.filter(object_id=obj.id)
        assert all(s.tenant_id == self.tenant_id for s in stages)


@pytest.mark.django_db(transaction=True)
class TestStageUpdateService:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)
        self.obj = _create_object(self.tenant_id)

    def test_skip_optional_stage_succeeds(self):
        StageUpdateService.update(
            self.obj.id, ObjectStageKey.SITE_VISIT, ObjectStageStatus.SKIPPED
        )
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.SITE_VISIT)
        assert stage.status == ObjectStageStatus.SKIPPED

    def test_skip_non_optional_stage_raises(self):
        with pytest.raises(ValidationError):
            StageUpdateService.update(
                self.obj.id, ObjectStageKey.REQUEST_RECEIVED, ObjectStageStatus.SKIPPED
            )

    def test_complete_stage_sets_completed_at(self):
        StageUpdateService.update(
            self.obj.id, ObjectStageKey.COST_ESTIMATION, ObjectStageStatus.COMPLETED
        )
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.COST_ESTIMATION)
        assert stage.status == ObjectStageStatus.COMPLETED
        assert stage.completed_at is not None

    def test_complete_stage_with_completed_by_id(self):
        user_id = uuid.uuid4()
        StageUpdateService.update(
            self.obj.id, ObjectStageKey.COST_ESTIMATION, ObjectStageStatus.COMPLETED, completed_by_id=user_id
        )
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.COST_ESTIMATION)
        assert stage.completed_by_id == user_id

    def test_return_to_pending_clears_completed_at(self):
        StageUpdateService.update(self.obj.id, ObjectStageKey.COST_ESTIMATION, ObjectStageStatus.COMPLETED)
        StageUpdateService.update(self.obj.id, ObjectStageKey.COST_ESTIMATION, ObjectStageStatus.PENDING)
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.COST_ESTIMATION)
        assert stage.status == ObjectStageStatus.PENDING
        assert stage.completed_at is None

    def test_unskip_optional_stage(self):
        StageUpdateService.update(self.obj.id, ObjectStageKey.SITE_VISIT, ObjectStageStatus.SKIPPED)
        StageUpdateService.update(self.obj.id, ObjectStageKey.SITE_VISIT, ObjectStageStatus.PENDING)
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.SITE_VISIT)
        assert stage.status == ObjectStageStatus.PENDING

    def test_update_with_note(self):
        StageUpdateService.update(
            self.obj.id, ObjectStageKey.CONTRACT_SIGNING, ObjectStageStatus.IN_PROGRESS, note="Уточняем условия"
        )
        stage = ObjectStage.objects.get(object_id=self.obj.id, stage_key=ObjectStageKey.CONTRACT_SIGNING)
        assert stage.note == "Уточняем условия"

    def test_tenant_isolation_wrong_object(self):
        other_tenant = uuid.uuid4()
        other_obj = _create_object(other_tenant)
        set_tenant_context(self.tenant_id)
        with pytest.raises(PermissionDenied):
            StageUpdateService.update(
                other_obj.id, ObjectStageKey.REQUEST_RECEIVED, ObjectStageStatus.IN_PROGRESS
            )
