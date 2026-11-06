"""In-memory Docker Volume API store."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _volume_id() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


@dataclass
class VolumeRecord:
    name: str
    driver: str = "local"
    labels: dict[str, str] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    anonymous: bool = False
    id: str = field(default_factory=_volume_id)

    @property
    def mountpoint(self) -> str:
        # Informational only: the data lives in the sandbox, not on this host.
        return f"/var/lib/docker-rt/volumes/{self.name}/_data"


class VolumeStore:
    """Process-local named volume registry."""

    def __init__(self) -> None:
        self._volumes: dict[str, VolumeRecord] = {}

    def create(
        self,
        *,
        name: str | None = None,
        driver: str = "local",
        labels: dict[str, str] | None = None,
        options: dict[str, str] | None = None,
    ) -> VolumeRecord:
        labels = dict(labels or {})
        anonymous = labels.get("com.docker.volume.anonymous", "").lower() in {
            "1",
            "true",
            "yes",
        }
        vname = name or f"anonymous-{_volume_id()[:12]}"
        if vname in self._volumes:
            raise KeyError(f"volume {vname!r} already exists")
        rec = VolumeRecord(
            name=vname,
            driver=driver or "local",
            labels=labels,
            options=dict(options or {}),
            anonymous=anonymous,
        )
        self._volumes[vname] = rec
        return rec

    def get(self, name: str) -> VolumeRecord | None:
        return self._volumes.get(name)

    def list(self) -> list[VolumeRecord]:
        return list(self._volumes.values())

    def remove(self, name: str, *, force: bool = False) -> VolumeRecord:
        rec = self._volumes.get(name)
        if rec is None:
            raise KeyError(f"No such volume: {name}")
        # force unused: we do not track container refs yet
        _ = force
        del self._volumes[name]
        return rec

    def is_anonymous(self, name: str) -> bool:
        rec = self._volumes.get(name)
        if rec is None:
            return False
        return bool(rec.anonymous)


def to_volume_inspect(rec: VolumeRecord) -> dict[str, Any]:
    return {
        "CreatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec.created)),
        "Driver": rec.driver,
        "Labels": dict(rec.labels),
        "Mountpoint": rec.mountpoint,
        "Name": rec.name,
        "Options": dict(rec.options),
        "Scope": "local",
    }


def to_volume_list(recs: list[VolumeRecord]) -> dict[str, Any]:
    return {
        "Volumes": [to_volume_inspect(r) for r in recs] or None,
        "Warnings": None,
    }
