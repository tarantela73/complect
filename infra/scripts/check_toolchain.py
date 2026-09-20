"""Check exact container pins separately from host compatibility diagnostics."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST_TOOLS = ("docker", "docker_compose", "git", "bash", "make", "jq")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def host_version_diagnostic(name: str, observed: str, specification: dict) -> str:
    require(bool(observed), f"Missing observed version for {name}")
    tested = specification.get("tested_version")
    if tested and observed.removeprefix("v") != tested:
        return f"WARN: {name} {observed} differs from tested {tested}; compatibility is not disproved"
    return f"PASS: {name} {observed} (presence/tested baseline, not an exact version gate)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-version", action="append", default=[], metavar="TOOL=VERSION")
    arguments = parser.parse_args()
    manifest = json.loads((ROOT / "toolchain.json").read_text())
    dockerfile = (ROOT / "backend/Dockerfile.dev").read_text()
    compose = (ROOT / "docker-compose.dev.yml").read_text()
    require(f"FROM {manifest['python']['image']} AS toolchain" in dockerfile, "Python image drift")
    require(f"uv=={manifest['uv']['version']}" in dockerfile, "uv version drift")
    require(f"image: {manifest['node']['image']}" in compose, "Node image drift")
    require(
        "command: [node, /opt/complect/npm_install.mjs]" in compose
        and "['ci'," in (ROOT / "infra/scripts/npm_install.mjs").read_text(),
        "Container must use npm ci",
    )
    require(manifest["target"]["os"] == "linux" and manifest["target"]["architecture"] == "amd64", "Target drift")
    for name in ("python", "node", "npm", "uv"):
        require(manifest[name]["version_policy"] == "exact", f"{name} must have exact container/build pins")
        require(manifest[name]["scope"] == "container", f"{name} pin must not constrain the host")
    for name in HOST_TOOLS:
        specification = manifest["external_tools"][name]
        require(specification["required"] and specification["version_policy"] == "presence", f"{name} policy drift")
        require("version" not in specification, f"{name}: tested versions must not become exact host pins")
    for folder in ("frontend", "frontend/e2e"):
        package = json.loads((ROOT / folder / "package.json").read_text())
        lock = json.loads((ROOT / folder / "package-lock.json").read_text())
        require(package.get("engines", {}) == lock["packages"][""].get("engines", {}), f"{folder} metadata drift")
        npmrc = ROOT / folder / ".npmrc"
        if npmrc.exists():
            strict = re.search(
                r"^\s*engine-strict\s*(?:=\s*true)?\s*$", npmrc.read_text(), re.MULTILINE | re.IGNORECASE
            )
            require(strict is None, f"{folder}: candidate must not hard-fail transitional host E2E")
    metadata = json.loads((ROOT / "backend/requirements/lock-metadata.json").read_text())
    require(metadata["python"] == manifest["python"]["version"], "Lock Python drift")
    require(metadata["uv"] == manifest["uv"]["version"], "Lock uv drift")
    require(metadata["python_platform"] == manifest["target"]["python_platform"], "Lock target drift")
    groups = {}
    for group in ("base", "dev"):
        text = (ROOT / f"backend/requirements/{group}.lock").read_text()
        groups[group] = dict(re.findall(r"^([\w.-]+)==([^\s;\\]+)", text, re.MULTILINE))
        require(bool(groups[group]), f"Empty {group} lock")
    require(groups["base"].items() <= groups["dev"].items(), "Runtime/dev lock versions differ")
    for value in arguments.host_version:
        name, separator, observed = value.partition("=")
        require(bool(separator) and name in HOST_TOOLS, "Expected a presence-policy TOOL=VERSION")
        print(host_version_diagnostic(name, observed, manifest["external_tools"][name]))
    print("Toolchain declarations and runtime/dev versions match")


if __name__ == "__main__":
    main()
