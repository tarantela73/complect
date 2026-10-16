import json
import sys
import threading
import time
from pathlib import Path

from infra.scripts.qualification_processes import run_stage, stop_work


def test_run_stage_journals_before_command_and_runs_it(tmp_path):
    marker = tmp_path / "marker"
    result = run_stage(
        tmp_path,
        "prepare",
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).write_text('done')"],
        Path.cwd(),
    )

    assert result == 0
    assert marker.read_text() == "done"
    record = json.loads((tmp_path / "processes/prepare.json").read_text())
    assert record["state"] == "finished"
    assert record["exit_code"] == 0
    assert "env" not in record


def test_stop_work_terminates_owned_process_group(tmp_path):
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            run_stage(tmp_path, "test", [sys.executable, "-c", "import time; time.sleep(30)"], Path.cwd())
        )
    )
    thread.start()
    journal = tmp_path / "processes/test.json"
    for _ in range(100):
        if journal.exists():
            break
        time.sleep(0.05)
    stop_work(tmp_path)
    thread.join(5)

    assert not thread.is_alive()
    assert result == [143]
    assert json.loads(journal.read_text())["state"] in {"stopped", "finished"}
