"""Explicit Docker IPv4 subnet contracts shared by dev and disposable tests."""

from __future__ import annotations

import hashlib
import ipaddress
import json


class NetworkError(ValueError):
    """Invalid or unavailable explicit network configuration."""


def validate_subnet(value: str, *, kind: str = "dev") -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as error:
        raise NetworkError("Subnet must be a canonical IPv4 network with /24 prefix") from error
    if network.version != 4 or network.prefixlen != 24 or str(network) != value:
        raise NetworkError("Subnet must be a canonical IPv4 network with /24 prefix")
    reserved = ["198.18.0.0/24", "198.18.128.0/17"]
    reserved.append("198.19.0.0/16" if kind == "dev" else "198.18.16.0/20")
    if any(network.overlaps(ipaddress.ip_network(zone)) for zone in reserved):
        raise NetworkError("Subnet overlaps a reserved host/project/disposable pool")
    return str(network)


def candidates(identity: str, *, kind: str = "dev") -> list[str]:
    count = 16 if kind == "dev" else 256
    slot = int(hashlib.sha256(identity.encode()).hexdigest(), 16) % count
    prefix, offset = ("198.18", 16) if kind == "dev" else ("198.19", 0)
    return [f"{prefix}.{offset + (slot + step) % count}.0/24" for step in range(count)]


def inventory(run) -> list[dict]:
    identifiers = run(["docker", "network", "ls", "-q"]).stdout.split()
    if not identifiers:
        return []
    # A concurrent removal fails closed; never mistake an incomplete inventory for free space.
    return json.loads(run(["docker", "network", "inspect", *identifiers]).stdout)


def overlapping(subnet: str, networks: list[dict], *, exclude_name: str | None = None) -> bool:
    candidate = ipaddress.ip_network(subnet)
    for network in networks:
        if exclude_name is not None and network.get("Name") == exclude_name:
            continue
        for config in network.get("IPAM", {}).get("Config") or []:
            if value := config.get("Subnet"):
                occupied = ipaddress.ip_network(value)
                if occupied.version == candidate.version and candidate.overlaps(occupied):
                    return True
    return False
