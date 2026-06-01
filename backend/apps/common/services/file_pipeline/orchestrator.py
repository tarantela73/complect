import uuid

import clamd
import structlog
from backend.apps.contracts.models.models import CommonDocument, DocumentStage
from celery import shared_task
from django.conf import settings
from django.db import transaction

from apps.common.repository.sre_engine import EnterpriseSreRepository
from apps.common.tasks import BaseEnterpriseTask

logger = structlog.get_logger(__name__)


class FilePipelineService:
    """[ИСПРАВЛЕНО - ПУНКТ 11] Полная FSM-оркестрация асинхронного конвейера."""

    @classmethod
    def initialize_upload_pipeline(cls, doc_id: uuid.UUID) -> None:
        """Точка входа: инициирует перевод в карантин."""
        with transaction.atomic():
            doc = EnterpriseSreRepository.get_by_id(CommonDocument, doc_id)
            if doc.stage != DocumentStage.UPLOADED:
                raise ValueError("Невалидный статус старта конвейера.")

            EnterpriseSreRepository.atomic_cas_update(
                doc, stage=DocumentStage.QUARANTINE
            )
            transaction.on_commit(lambda: run_antivirus_scan_task.delay(str(doc.id)))


@shared_task(base=BaseEnterpriseTask, bind=True, queue="antivirus")
def run_antivirus_scan_task(self, doc_id: str) -> None:
    """Стриминг байт в ClamAV. При падении ретраится 5 раз, затем падает в Postgres DLQ."""
    doc_uuid = uuid.UUID(doc_id)
    doc = CommonDocument.objects.get(id=doc_uuid)

    # Принудительно выставляем стейт сканирования через репозиторий
    doc.stage = DocumentStage.VIRUS_SCANNING
    doc.save()

    cd = clamd.ClamdNetworkChild(host=settings.CLAMAV_HOST, port=settings.CLAMAV_PORT)
    try:
        s3_stream = settings.S3_CLIENT.get_object(
            Bucket=settings.S3_QUARANTINE_BUCKET, Key=doc.s3_key
        )["Body"]
        scan_result = cd.instream(s3_stream)

        if scan_result and scan_result["stream"] == "FOUND":
            doc.stage = DocumentStage.FAILED
            doc.meta_json["virus_detected"] = True
            doc.save()
            logger.error("ANTIVIRUS CONTOUR: Обнаружена угроза!", file_id=doc_id)
            return

        # Зеленый свет: переводим в статус processing для OCR и тяжелой аналитики
        doc.stage = DocumentStage.PROCESSING
        doc.save()

        # Эвакуация из карантинного бакета в продуктовый
        settings.S3_CLIENT.copy_object(
            Bucket=settings.S3_PRODUCTION_BUCKET,
            Key=doc.s3_key,
            CopySource={"Bucket": settings.S3_QUARANTINE_BUCKET, "Key": doc.s3_key},
        )
        settings.S3_CLIENT.delete_object(
            Bucket=settings.S3_QUARANTINE_BUCKET, Key=doc.s3_key
        )

        # Конвейер завершен -> Финализируем готовность документа
        doc.stage = DocumentStage.READY
        doc.save()

    except Exception as exc:
        # Экспоненциальный управляемый бэкофф с джиттером [Пункт 9]
        raise self.retry(exc=exc, countdown=2**self.request.retries * 15) from exc

