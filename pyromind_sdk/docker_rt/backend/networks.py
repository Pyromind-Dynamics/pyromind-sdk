"""In-memory Docker Network API stubs (enough for Compose create/connect)."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _net_id() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


@dataclass
class NetworkEndpoint:
    container_id: str
    ipv4: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class NetworkRecord:
    id: str
    name: str
    driver: str = "bridge"
    labels: dict[str, str] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    endpoints: dict[str, NetworkEndpoint] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:12]


class NetworkStore:
    """Process-local network registry (no real isolation)."""

    def __init__(self) -> None:
        self._networks: dict[str, NetworkRecord] = {}
        self._names: dict[str, str] = {}
        # Ensure a default bridge exists (docker info / compose expects it).
        self._ensure_bridge()

    def _ensure_bridge(self) -> None:
        if "bridge" in self._names:
            return
        nid = _net_id()
        rec = NetworkRecord(id=nid, name="bridge", driver="bridge")
        self._networks[nid] = rec
        self._names["bridge"] = nid

    def create(
        self,
        *,
        name: str,
        driver: str = "bridge",
        labels: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
        check_duplicate: bool = True,
    ) -> NetworkRecord:
        if not name:
            raise ValueError("network name is required")
        if check_duplicate and name in self._names:
            raise KeyError(f"network with name {name!r} already exists")
        if name in self._names:
            return self._networks[self._names[name]]
        nid = _net_id()
        rec = NetworkRecord(
            id=nid,
            name=name,
            driver=driver or "bridge",
            labels=dict(labels or {}),
            options=dict(options or {}),
        )
        self._networks[nid] = rec
        self._names[name] = nid
        return rec

    def get(self, id_or_name: str) -> NetworkRecord | None:
        if id_or_name in self._networks:
            return self._networks[id_or_name]
        matches = [n for n in self._networks.values() if n.id.startswith(id_or_name)]
        if len(matches) == 1:
            return matches[0]
        nid = self._names.get(id_or_name)
        if nid:
            return self._networks.get(nid)
        return None

    def list(self) -> list[NetworkRecord]:
        return list(self._networks.values())

    def remove(self, id_or_name: str) -> NetworkRecord:
        rec = self.get(id_or_name)
        if rec is None:
            raise KeyError(f"network {id_or_name!r} not found")
        if rec.name == "bridge":
            raise ValueError("cannot remove default bridge network")
        self._networks.pop(rec.id, None)
        self._names.pop(rec.name, None)
        return rec

    def connect(
        self,
        network_id: str,
        *,
        container_id: str,
        aliases: list[str] | None = None,
        ipv4: str = "",
    ) -> None:
        rec = self.get(network_id)
        if rec is None:
            raise KeyError(f"network {network_id!r} not found")
        rec.endpoints[container_id] = NetworkEndpoint(
            container_id=container_id,
            ipv4=ipv4 or "",
            aliases=list(aliases or []),
        )

    def disconnect(self, network_id: str, *, container_id: str) -> None:
        rec = self.get(network_id)
        if rec is None:
            raise KeyError(f"network {network_id!r} not found")
        rec.endpoints.pop(container_id, None)

    def disconnect_container(self, container_id: str) -> None:
        for rec in self._networks.values():
            rec.endpoints.pop(container_id, None)


def to_network_inspect(rec: NetworkRecord) -> dict[str, Any]:
    containers: dict[str, Any] = {}
    for eid, ep in rec.endpoints.items():
        containers[eid] = {
            "Name": eid[:12],
            "EndpointID": eid[:12],
            "MacAddress": "",
            "IPv4Address": ep.ipv4,
            "IPv6Address": "",
        }
    return {
        "Name": rec.name,
        "Id": rec.id,
        "Created": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(rec.created)),
        "Scope": "local",
        "Driver": rec.driver,
        "EnableIPv6": False,
        "IPAM": {
            "Driver": "default",
            "Options": None,
            "Config": [{"Subnet": "172.18.0.0/16", "Gateway": "172.18.0.1"}],
        },
        "Internal": False,
        "Attachable": True,
        "Ingress": False,
        "ConfigFrom": {"Network": ""},
        "ConfigOnly": False,
        "Containers": containers,
        "Options": dict(rec.options),
        "Labels": dict(rec.labels),
    }


def to_network_list_item(rec: NetworkRecord) -> dict[str, Any]:
    return {
        "Name": rec.name,
        "Id": rec.id,
        "Created": time.strftime("%Y-%m-%dT%H:%M:%S.000000000Z", time.gmtime(rec.created)),
        "Scope": "local",
        "Driver": rec.driver,
        "EnableIPv6": False,
        "IPAM": {"Driver": "default", "Options": None, "Config": []},
        "Internal": False,
        "Attachable": True,
        "Ingress": False,
        "ConfigFrom": {"Network": ""},
        "ConfigOnly": False,
        "Containers": {},
        "Options": dict(rec.options),
        "Labels": dict(rec.labels),
    }
