import structlog
from django.core.mail import send_mail

from apps.common.tasks import BaseEnterpriseTask
from config.celery import app

logger = structlog.get_logger(__name__)


@app.task(
    base=BaseEnterpriseTask,
    name="apps.notifications.channels.email_channel.send_notification_email_task",
    queue="notifications",
    ignore_result=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_notification_email_task(*, user_id: str, subject: str, body: str) -> None:
    from apps.users.models import User

    email = (
        User.objects.filter(id=user_id)
        .values_list("email", flat=True)
        .first()
    )
    if not email:
        logger.info("notification_email_skipped_no_address", user_id=user_id)
        return

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[email],
            fail_silently=False,
        )
        logger.info("notification_email_sent", user_id=user_id, subject=subject)
    except Exception as exc:
        logger.error("notification_email_failed", user_id=user_id, error=str(exc))
        raise send_notification_email_task.retry(exc=exc) from exc
