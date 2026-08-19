"""In-memory container and exec session store."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ContainerState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    EXITED = "exited"
    DEAD = "dead"
    REMOVED = "removed"


@dataclass
class ContainerRecord:
    id: str
    name: str
    image: str
    state: ContainerState = ContainerState.CREATED
    created: float = field(default_factory=time.time)
    env: dict[str, str] = field(default_factory=dict)
    cmd: list[str] = field(default_factory=list)
    working_dir: str = "/"
    namespace: str = ""
    kubeconfig: str | None = None
    kube_context: str | None = None
    image_pull_secrets: list[str] = field(default_factory=list)
    ready_timeout: int = 600
    pod_timeout: str = "2h"
    # Create-time stdio / TTY (used by attach and inspect)
    tty: bool = False
    attach_stdin: bool = False
    attach_stdout: bool = True
    attach_stderr: bool = True
    open_stdin: bool = False
    stdin_once: bool = False
    # Docker -v / HostConfig.Binds → Pod hostPath mounts
    binds: list[str] = field(default_factory=list)
    # HostConfig.Mounts / Tmpfs (Compose)
    mounts: list[dict[str, Any]] = field(default_factory=list)
    tmpfs: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    networking_config: dict[str, Any] = field(default_factory=dict)
    # Docker -p / HostConfig.PortBindings
    port_bindings: dict[str, Any] = field(default_factory=dict)
    exposed_ports: dict[str, Any] = field(default_factory=dict)
    publish_all_ports: bool = False
    # Runtime published maps (HostPort may be allocated)
    # Docker --add-host / HostConfig.ExtraHosts
    extra_hosts: list[dict[str, str]] = field(default_factory=list)
    published_ports: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    port_forwarder: Any = None
    # ClusterIP Service for compose DNS
    k8s_service_name: str | None = None
    # Pod resources (K8s quantities, e.g. 8Gi / 2)
    memory_limit: str | None = None
    memory_request: str | None = None
    cpu_limit: str | None = None
    cpu_request: str | None = None
    gpu: str | None = None
    gpu_card: str | None = None
    # Runtime (set after start)
    kube_env: Any = None
    sandbox_id: str | None = None
    sandbox_status: str | None = None
    pod_name: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def short_id(self) -> str:
        return self.id[:12]


@dataclass
class ExecRecord:
    id: str
    container_id: str
    cmd: list[str]
    attach_stdin: bool = False
    attach_stdout: bool = True
    attach_stderr: bool = True
    tty: bool = False
    working_dir: str = ""
    env: list[str] = field(default_factory=list)
    running: bool = False
    exit_code: int | None = None
    created: float = field(default_factory=time.time)


def _docker_id() -> str:
    return hashlib.sha256(uuid.uuid4().bytes).hexdigest()


class ContainerStore:
    """Process-local store for containers and exec sessions."""

    def __init__(self) -> None:
        self._containers: dict[str, ContainerRecord] = {}
        self._names: dict[str, str] = {}  # name -> id
        self._execs: dict[str, ExecRecord] = {}
        self._extra_images: set[str] = set()
        # short/local image name → pullable registry ref (from buildctl)
        self._image_aliases: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_container(
        self,
        *,
        name: str | None,
        image: str,
        env: dict[str, str],
        cmd: list[str],
        working_dir: str,
        namespace: str,
        kubeconfig: str | None = None,
        kube_context: str | None = None,
        image_pull_secrets: list[str] | None = None,
        ready_timeout: int = 600,
        pod_timeout: str = "2h",
        tty: bool = False,
        attach_stdin: bool = False,
        attach_stdout: bool = True,
        attach_stderr: bool = True,
        open_stdin: bool = False,
        stdin_once: bool = False,
        binds: list[str] | None = None,
        mounts: list[dict[str, Any]] | None = None,
        tmpfs: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        networking_config: dict[str, Any] | None = None,
        port_bindings: dict[str, Any] | None = None,
        exposed_ports: dict[str, Any] | None = None,
        publish_all_ports: bool = False,
        extra_hosts: list[dict[str, str]] | None = None,
        memory_limit: str | None = None,
        memory_request: str | None = None,
        cpu_limit: str | None = None,
        cpu_request: str | None = None,
        gpu: str | None = None,
        gpu_card: str | None = None,
    ) -> ContainerRecord:
        cid = _docker_id()
        cname = name or f"docker-rt-{cid[:12]}"
        if cname.startswith("/"):
            cname = cname[1:]

        async with self._lock:
            if cname in self._names:
                raise KeyError(f"Conflict: name {cname!r} already in use")
            record = ContainerRecord(
                id=cid,
                name=cname,
                image=image,
                env=env,
                cmd=cmd,
                working_dir=working_dir or "/",
                namespace=namespace,
                kubeconfig=kubeconfig,
                kube_context=kube_context,
                image_pull_secrets=image_pull_secrets or [],
                ready_timeout=ready_timeout,
                pod_timeout=pod_timeout,
                tty=tty,
                attach_stdin=attach_stdin,
                attach_stdout=attach_stdout,
                attach_stderr=attach_stderr,
                open_stdin=open_stdin,
                stdin_once=stdin_once,
                binds=list(binds or []),
                mounts=list(mounts or []),
                tmpfs=dict(tmpfs or {}),
                labels=dict(labels or {}),
                networking_config=dict(networking_config or {}),
                port_bindings=dict(port_bindings or {}),
                exposed_ports=dict(exposed_ports or {}),
                publish_all_ports=bool(publish_all_ports),
                extra_hosts=list(extra_hosts or []),
                memory_limit=memory_limit,
                memory_request=memory_request,
                cpu_limit=cpu_limit,
                cpu_request=cpu_request,
                gpu=gpu,
                gpu_card=gpu_card,
            )
            self._containers[cid] = record
            self._names[cname] = cid
            return record

    def get(self, id_or_name: str) -> ContainerRecord | None:
        if id_or_name in self._containers:
            return self._containers[id_or_name]
        # Prefix match on id
        matches = [c for c in self._containers.values() if c.id.startswith(id_or_name)]
        if len(matches) == 1:
            return matches[0]
        for c in self._containers.values():
            kube_env = getattr(c, "kube_env", None)
            sandbox_id = getattr(kube_env, "sandbox_id", None)
            if sandbox_id and (
                sandbox_id == id_or_name or sandbox_id.startswith(id_or_name)
            ):
                return c
        # Name (with or without leading /)
        key = id_or_name[1:] if id_or_name.startswith("/") else id_or_name
        cid = self._names.get(key)
        if cid:
            return self._containers.get(cid)
        return None

    def list(
        self, *, all_containers: bool = False
    ) -> list[ContainerRecord]:
        out: list[ContainerRecord] = []
        for c in self._containers.values():
            if c.state == ContainerState.REMOVED:
                continue
            if all_containers or c.state == ContainerState.RUNNING:
                out.append(c)
        return out

    def known_images(self) -> set[str]:
        return (
            {c.image for c in self._containers.values() if c.image}
            | set(self._extra_images)
            | set(self._image_aliases.keys())
            | set(self._image_aliases.values())
        )

    def register_image(self, name: str) -> None:
        if name:
            self._extra_images.add(name)

    def register_image_alias(self, short: str, pullable: str) -> None:
        """Map a Compose/local tag to a registry pullable ref."""
        if short and pullable:
            self._image_aliases[short] = pullable
            self._extra_images.add(short)
            self._extra_images.add(pullable)

    def resolve_image(self, name: str) -> str:
        """Resolve build aliases to the pullable registry ref."""
        if not name:
            return name
        if name in self._image_aliases:
            return self._image_aliases[name]
        # Also try without implicit :latest
        if ":" not in name.rsplit("/", 1)[-1]:
            alt = f"{name}:latest"
            if alt in self._image_aliases:
                return self._image_aliases[alt]
        return name

    def unregister_image(self, name: str) -> bool:
        """Drop a stub-registered image. Returns True if it was known."""
        known = False
        if name in self._extra_images:
            self._extra_images.discard(name)
            known = True
        # Drop aliases pointing to or from this name
        drop = [k for k, v in self._image_aliases.items() if k == name or v == name]
        for k in drop:
            self._image_aliases.pop(k, None)
            known = True
        return known or name in {c.image for c in self._containers.values() if c.image}

    async def adopt_container(
        self,
        *,
        container_id: str,
        name: str,
        image: str,
        namespace: str,
        pod_name: str,
        kube_env: Any,
        port_bindings: dict[str, Any] | None = None,
        exposed_ports: dict[str, Any] | None = None,
        publish_all_ports: bool = False,
        state: ContainerState | None = None,
    ) -> ContainerRecord:
        """Re-register a running Pod after daemon restart."""
        async with self._lock:
            # Drop conflicting name mapping if needed
            old = self._names.get(name)
            if old and old != container_id:
                raise KeyError(f"Conflict: name {name!r} already in use")
            record = ContainerRecord(
                id=container_id,
                name=name,
                image=image,
                state=state or ContainerState.RUNNING,
                namespace=namespace,
                pod_name=pod_name,
                kube_env=kube_env,
                started_at=time.time(),
                port_bindings=dict(port_bindings or {}),
                exposed_ports=dict(exposed_ports or {}),
                publish_all_ports=bool(publish_all_ports),
            )
            self._containers[container_id] = record
            self._names[name] = container_id
            return record

    async def rename(self, record: ContainerRecord, new_name: str) -> None:
        if new_name.startswith("/"):
            new_name = new_name[1:]
        async with self._lock:
            if new_name in self._names and self._names[new_name] != record.id:
                raise KeyError(f"Conflict: name {new_name!r} already in use")
            self._names.pop(record.name, None)
            record.name = new_name
            self._names[new_name] = record.id

    async def remove(self, record: ContainerRecord) -> None:
        async with self._lock:
            self._names.pop(record.name, None)
            record.state = ContainerState.REMOVED
            # Keep record briefly for inspect-after-rm edge cases; drop hard ref
            self._containers.pop(record.id, None)
            # Drop exec sessions for this container.
            dead = [
                eid
                for eid, ex in self._execs.items()
                if ex.container_id == record.id
            ]
            for eid in dead:
                self._execs.pop(eid, None)

    def create_exec(
        self,
        *,
        container_id: str,
        cmd: list[str],
        attach_stdin: bool,
        attach_stdout: bool,
        attach_stderr: bool,
        tty: bool,
        working_dir: str = "",
        env: list[str] | None = None,
    ) -> ExecRecord:
        eid = _docker_id()
        rec = ExecRecord(
            id=eid,
            container_id=container_id,
            cmd=cmd,
            attach_stdin=attach_stdin,
            attach_stdout=attach_stdout,
            attach_stderr=attach_stderr,
            tty=tty,
            working_dir=working_dir,
            env=env or [],
        )
        self._execs[eid] = rec
        return rec

    def get_exec(self, exec_id: str) -> ExecRecord | None:
        if exec_id in self._execs:
            return self._execs[exec_id]
        matches = [e for e in self._execs.values() if e.id.startswith(exec_id)]
        if len(matches) == 1:
            return matches[0]
        return None

    def prune_execs(
        self,
        *,
        max_finished: int = 128,
        max_age_s: float = 600.0,
    ) -> int:
        """Drop finished/abandoned exec records. Returns number removed."""
        now = time.time()
        removed = 0
        for eid, ex in list(self._execs.items()):
            if ex.running:
                continue
            aged = (now - float(ex.created)) > max_age_s
            finished = ex.exit_code is not None
            if aged and (finished or not ex.running):
                self._execs.pop(eid, None)
                removed += 1
        finished = [
            (eid, ex)
            for eid, ex in self._execs.items()
            if ex.exit_code is not None and not ex.running
        ]
        if len(finished) > max_finished:
            finished.sort(key=lambda t: t[1].created)
            for eid, _ in finished[: len(finished) - max_finished]:
                if self._execs.pop(eid, None) is not None:
                    removed += 1
        return removed
