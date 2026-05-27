import structlog
from django.conf import settings
from apps.common.tenancy.context import set_force_primary_db

logger = structlog.get_logger(__name__)


class SreInfrastructureGovernanceManager:
    KILL_SWITCH_KEY_FINANCE = "flags:kill_switch:finance"
    REPLICA_HEALTH_KEY = "infra:postgres_replica:status"

    REPLICA_TTL = 60
    HEALTHY_TTL = 120

    @classmethod
    def enforce_replica_lag_protection(cls, current_lag_seconds: float) -> None:
        redis_client = settings.REDIS_CLIENT

        if current_lag_seconds > 1.0:
            redis_client.setex(cls.REPLICA_HEALTH_KEY, cls.REPLICA_TTL, "DEGRADED")

            # IMPORTANT: only for current request context
            set_force_primary_db(True)

            logger.error(
                "SRE: replica degraded → forcing primary routing",
                lag=current_lag_seconds,
            )

        else:
            redis_client.setex(cls.REPLICA_HEALTH_KEY, cls.HEALTHY_TTL, "HEALTHY")

    @classmethod
    def enforce_queue_backlog_protection(cls, queue_name: str, current_latency: float) -> None:

        redis_client = settings.REDIS_CLIENT

        if current_latency > 15.0:
            redis_client.setex(cls.KILL_SWITCH_KEY_FINANCE, 300, "FORCED_OFF")

            logger.critical(
                "SRE: queue backlog detected → kill-switch enabled",
                queue=queue_name,
                latency=current_latency,
            )

        else:
            # only clear if explicitly exists
            if redis_client.get(cls.KILL_SWITCH_KEY_FINANCE):
                redis_client.delete(cls.KILL_SWITCH_KEY_FINANCE)

                logger.info(
                    "SRE: backlog resolved → kill-switch disabled",
                    queue=queue_name,
                )
