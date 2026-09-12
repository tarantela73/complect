"""Compile and verify requirements locks inside the pinned Docker toolchain."""

from __future__ import annotations

import hashlib
import importlib.metadata
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


def locked_versions(group: str) -> dict[str, str]:
    from pip._vendor.packaging.utils import canonicalize_name

    content = (DIRECTORY / f"{group}.lock").read_text()
    return {canonicalize_name(name): version for name, version in re.findall(r"^([\w.-]+)==([^\s;\\]+)", content, re.M)}


def input_requirements(group: str):
    from pip._vendor.packaging.requirements import Requirement

    pending = [f"{group}.txt"]
    visited = set()
    result = []
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        if name not in {"base.txt", "dev.txt"}:
            raise ValueError("Requirements include an unverified input path")
        visited.add(name)
        for line in (DIRECTORY / name).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-r "):
                pending.append(line[3:].strip())
            else:
                requirement = Requirement(line)
                if requirement.url:
                    raise ValueError("Direct URL inputs require a separately reviewed lock policy")
                result.append(requirement)
    return result


def verify_root_constraints() -> None:
    from pip._vendor.packaging.utils import canonicalize_name

    for group in ("base", "dev"):
        versions = locked_versions(group)
        for requirement in input_requirements(group):
            if requirement.marker and not requirement.marker.evaluate():
                continue
            name = canonicalize_name(requirement.name)
            if name not in versions:
                raise ValueError(f"Missing locked requirement: {name}")
            if not requirement.specifier.contains(versions[name], prereleases=True):
                raise ValueError(f"Locked version does not satisfy input: {name}")


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
    verify_root_constraints()


def verify_installed() -> None:
    """Validate the installed dependency closure offline, including selected extras."""
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.utils import canonicalize_name

    verify_inputs()
    versions = locked_versions("dev")
    for name, version in versions.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError(f"Locked package not installed: {name}") from error
        if actual != version:
            raise ValueError(f"Installed version differs from lock: {name}")
    requirements = input_requirements("dev")
    visited: set[tuple[str, tuple[str, ...]]] = set()
    while requirements:
        requirement = requirements.pop()
        name = canonicalize_name(requirement.name)
        if name not in versions:
            raise ValueError(f"Missing locked dependency: {name}")
        if not requirement.specifier.contains(versions[name], prereleases=True):
            raise ValueError(f"Locked transitive version violates constraint: {name}")
        extras = tuple(sorted(requirement.extras))
        state = (name, extras)
        if state in visited:
            continue
        visited.add(state)
        for value in importlib.metadata.requires(name) or []:
            dependency = Requirement(value)
            if dependency.marker is None or any(
                dependency.marker.evaluate({"extra": extra}) for extra in ("", *extras)
            ):
                requirements.append(dependency)
    installed = {canonicalize_name(package.metadata["Name"]) for package in importlib.metadata.distributions()}
    unexpected = installed - set(versions) - {"pip", "uv"}
    if unexpected:
        raise ValueError("Unexpected installed packages: " + ", ".join(sorted(unexpected)))
    print("Installed package versions and transitive closure match the reviewed lock (offline)")


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
        "--python-version",
        python,
        "--python-platform",
        target,
        "--generate-hashes",
        "--no-header",
        "--no-annotate",
        "--default-index",
        "https://pypi.org/simple",
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
    if len(sys.argv) != 2 or sys.argv[1] not in {"compile", "check", "check-inputs", "verify-installed"}:
        raise ValueError("Usage: lock.py compile|check|check-inputs|verify-installed")
    mode = sys.argv[1]
    if mode == "verify-installed":
        verify_installed()
        return
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
