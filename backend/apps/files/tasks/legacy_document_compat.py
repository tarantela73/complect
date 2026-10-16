"""Register the known historical name; consumption of antivirus remains disabled."""

from apps.common.services.file_pipeline.orchestrator import run_antivirus_scan_task

__all__ = ["run_antivirus_scan_task"]
