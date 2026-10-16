"""Validate repository agent state; never execute commands from state files."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import html
import json
import re
import subprocess
import stat
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"not-started", "in-progress", "blocked", "done"}

VERIFICATION_INPUTS = (
    "init.sh",
    "Makefile",
    "infra/scripts/agent_harness.py",
    "infra/scripts/check_agent_harness.sh",
    "infra/scripts/tests/test_agent_harness.py",
    "infra/scripts/tests/harness_skill.test.mjs",
    "docs/agents/harness-skill-provenance.json",
)


def verification_inputs(root: Path) -> set[str]:
    return set(VERIFICATION_INPUTS) | {
        str(path.relative_to(root)) for path in (root / ".agents/skills/harness-creator").rglob("*") if path.is_file()
    }


SNAPSHOT_EXCLUDED_PREFIXES = (".git/", ".aws/", ".dev/", ".codex/", ".ssh/", ".kube/", "runtime/")
SNAPSHOT_EXCLUDED_NAMES = {".env"}


def _git(root: Path, *args: str) -> str:
    environment = dict(__import__("os").environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result.stdout


def _git_bytes(root: Path, *args: str) -> bytes:
    """Run Git without text decoding so NUL-delimited index data is exact."""
    environment = dict(__import__("os").environ)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        env=environment,
    )
    return result.stdout


def _snapshot_path_allowed(path: str) -> bool:
    return (
        path not in SNAPSHOT_EXCLUDED_NAMES
        and not path.startswith(SNAPSHOT_EXCLUDED_PREFIXES)
        and not path.endswith(".env")
        and (".env." not in path or path.endswith(".example"))
    )


def working_tree_snapshot(root: Path) -> dict[str, Any]:
    """Return a deterministic, read-only manifest of the current working tree.

    This is deliberately separate from provenance validation: recording a task
    snapshot is explicit and never changes the accepted provenance hashes.
    """
    root = root.resolve()
    _git(root, "rev-parse", "--show-toplevel")
    raw_paths = _git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files: dict[str, dict[str, object]] = {}
    paths = set(raw_paths.split("\0"))
    # Git ignores *.log, but a current successful receipt is an explicit
    # verification dependency. Include only this bounded, source-owned evidence
    # directory; runtime .dev reports and arbitrary ignored logs remain excluded.
    state_path = root / "feature_list.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        for feature in state.get("features", []):
            for receipt in feature.get("evidence", []):
                log = receipt.get("log")
                if isinstance(log, str) and log.startswith("docs/agents/harness-evidence/") and log.endswith(".log"):
                    candidate = root / log
                    if not candidate.resolve().is_relative_to(root / "docs/agents/harness-evidence"):
                        raise ValueError("unsafe verification log path")
                    if candidate.is_file():
                        paths.add(log)
    for path in sorted(paths):
        if not path or not _snapshot_path_allowed(path):
            continue
        candidate = root / path
        if not candidate.is_file() and not candidate.is_symlink():
            continue
        if candidate.is_symlink():
            target = candidate.resolve(strict=False)
            if not target.is_relative_to(root) or not _snapshot_path_allowed(str(target.relative_to(root))):
                raise ValueError(f"unsafe symlink target: {path}")
        data = candidate.read_bytes() if candidate.is_file() else str(candidate.readlink()).encode()
        metadata: dict[str, object] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": stat.S_IMODE(candidate.lstat().st_mode),
            "kind": "symlink" if candidate.is_symlink() else "file",
        }
        if candidate.is_symlink():
            metadata["target"] = str(candidate.readlink())
        files[path] = metadata
    deleted = set(_git(root, "ls-files", "--deleted", "-z").split("\0"))
    deleted.update(_git(root, "diff", "--name-only", "--diff-filter=D", "HEAD", "--").splitlines())
    return {
        "format": "complect-working-tree-snapshot-v1",
        "head": _git(root, "rev-parse", "HEAD").strip(),
        "index_entries_sha256": hashlib.sha256(_git_bytes(root, "ls-files", "--stage", "-z")).hexdigest(),
        "index_flags_sha256": hashlib.sha256(_git_bytes(root, "ls-files", "-v", "-z")).hexdigest(),
        "files": dict(sorted(files.items())),
        "deleted": sorted(path for path in deleted if path and _snapshot_path_allowed(path)),
        "excluded": [".git", ".aws", ".dev", ".codex", ".ssh", ".kube", "runtime", "*.env", ".env.* except *.example"],
    }


def verify_working_tree_snapshot(root: Path, manifest_path: Path) -> list[str]:
    try:
        expected = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as error:
        return [f"cannot read snapshot manifest: {error}"]
    if expected.get("format") != "complect-working-tree-snapshot-v1":
        return ["unsupported snapshot manifest format"]
    try:
        actual = working_tree_snapshot(root)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return [f"cannot inspect working tree: {error}"]
    errors = []
    for field in ("head", "index_entries_sha256", "index_flags_sha256", "files", "deleted", "excluded"):
        if expected.get(field) != actual.get(field):
            errors.append(f"snapshot stale: {field} differs")
    return errors


def verification_summary(text: str) -> dict[str, int]:
    python = re.findall(r"(?m)^Ran (\d+) tests? in ", text)

    def node_count(name: str) -> int:
        matches = re.findall(rf"(?m)^# {name} (\d+)$", text)
        if len(matches) != 1:
            raise ValueError(f"missing/ambiguous Node {name} summary")
        return int(matches[0])

    if len(python) != 1 or int(python[0]) < 1 or not re.search(r"(?m)^OK$", text):
        raise ValueError("missing, unsuccessful or empty Python test run")
    node = node_count("tests")
    named = re.findall(r"(?m)^# Subtest: (.+)$", text)
    if node < 1 or not any(not name.endswith(".mjs") for name in named):
        raise ValueError("empty Node test run")
    if node_count("pass") != node or any(node_count(name) for name in ("fail", "skipped", "cancelled")):
        raise ValueError("unsuccessful or skipped Node tests")
    if "HARNESS_GATE: PASS; ERP_ACCEPTANCE: NOT_RUN" not in text:
        raise ValueError("missing mandatory gate or ERP boundary")
    return {"python_tests": int(python[0]), "node_tests": node, "skips": 0}


def local_file(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        return None
    candidate = (root / value).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        return None
    return candidate


def state_errors(root: Path, state: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict) or not isinstance(state.get("features"), list) or not state["features"]:
        return ["features must be a nonempty array"]
    features = state["features"]
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for feature in features:
        if not isinstance(feature, dict):
            errors.append("feature must be an object")
            continue
        identifier = feature.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier.startswith("feat-")
            or not identifier[5:].isdigit()
            or identifier in ids
        ):
            errors.append("invalid or duplicate feature ID")
            continue
        ids.add(identifier)
        by_id[identifier] = feature
        for field in ("name", "description"):
            if not isinstance(feature.get(field), str) or not feature[field].strip():
                errors.append(f"{identifier}: missing {field}")
        if not isinstance(feature.get("status"), str) or feature["status"] not in STATUSES:
            errors.append(f"{identifier}: invalid status")
        dependencies = feature.get("dependencies")
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) for dep in dependencies):
            errors.append(f"{identifier}: invalid dependencies")
        elif len(set(dependencies)) != len(dependencies):
            errors.append(f"{identifier}: duplicate dependencies")
        criteria = feature.get("acceptance_criteria")
        if (
            not isinstance(criteria, list)
            or not criteria
            or any(not isinstance(item, str) or not item.strip() for item in criteria)
        ):
            errors.append(f"{identifier}: missing acceptance criteria")
        owned = feature.get("files")
        if not isinstance(owned, list) or not owned or any(local_file(root, item) is None for item in owned):
            errors.append(f"{identifier}: missing owned files")
        if feature.get("status") == "done":
            evidence = feature.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{identifier}: done requires evidence")
                continue
            for item in evidence:
                if not isinstance(item, dict):
                    errors.append(f"{identifier}: invalid evidence")
                    continue
                log = local_file(root, item.get("log"))
                if (
                    not isinstance(item.get("command"), str)
                    or not item["command"].strip()
                    or type(item.get("exit_code")) is not int
                    or item["exit_code"] != 0
                ):
                    errors.append(f"{identifier}: evidence requires a successful command")
                if log is None or hashlib.sha256(log.read_bytes()).hexdigest() != item.get("sha256"):
                    errors.append(f"{identifier}: evidence log absent or changed")
                if log is not None:
                    try:
                        summary = verification_summary(log.read_text())
                        if item.get("verification") != summary or item.get("verification_status") != "PASS":
                            errors.append(f"{identifier}: unverified mandatory checks")
                    except (ValueError, UnicodeError) as error:
                        errors.append(f"{identifier}: {error}")
                if (
                    item.get("command") != "bash infra/scripts/check_agent_harness.sh"
                    or item.get("producer") != "agent_harness.py --record"
                    or item.get("timed_out") is not False
                    or item.get("sources_unchanged_during_run") is not True
                    or item.get("erp_runtime") != "NOT_RUN"
                ):
                    errors.append(f"{identifier}: invalid verification receipt")
                sources = item.get("sources")
                if not isinstance(sources, dict) or not sources:
                    errors.append(f"{identifier}: evidence requires source hashes")
                else:
                    if not verification_inputs(root).issubset(sources):
                        errors.append(f"{identifier}: missing mandatory verification inputs")
                    for name, digest in sources.items():
                        source = local_file(root, name)
                        if source is None or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                            errors.append(f"{identifier}: evidence source absent or changed")
    if sum(feature.get("status") == "in-progress" for feature in by_id.values()) > 1:
        errors.append("only one active feature allowed")
    for identifier, feature in by_id.items():
        if feature.get("status") == "done" and isinstance(feature.get("files"), list):
            covered = set()
            for item in feature.get("evidence", []):
                if isinstance(item, dict) and isinstance(item.get("sources"), dict):
                    covered.update(item["sources"])
            if any(not isinstance(name, str) for name in feature["files"]) or not set(feature["files"]).issubset(
                covered
            ):
                errors.append(f"{identifier}: evidence does not cover owned files")
        dependencies = feature.get("dependencies")
        if not isinstance(dependencies, list):
            continue
        for dep in dependencies:
            if not isinstance(dep, str) or dep not in by_id or dep == identifier:
                errors.append(f"{identifier}: unknown/self dependency")
            elif feature.get("status") in ("in-progress", "done") and by_id[dep].get("status") != "done":
                errors.append(f"{identifier}: dependency not done")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            errors.append("dependency cycle")
            return
        if identifier in visited:
            return
        visiting.add(identifier)
        deps = by_id[identifier].get("dependencies")
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, str) and dep in by_id:
                    visit(dep)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in by_id:
        visit(identifier)
    return errors


def provenance_errors(root: Path) -> list[str]:
    base = root / ".agents/skills/harness-creator"
    try:
        manifest = json.loads((root / "docs/agents/harness-skill-provenance.json").read_text())
        entries = manifest["files"]
        if not isinstance(entries, list) or not entries:
            return ["missing skill provenance entries"]
        expected = {}
        for item in entries:
            source = local_file(base, item.get("path"))
            if source is None or hashlib.sha256(source.read_bytes()).hexdigest() != item.get("installed_sha256"):
                return ["skill provenance hash mismatch"]
            if item["path"] in expected:
                return ["duplicate skill provenance entry"]
            expected[item["path"]] = item["installed_sha256"]
        actual = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}
        return [] if actual == set(expected) else ["skill provenance file set mismatch"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ["cannot read skill provenance"]


def assess(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    groups = {
        "instructions": ["AGENTS.md", "docs/agents/harness.md"],
        "state": ["feature_list.json", "progress.md"],
        "verification": [
            "init.sh",
            "infra/scripts/check_agent_harness.sh",
            "infra/scripts/tests/test_agent_harness.py",
        ],
        "scope": ["feature_list.json", "docs/agents/harness.md"],
        "lifecycle": ["session-handoff.md", "progress.md"],
    }
    results = {}
    for group, names in groups.items():
        missing = [name for name in names if local_file(root, name) is None]
        results[group] = {"status": "FAIL" if missing else "PASS", "missing": missing}
        errors.extend(f"missing {name}" for name in missing)
    try:
        state = json.loads((root / "feature_list.json").read_text())
        failures = state_errors(root, state)
        errors.extend(failures)
        if failures:
            results["state"]["status"] = "FAIL"
            results["scope"]["status"] = "FAIL"
    except (OSError, ValueError):
        errors.append("cannot read feature_list.json")
        results["state"]["status"] = "FAIL"
        results["scope"]["status"] = "FAIL"
    errors.extend(provenance_errors(root))
    return {
        "status": "FAIL" if errors else "PASS",
        "subsystems": results,
        "errors": errors,
        "limits": (
            "Checks files, state and evidence integrity; does not prove acceptance criteria or agent effectiveness."
        ),
        "erp_runtime": "NOT_RUN",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--html", type=Path)
    parser.add_argument(
        "--record",
        action="store_true",
        help="Run the fixed harness gate and produce its receipt; no arbitrary commands",
    )
    parser.add_argument(
        "--record-snapshot",
        "--snapshot",
        dest="snapshot",
        type=Path,
        help="Explicitly record a manifest of the current working tree",
    )
    parser.add_argument(
        "--verify-snapshot",
        type=Path,
        help="Require the current working tree to match an explicit snapshot manifest",
    )
    parser.add_argument(
        "--snapshot-summary",
        action="store_true",
        help="Print only snapshot status, path and SHA-256 (requires --record-snapshot)",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if args.snapshot_summary and args.snapshot is None:
        parser.error("--snapshot-summary requires --record-snapshot")
    if args.snapshot_summary and args.verify_snapshot is not None:
        parser.error("--snapshot-summary cannot be combined with --verify-snapshot")
    if args.verify_snapshot is not None:
        manifest_path = args.verify_snapshot if args.verify_snapshot.is_absolute() else root / args.verify_snapshot
        errors = verify_working_tree_snapshot(root, manifest_path)
        if errors:
            print(json.dumps({"status": "FAIL", "errors": errors}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"status": "PASS", "snapshot": str(manifest_path)}, ensure_ascii=False))
        return 0
    if args.snapshot is not None:
        destination = args.snapshot if args.snapshot.is_absolute() else root / args.snapshot
        try:
            manifest = working_tree_snapshot(root)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            print(f"snapshot failed: {error}")
            return 1
        if args.snapshot_summary:
            print(json.dumps({"status": "PASS", "path": str(destination), "sha256": digest}, ensure_ascii=False))
            return 0
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.record:
        preflight = assess(root)
        if preflight["errors"]:
            print(json.dumps(preflight, indent=2))
            return 1
        state = json.loads((root / "feature_list.json").read_text())
        active = [f for f in state["features"] if f["status"] == "in-progress"]
        if len(active) != 1:
            parser.error("record requires exactly one active feature")
        feature = active[0]
        sources = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in set(feature["files"]) | verification_inputs(root)
        }
        run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        relative = f"docs/agents/harness-evidence/{run_id}"
        command = ["bash", "infra/scripts/check_agent_harness.sh"]
        timed_out = False
        try:
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False, timeout=180)
        except subprocess.TimeoutExpired as error:
            timed_out = True

            def decoded(value: str | bytes | None) -> str:
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""

            completed = subprocess.CompletedProcess(
                command,
                124,
                decoded(error.stdout),
                decoded(error.stderr) + "\nHarness gate timed out after 180 seconds.\n",
            )
        except OSError as error:
            completed = subprocess.CompletedProcess(command, 127, "", str(error))
        log = root / f"{relative}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(completed.stdout + completed.stderr)
        receipt = {
            "producer": "agent_harness.py --record",
            "run_id": run_id,
            "cwd": str(root),
            "command": " ".join(command),
            "exit_code": completed.returncode,
            "log": f"{relative}.log",
            "sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
            "sources": sources,
            "erp_runtime": "NOT_RUN",
            "timed_out": timed_out,
        }
        unchanged = all(
            (source := local_file(root, name)) is not None and hashlib.sha256(source.read_bytes()).hexdigest() == digest
            for name, digest in sources.items()
        )
        receipt["sources_unchanged_during_run"] = unchanged
        try:
            receipt["verification"] = verification_summary(log.read_text())
            verified = completed.returncode == 0 and unchanged and not timed_out
        except ValueError as error:
            receipt["verification_error"] = str(error)
            verified = False
        receipt["verification_status"] = "PASS" if verified else "FAIL"
        (root / f"{relative}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0 if verified else completed.returncode or 1
    result = assess(root)
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        rows = "".join(
            f"<tr><td>{html.escape(name)}</td><td>{group['status']}</td></tr>"
            for name, group in result["subsystems"].items()
        )
        document = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Complect: проверки работы агента</title>
<style>body{{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 20px}}
td,th{{padding:10px;text-align:left;border-bottom:1px solid #ddd}}
pre{{white-space:pre-wrap}}</style>
<h1>Complect: проверки работы агента</h1><p>{result["status"]}</p>
<table><tr><th>Подсистема</th><th>Статус</th></tr>{rows}</table>
<p>Проверка ERP: NOT_RUN. Проверены файлы, состояние задач и целостность доказательств.
Качество реальных сессий требует отдельного измерения.</p>
<pre>{html.escape(json.dumps(result, ensure_ascii=False, indent=2))}</pre></html>"""
        args.html.write_text(document)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
