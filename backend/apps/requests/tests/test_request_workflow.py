import uuid
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from apps.common.contracts.objects_contracts import ObjectCreationDTO, ObjectCreatorProtocol
from apps.common.tenancy.context import set_tenant_context
from apps.requests.models.request import Request, RequestStatus
from apps.requests.repositories.request_repo import RequestRepository
from apps.requests.services.request_create_service import RequestCreateService
from apps.requests.services.request_workflow_service import RequestWorkflowService


def _make_request(tenant_id: uuid.UUID) -> Request:
    set_tenant_context(tenant_id)
    return RequestCreateService.execute(
        client_name="Иван Иванов",
        client_phone="+79001234567",
        address="Москва, ул. Ленина, 1",
        resource_type="GAS",
    )


def _mock_creator(object_id: uuid.UUID | None = None) -> ObjectCreatorProtocol:
    creator = MagicMock(spec=ObjectCreatorProtocol)
    creator.create_supply_object.return_value = object_id or uuid.uuid4()
    return creator


@pytest.mark.django_db(transaction=True)
class TestRequestCreate:
    def test_creates_with_status_new(self):
        tid = uuid.uuid4()
        req = _make_request(tid)
        assert req.status == RequestStatus.NEW
        assert req.tenant_id == tid

    def test_tenant_isolation(self):
        tid1, tid2 = uuid.uuid4(), uuid.uuid4()
        _make_request(tid1)
        set_tenant_context(tid2)
        result = RequestRepository.get_queryset()
        assert result.count() == 0


@pytest.mark.django_db(transaction=True)
class TestRequestWorkflow:
    @pytest.fixture(autouse=True)
    def setup(self):
        self.tenant_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)

    def test_assign_transitions_new_to_assigned(self):
        req = _make_request(self.tenant_id)
        manager_id = uuid.uuid4()
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.assign(req.id, manager_id)
        req.refresh_from_db()
        assert req.status == RequestStatus.ASSIGNED
        assert req.manager_id == manager_id

    def test_assign_on_converted_raises(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        Request.objects.filter(id=req.id).update(status=RequestStatus.CONVERTED)
        with pytest.raises(ValidationError):
            RequestWorkflowService.assign(req.id, uuid.uuid4())

    def test_start_processing_from_assigned(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        manager_id = uuid.uuid4()
        RequestWorkflowService.assign(req.id, manager_id)
        RequestWorkflowService.start_processing(req.id)
        req.refresh_from_db()
        assert req.status == RequestStatus.PROCESSING

    def test_start_processing_from_new_raises(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        with pytest.raises(ValidationError):
            RequestWorkflowService.start_processing(req.id)

    def test_convert_atomically_creates_object_and_sets_converted(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.assign(req.id, uuid.uuid4())
        RequestWorkflowService.start_processing(req.id)

        expected_obj_id = uuid.uuid4()
        creator = _mock_creator(expected_obj_id)
        result_id = RequestWorkflowService.convert(req.id, creator)

        req.refresh_from_db()
        assert req.status == RequestStatus.CONVERTED
        assert req.converted_object_id == expected_obj_id
        assert req.converted_at is not None
        assert result_id == expected_obj_id

        creator.create_supply_object.assert_called_once()
        dto: ObjectCreationDTO = creator.create_supply_object.call_args[0][0]
        assert dto.name == req.client_name
        assert dto.address == req.address
        assert dto.resource_type == req.resource_type

    def test_convert_from_new_raises(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        with pytest.raises(ValidationError):
            RequestWorkflowService.convert(req.id, _mock_creator())

    def test_convert_rollback_on_creator_failure(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.assign(req.id, uuid.uuid4())
        RequestWorkflowService.start_processing(req.id)

        failing_creator = MagicMock(spec=ObjectCreatorProtocol)
        failing_creator.create_supply_object.side_effect = RuntimeError("DB error")

        with pytest.raises(RuntimeError):
            RequestWorkflowService.convert(req.id, failing_creator)

        req.refresh_from_db()
        assert req.status == RequestStatus.PROCESSING

    def test_close_from_new(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.close(req.id)
        req.refresh_from_db()
        assert req.status == RequestStatus.CLOSED

    def test_close_from_assigned(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.assign(req.id, uuid.uuid4())
        RequestWorkflowService.close(req.id)
        req.refresh_from_db()
        assert req.status == RequestStatus.CLOSED

    def test_close_from_processing(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        RequestWorkflowService.assign(req.id, uuid.uuid4())
        RequestWorkflowService.start_processing(req.id)
        RequestWorkflowService.close(req.id)
        req.refresh_from_db()
        assert req.status == RequestStatus.CLOSED

    def test_close_from_converted_raises(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(self.tenant_id)
        Request.objects.filter(id=req.id).update(status=RequestStatus.CONVERTED)
        with pytest.raises(ValidationError):
            RequestWorkflowService.close(req.id)

    def test_wrong_tenant_raises_permission_denied(self):
        req = _make_request(self.tenant_id)
        set_tenant_context(uuid.uuid4())
        with pytest.raises(PermissionDenied):
            RequestWorkflowService.assign(req.id, uuid.uuid4())
