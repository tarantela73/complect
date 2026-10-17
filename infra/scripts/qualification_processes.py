"""Small, journaled process supervisor for one qualification attempt."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_WAIT = 30.0
_POLL = 0.05


def _boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _proc_identity(pid: int) -> dict[str, Any]:
    stat = Path(f"/proc/{pid}/stat").read_text()
    end = stat.rfind(")")
    fields = stat[end + 2 :].split()
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").strip().decode(errors="replace")
    return {
        "pid": pid,
        "boot_id": _boot_id(),
        "starttime": int(fields[19]),
        "pgid": int(fields[2]),
        "session": int(fields[3]),
        "state": fields[0],
        "cmdline": cmdline,
    }


def _same_process(expected: dict[str, Any], *, leader: bool = False) -> bool:
    try:
        actual = _proc_identity(int(expected["pid"]))
    except (FileNotFoundError, OSError, ValueError, IndexError):
        return False
    keys = ("boot_id", "starttime", "pid", "pgid", "session", "cmdline") if leader else ("boot_id", "starttime")
    if not all(actual.get(key) == expected.get(key) for key in keys):
        return False
    return not leader or (
        actual["pgid"] == actual["pid"]
        and actual["session"] == actual["pid"]
        and actual["pgid"] != os.getpgrp()
    )


def _journal_path(directory: Path, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("invalid stage name")
    path = directory / "processes" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    status = path.parent.lstat()
    if path.parent.is_symlink() or status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise ValueError("unsafe process journal directory")
    return path


def _write_record(path: Path, value: dict[str, Any]) -> None:
    from infra.scripts.run_legacy_qualification import write_record

    write_record(path, value)


def _read_record(path: Path) -> dict[str, Any]:
    from infra.scripts.run_legacy_qualification import read_record

    return read_record(path)


def _children_in_group(pgid: int, session: int) -> list[dict[str, Any]]:
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            identity = _proc_identity(int(entry.name))
        except (FileNotFoundError, OSError, ValueError, IndexError):
            continue
        if identity["pgid"] == pgid and identity["session"] == session and identity["state"] != "Z":
            found.append(identity)
    return found


def _child_main(args: argparse.Namespace) -> int:
    directory = args.directory.resolve()
    journal = _journal_path(directory, args.name)
    permit = journal.with_suffix(".permit")
    identity = _proc_identity(os.getpid())
    record = {
        "version": 1,
        "name": args.name,
        "state": "waiting",
        "identity": identity,
        "parent": {"pid": args.parent_pid, "boot_id": args.parent_boot_id, "starttime": args.parent_starttime},
        "cwd": str(args.cwd),
    }
    _write_record(journal, record)
    deadline = time.monotonic() + _WAIT
    while not permit.exists():
        if not _same_process(record["parent"]):
            record["state"] = "blocked_parent_lost"
            record["exit_code"] = 125
            _write_record(journal, record)
            return 125
        if time.monotonic() >= deadline:
            record["state"] = "blocked_timeout"
            record["exit_code"] = 125
            _write_record(journal, record)
            return 125
        time.sleep(_POLL)
    try:
        permit.unlink()
    except FileNotFoundError:
        pass
    if not _same_process(record["parent"]):
        record["state"] = "blocked_parent_lost"
        record["exit_code"] = 125
        _write_record(journal, record)
        return 125
    record["state"] = "running"
    _write_record(journal, record)
    try:
        completed = subprocess.run(args.command, cwd=args.cwd, check=False)
        code = completed.returncode
    except BaseException as exc:
        code = 125
        record["error"] = type(exc).__name__
    record["state"] = "finished"
    normalized = 128 + (-code) if code < 0 else code
    record["exit_code"] = normalized
    _write_record(journal, record)
    return normalized


def run_stage(directory: Path, name: str, command: list[str], cwd: Path) -> int:
    """Run one stage only after its durable process identity is journaled."""
    journal = _journal_path(directory.resolve(), name)
    if journal.exists():
        raise ValueError(f"stage journal already exists: {name}")
    intent = journal.with_name(f"{name}.intent.json")
    if intent.exists():
        raise ValueError(f"stage intent already exists: {name}")
    parent = _proc_identity(os.getpid())
    _write_record(intent, {"version": 1, "name": name, "state": "spawn_intent", "parent": parent, "cwd": str(cwd)})
    log_path = journal.with_suffix(".log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(log_fd, "w") as log:
            process = subprocess.Popen(
                [sys.executable, "-B", "-m", "infra.scripts.qualification_processes", "child",
                 "--directory", str(directory.resolve()), "--name", name, "--cwd", str(cwd.resolve()),
                 "--parent-pid", str(parent["pid"]), "--parent-boot-id", parent["boot_id"],
                 "--parent-starttime", str(parent["starttime"]), "--", *command],
                cwd=cwd,
                start_new_session=True,
                close_fds=True,
                stdout=log,
                stderr=log,
            )
    except BaseException:
        raise
    _write_record(intent, {"version": 1, "name": name, "state": "spawned", "parent": parent, "pid": process.pid})
    deadline = time.monotonic() + _WAIT
    while not journal.exists():
        if process.poll() is not None:
            raise RuntimeError("stage supervisor exited before journaling")
        if time.monotonic() >= deadline:
            process.kill()
            raise RuntimeError("stage supervisor did not journal identity")
        time.sleep(_POLL)
    record = _read_record(journal)
    if record.get("state") != "waiting":
        raise RuntimeError("invalid stage waiting journal")
    _write_record(journal.with_suffix(".permit"), {"version": 1, "stage": name})
    returncode = process.wait()
    record = _read_record(journal)
    record.setdefault("exit_code", 128 + (-returncode) if returncode < 0 else returncode)
    record["process_returncode"] = returncode
    _write_record(journal, record)
    _write_record(intent, {"version": 1, "name": name, "state": "finished", "pid": process.pid, "exit_code": int(record["exit_code"])})
    return int(record["exit_code"])


def stop_work(directory: Path) -> None:
    """Stop only live, fingerprinted stage groups owned by this attempt."""
    process_dir = directory.resolve() / "processes"
    if process_dir.is_symlink():
        raise RuntimeError("process journal directory symlink refused")
    if not process_dir.is_dir():
        return
    intents = sorted(process_dir.glob("*.intent.json"))
    journals = sorted(path for path in process_dir.glob("*.json") if not path.name.endswith(".intent.json"))
    by_name = {path.stem: path for path in journals}
    for intent in intents:
        if intent.name.removesuffix(".intent.json") not in by_name:
            raise RuntimeError("spawn intent has no process journal")
    for journal in journals:
        record = _read_record(journal)
        identity = record.get("identity")
        if not isinstance(identity, dict) or not all(key in identity for key in ("pid", "boot_id", "starttime", "pgid", "session", "cmdline")):
            raise RuntimeError("malformed process identity journal")
        if not _same_process(identity, leader=True):
            active = _children_in_group(int(identity.get("pgid", -1)), int(identity.get("session", -1)))
            if active:
                raise RuntimeError("stage leader missing while process group remains active")
            if record.get("state") != "finished":
                record["state"] = "leader_gone"
            _write_record(journal, record)
            continue
        pgid = int(identity["pgid"])
        session = int(identity["session"])
        os.killpg(pgid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while _children_in_group(pgid, session) and time.monotonic() < deadline:
            time.sleep(_POLL)
        if _children_in_group(pgid, session):
            os.killpg(pgid, signal.SIGKILL)
            deadline = time.monotonic() + 2
            while _children_in_group(pgid, session) and time.monotonic() < deadline:
                time.sleep(_POLL)
        if _children_in_group(pgid, session):
            raise RuntimeError("stage process group did not stop")
        record["state"] = "stopped"
        record["exit_code"] = 128 + signal.SIGTERM
        _write_record(journal, record)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    child = sub.add_parser("child")
    child.add_argument("--directory", type=Path, required=True)
    child.add_argument("--name", required=True)
    child.add_argument("--cwd", type=Path, required=True)
    child.add_argument("--parent-pid", type=int, required=True)
    child.add_argument("--parent-boot-id", required=True)
    child.add_argument("--parent-starttime", type=int, required=True)
    child.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == "child":
        if args.command[:1] == ["--"]:
            args.command = args.command[1:]
        if not args.command:
            raise SystemExit("child command is required")
        return _child_main(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
