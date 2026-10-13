"""Compatibility regressions for checkout resource ownership admission."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from infra.scripts.dev_environment import Commands, DevError, configure


ROOT = Path(__file__).resolve().parents[3]


def _commands(tmp_path):
    shutil.copyfile(ROOT / ".env.dev.example", tmp_path / ".env.dev.example")
    configure(tmp_path)
    commands = Commands(tmp_path)
    calls = []
    responses = {}

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[:4] == ["docker", "ps", "-aq", "--filter"]:
            return subprocess.CompletedProcess(argv, 0, "container-a container-b\n", "")
        response = responses[tuple(argv)]
        if isinstance(response, subprocess.CompletedProcess):
            return response
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    commands.run = run
    return commands, calls, responses


def _container(identifier, owner):
    return {"Id": identifier, "Config": {"Labels": {"complect.checkout_id": owner}}}


def test_own_resources_batches_project_container_inspect_and_allows_missing_named_resources(
    tmp_path,
):
    commands, calls, responses = _commands(tmp_path)
    owner = commands.values["DEV_CHECKOUT_ID"]
    responses.update(
        {
            ("docker", "inspect", "container-a", "container-b"): [
                _container("container-a", owner),
                _container("container-b", owner),
            ],
            ("docker", "volume", "inspect", "checkout-data"):
                subprocess.CompletedProcess(
                    ["docker", "volume", "inspect", "checkout-data"],
                    1,
                    "",
                    "missing",
                ),
            ("docker", "network", "inspect", "checkout-network"):
                subprocess.CompletedProcess(
                    ["docker", "network", "inspect", "checkout-network"],
                    1,
                    "",
                    "missing",
                ),
        }
    )

    commands.own_resources(
        {
            "volumes": {"data": {"name": "checkout-data"}},
            "networks": {"default": {"name": "checkout-network"}},
        }
    )

    assert [entry[0] for entry in calls] == [
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={commands.project}",
        ],
        ["docker", "inspect", "container-a", "container-b"],
        ["docker", "volume", "inspect", "checkout-data"],
        ["docker", "network", "inspect", "checkout-network"],
    ]
    assert all(not kwargs.get("write", False) for _, kwargs in calls)


def test_own_resources_preserves_foreign_container_diagnostic_and_command_boundary(
    tmp_path,
):
    commands, calls, responses = _commands(tmp_path)
    owner = commands.values["DEV_CHECKOUT_ID"]
    responses[("docker", "inspect", "container-a", "container-b")] = [
        _container("container-a", owner),
        _container("container-b", "foreign-owner"),
    ]

    with pytest.raises(
        DevError,
        match=(
            "^Project contains containers owned by another checkout; "
            "refusing to touch them$"
        ),
    ):
        commands.own_resources({})

    assert [entry[0] for entry in calls] == [
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={commands.project}",
        ],
        ["docker", "inspect", "container-a", "container-b"],
    ]


@pytest.mark.parametrize(
    ("kind", "field", "name"),
    [
        ("volume", "volumes", "checkout-data"),
        ("network", "networks", "checkout-network"),
    ],
)
def test_own_resources_preserves_foreign_named_resource_diagnostic_without_effects(
    tmp_path, kind, field, name
):
    commands, calls, responses = _commands(tmp_path)
    owner = commands.values["DEV_CHECKOUT_ID"]
    responses[("docker", "inspect", "container-a", "container-b")] = [
        _container("container-a", owner),
        _container("container-b", owner),
    ]
    responses[("docker", kind, "inspect", name)] = [
        {"Name": name, "Labels": {"complect.checkout_id": "foreign-owner"}}
    ]

    with pytest.raises(
        DevError,
        match=rf"^Existing {kind} belongs to another or unmanaged checkout: {name}$",
    ):
        commands.own_resources({field: {"resource": {"name": name}}})

    assert [entry[0] for entry in calls] == [
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={commands.project}",
        ],
        ["docker", "inspect", "container-a", "container-b"],
        ["docker", kind, "inspect", name],
    ]
    assert all(not kwargs.get("write", False) for _, kwargs in calls)
