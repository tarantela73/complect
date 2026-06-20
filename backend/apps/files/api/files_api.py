import uuid
from typing import Optional

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from ninja import Query, Router

from apps.common.api.errors import make_error_response
from apps.common.api.permissions import require_permission
from apps.common.tenancy.context_core import get_context
from apps.files.queries.file_queries import get_files_page
from apps.files.repositories.file_repo import FileRepository
from apps.files.schemas.file_dto import (
    DownloadUrlResponse,
    FileDetailResponse,
    FileListResponse,
    UploadCompleteRequest,
    UploadInitRequest,
    UploadInitResponse,
)
from apps.files.services.download_service import DownloadService
from apps.files.services.upload_complete_service import UploadCompleteService
from apps.files.services.upload_init_service import UploadInitService

router = Router(tags=["files"])


@router.get("/", response={200: FileListResponse, 403: dict})
@require_permission("read", "files")
def list_files(
    request: HttpRequest,
    cursor: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    page = get_files_page(cursor=cursor, limit=limit, status=status, q=q)
    return 200, FileListResponse(**page)


@router.post("/upload-init", response={201: UploadInitResponse, 400: dict, 403: dict})
@require_permission("create", "files")
def upload_init(request: HttpRequest, body: UploadInitRequest):
    try:
        ctx = get_context()
        result = UploadInitService.initialize_upload(actor_id=ctx.user_id, request=body)
        return 201, UploadInitResponse(
            file_id=result.file_id,
            object_key=result.object_key,
            upload_url=result.upload_url,
            status=result.status,
        )
    except ValidationError as exc:
        return 400, make_error_response("VALIDATION_ERROR", str(exc))


@router.post(
    "/{file_id}/complete/",
    response={200: FileDetailResponse, 400: dict, 403: dict, 409: dict},
)
@require_permission("create", "files")
def upload_complete(request: HttpRequest, file_id: uuid.UUID, body: UploadCompleteRequest):
    try:
        ctx = get_context()
        record = UploadCompleteService.complete_upload(
            file_id=file_id,
            actor_id=ctx.user_id,
            request=body,
            correlation_id=ctx.correlation_id,
        )
        return 200, FileDetailResponse.model_validate(record)
    except ValidationError as exc:
        return 400, make_error_response("BUSINESS_RULE_VIOLATION", str(exc))
    except PermissionDenied as exc:
        return 409, make_error_response("CONCURRENT_UPDATE", str(exc))


@router.get("/{file_id}/", response={200: FileDetailResponse, 403: dict})
@require_permission("read", "files")
def get_file(request: HttpRequest, file_id: uuid.UUID):
    try:
        record = FileRepository.get_by_id(file_id)
        return 200, FileDetailResponse.model_validate(record)
    except PermissionDenied:
        return 403, make_error_response("FILE_NOT_FOUND", "Файл не найден.")


@router.delete("/{file_id}/", response={204: None, 403: dict, 404: dict, 422: dict})
@require_permission("delete", "files")
def delete_file(request: HttpRequest, file_id: uuid.UUID):
    try:
        record = FileRepository.get_by_id(file_id)
    except PermissionDenied:
        return 404, make_error_response("FILE_NOT_FOUND", "Файл не найден.")
    if record.is_legal_hold:
        return 403, make_error_response("LEGAL_HOLD", "Файл заблокирован юридическим удержанием.")
    deleted = FileRepository.soft_delete(file_id)
    if not deleted:
        return 422, make_error_response("DELETE_FAILED", "Не удалось удалить файл.")
    return 204, None


@router.get(
    "/{file_id}/download-url/",
    response={200: DownloadUrlResponse, 403: dict, 422: dict},
)
@require_permission("download", "files")
def get_download_url(request: HttpRequest, file_id: uuid.UUID):
    try:
        result = DownloadService.get_download_url(file_id=file_id)
        return 200, DownloadUrlResponse(
            file_id=result.file_id,
            download_url=result.download_url,
            expires_in=result.expires_in,
            object_key=result.object_key,
        )
    except ValidationError as exc:
        return 422, make_error_response("FILE_NOT_DOWNLOADABLE", str(exc))
    except PermissionDenied:
        return 403, make_error_response("FILE_NOT_FOUND", "Файл не найден.")
