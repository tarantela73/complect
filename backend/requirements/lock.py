"""Compile and verify requirements locks inside the pinned Docker toolchain."""
from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
ROOT = DIRECTORY.parent.parent
INPUTS = ("base.txt", "dev.txt", "lock.py")
LOCKS = ("base.lock", "dev.lock")
METADATA = "lock-metadata.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def uv_version() -> str:
    return subprocess.check_output(["uv", "--version"], text=True).split()[1]


def verify_inputs() -> None:
    metadata = json.loads((DIRECTORY / METADATA).read_text())
    required_fields = {"schema_version", "python", "uv", "python_platform", "sha256"}
    if not isinstance(metadata, dict) or set(metadata) != required_fields or metadata["schema_version"] != 1:
        raise ValueError("Invalid lock metadata schema")
    fingerprints = metadata["sha256"]
    if not isinstance(fingerprints, dict) or set(fingerprints) != set((*INPUTS, *LOCKS)):
        raise ValueError("Lock metadata must fingerprint every declared input and lock, with no extra paths")
    if metadata["python_platform"] != "x86_64-unknown-linux-gnu":
        raise ValueError("Unsupported lock target")
    for name in (*INPUTS, *LOCKS):
        expected = fingerprints[name]
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError(f"Invalid SHA-256 fingerprint for {name}")
        if digest(DIRECTORY / name) != expected:
            raise ValueError(f"Stale dependency lock: {name} changed; run make lock-backend")
    if platform.python_version() != metadata["python"] or uv_version() != metadata["uv"]:
        raise ValueError("Use the Python/uv versions in toolchain.json")
    if platform.machine() != "x86_64" or sys.platform != "linux":
        raise ValueError("This lock targets Linux amd64; another target needs separate validation")


def generate(destination: Path) -> dict:
    # The running pinned image/resolver are the compiler authority. The backend
    # Docker build context does not contain the repository-root manifest.
    python = platform.python_version()
    uv = uv_version()
    target = "x86_64-unknown-linux-gnu"
    manifest_path = ROOT / "toolchain.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if python != manifest["python"]["version"] or uv != manifest["uv"]["version"]:
            raise ValueError("Compile with the Python/uv toolchain in toolchain.json")
        if target != manifest["target"]["python_platform"]:
            raise ValueError("Unsupported manifest target")
    if platform.machine() != "x86_64" or sys.platform != "linux":
        raise ValueError("Compile in the Linux amd64 toolchain")
    for name in (*INPUTS, *LOCKS):
        source = DIRECTORY / name
        if source.exists():
            shutil.copyfile(source, destination / name)
    options = [
        "--python-version", python,
        "--python-platform", target,
        "--generate-hashes", "--no-header", "--no-annotate",
        "--default-index", "https://pypi.org/simple",
    ]
    for group in ("base", "dev"):
        command = ["uv", "--no-config", "pip", "compile", f"{group}.txt", *options]
        # Preserve the existing dev selections when initially creating base.lock.
        # Both groups must resolve shared packages to the same versions.
        constraint = "dev.lock" if group == "base" else "base.lock"
        if (destination / constraint).exists() and (group == "dev" or not (destination / "base.lock").exists()):
            command.extend(["--constraint", constraint])
        command.extend(["--output-file", f"{group}.lock"])
        subprocess.run(command, cwd=destination, check=True, stdout=subprocess.DEVNULL)
    return {
        "schema_version": 1,
        "python": python,
        "uv": uv,
        "python_platform": target,
        "sha256": {name: digest(destination / name) for name in (*INPUTS, *LOCKS)},
    }


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in {"compile", "check", "check-inputs"}:
        raise ValueError("Usage: lock.py compile|check|check-inputs")
    mode = sys.argv[1]
    if mode in {"check", "check-inputs"}:
        verify_inputs()
    if mode == "check-inputs":
        print("Dependency input and lock fingerprints match")
        return
    with tempfile.TemporaryDirectory() as directory:
        destination = Path(directory)
        metadata = generate(destination)
        (destination / METADATA).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        names = (*LOCKS, METADATA)
        if mode == "check":
            changed = [name for name in names if (destination / name).read_bytes() != (DIRECTORY / name).read_bytes()]
            if changed:
                raise ValueError("Lock regeneration differs: " + ", ".join(changed))
            print("Lock regeneration: zero diff")
        else:
            # Replace completed files atomically; never truncate a reviewed lock.
            with tempfile.TemporaryDirectory(dir=DIRECTORY) as staging:
                for name in names:
                    temporary = Path(staging) / name
                    shutil.copyfile(destination / name, temporary)
                    temporary.replace(DIRECTORY / name)
            print("Compiled base.lock and dev.lock; reviewed updates must include lock-metadata.json")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, FileNotFoundError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        sys.exit(1)
