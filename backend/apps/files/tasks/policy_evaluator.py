from __future__ import annotations

from apps.common.tasks import BaseEnterpriseTask
from config.celery import app


@app.task(
    base=BaseEnterpriseTask,
    name="apps.files.tasks.policy_evaluator.run_system_control_policy_task",
    queue="files",
    ignore_result=True,
)
def run_system_control_policy_task() -> None:
    """
    Evaluate system control policy and write batch_size control parameter.

    Reads: CB open event rate, scan latency EWMA, rescan backlog.
    Writes: files:control:batch_size (consumed by run_files_consumer_task).
    Runs every 30 seconds; self-healing — parameter TTL restores defaults if task stops.
    """
    from apps.files.services.system_control_policy import get_system_control_policy

    get_system_control_policy().evaluate()
