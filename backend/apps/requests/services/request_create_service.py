import uuid

from apps.requests.models.request import Request, RequestSource, RequestStatus
from apps.requests.repositories.request_repo import RequestRepository


class RequestCreateService:
    @staticmethod
    def execute(
        client_name: str,
        client_phone: str,
        address: str,
        resource_type: str,
        client_email: str = "",
        comment: str = "",
        source: str = RequestSource.MANUAL,
        manager_id: uuid.UUID | None = None,
    ) -> Request:
        req = Request(
            client_name=client_name,
            client_phone=client_phone,
            client_email=client_email,
            address=address,
            resource_type=resource_type,
            comment=comment,
            source=source,
            status=RequestStatus.NEW,
            manager_id=manager_id,
        )
        return RequestRepository.create(req)
