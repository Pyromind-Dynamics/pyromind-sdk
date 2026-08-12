"""Docker ``-p`` / ``--publish`` → local TCP proxy to the Pod.

Two backends:

* **direct** — TCP to ``PodIP:containerPort`` (needs Pod CIDR reachability).
* **api** — Kubernetes apiserver port-forward websocket (only needs kube API).

``DOCKER_RT_PORT_FORWARD_MODE``: ``direct`` | ``api`` | ``auto`` (default).
``auto`` probes whether the Pod IP is reachable; if not, uses ``api``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("docker_rt.portforward")

ANNOTATION_PORT_BINDINGS = "docker-rt.port-bindings"
ANNOTATION_EXPOSED_PORTS = "docker-rt.exposed-ports"
ANNOTATION_PUBLISH_ALL = "docker-rt.publish-all-ports"


@dataclass(frozen=True)
class PortMapping:
    """One host→container TCP mapping (before or after bind)."""

    container_port: int
    host_ip: str = "0.0.0.0"
    host_port: int | None = None  # None → allocate ephemeral
    protocol: str = "tcp"
    # Actual connect target on pod_ip (defaults to container_port).
    target_port: int | None = None

    @property
    def connect_port(self) -> int:
        return self.target_port if self.target_port is not None else self.container_port


@dataclass
class PublishedBinding:
    """Runtime binding shown in inspect NetworkSettings.Ports."""

    container_port: int
    host_ip: str
    host_port: int
    protocol: str = "tcp"

    def docker_key(self) -> str:
        return f"{self.container_port}/{self.protocol}"

    def as_docker_entry(self) -> dict[str, str]:
        return {"HostIp": self.host_ip, "HostPort": str(self.host_port)}


def resolve_port_forward_mode(raw: str | None = None) -> str:
    """Return ``direct``, ``api``, or ``auto``."""
    text = (raw if raw is not None else os.getenv("DOCKER_RT_PORT_FORWARD_MODE", "auto"))
    text = str(text or "auto").strip().lower()
    if text in {"direct", "api", "auto"}:
        return text
    logger.warning("unknown DOCKER_RT_PORT_FORWARD_MODE=%r; using auto", text)
    return "auto"


def serialize_port_bindings(bindings: dict[str, Any] | None) -> str:
    return json.dumps(bindings or {}, separators=(",", ":"), sort_keys=True)


def deserialize_port_bindings(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def serialize_exposed_ports(exposed: dict[str, Any] | list[str] | None) -> str:
    if isinstance(exposed, list):
        return json.dumps(sorted(exposed), separators=(",", ":"))
    if isinstance(exposed, dict):
        return json.dumps(sorted(exposed.keys()), separators=(",", ":"))
    return "[]"


def deserialize_exposed_ports(raw: str | None) -> dict[str, dict]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(data, list):
        return {str(k): {} for k in data}
    if isinstance(data, dict):
        return {str(k): {} for k in data}
    return {}


def parse_port_key(key: str) -> tuple[int, str] | None:
    """Parse ``80/tcp`` → (80, 'tcp'). Returns None for unsupported protocols."""
    text = str(key).strip().lower()
    if "/" in text:
        port_s, _, proto = text.partition("/")
        proto = proto or "tcp"
    else:
        port_s, proto = text, "tcp"
    if proto != "tcp":
        return None
    try:
        port = int(port_s)
    except ValueError:
        raise ValueError(f"invalid port key: {key!r}") from None
    if not (1 <= port <= 65535):
        raise ValueError(f"port out of range: {key!r}")
    return port, proto


def parse_publish_spec(
    *,
    port_bindings: dict[str, Any] | None = None,
    exposed_ports: dict[str, Any] | None = None,
    publish_all_ports: bool = False,
) -> list[PortMapping]:
    """Normalize Docker PortBindings / ExposedPorts / PublishAllPorts to mappings.

    UDP entries are skipped. Invalid TCP keys raise ``ValueError``.
    """
    mappings: list[PortMapping] = []
    seen_container: set[int] = set()

    for key, hosts in (port_bindings or {}).items():
        parsed = parse_port_key(key)
        if parsed is None:
            logger.info("ignoring non-tcp port binding %s", key)
            continue
        cport, proto = parsed
        host_list = hosts if isinstance(hosts, list) else [hosts]
        if not host_list:
            host_list = [{}]
        for host in host_list:
            host = host or {}
            if not isinstance(host, dict):
                raise ValueError(f"invalid PortBindings entry for {key}: {host!r}")
            host_ip = str(host.get("HostIp") or "0.0.0.0").strip() or "0.0.0.0"
            raw_hp = host.get("HostPort", "")
            if raw_hp is None or str(raw_hp).strip() == "":
                host_port: int | None = None
            else:
                try:
                    host_port = int(str(raw_hp).strip())
                except ValueError as exc:
                    raise ValueError(
                        f"invalid HostPort for {key}: {raw_hp!r}"
                    ) from exc
                if not (1 <= host_port <= 65535):
                    raise ValueError(f"HostPort out of range for {key}: {host_port}")
            mappings.append(
                PortMapping(
                    container_port=cport,
                    host_ip=host_ip,
                    host_port=host_port,
                    protocol=proto,
                )
            )
            seen_container.add(cport)

    if publish_all_ports:
        for key in exposed_ports or {}:
            parsed = parse_port_key(key)
            if parsed is None:
                continue
            cport, proto = parsed
            if cport in seen_container:
                continue
            mappings.append(
                PortMapping(
                    container_port=cport,
                    host_ip="0.0.0.0",
                    host_port=None,
                    protocol=proto,
                )
            )
            seen_container.add(cport)

    return mappings


def published_to_network_settings(
    published: list[PublishedBinding],
) -> dict[str, list[dict[str, str]] | None]:
    """Docker NetworkSettings.Ports shape."""
    out: dict[str, list[dict[str, str]] | None] = {}
    for b in published:
        key = b.docker_key()
        out.setdefault(key, []).append(b.as_docker_entry())
    return out


def published_to_list_ports(
    published: list[PublishedBinding],
) -> list[dict[str, Any]]:
    """Docker ``ps`` Ports array."""
    rows: list[dict[str, Any]] = []
    for b in published:
        rows.append(
            {
                "IP": b.host_ip,
                "PrivatePort": b.container_port,
                "PublicPort": b.host_port,
                "Type": b.protocol,
            }
        )
    return rows


async def probe_pod_network(pod_ip: str, port: int, *, timeout: float = 1.0) -> bool:
    """Return True if this host can route TCP to ``pod_ip`` (CIDR reachable).

    ``ConnectionRefusedError`` still counts as reachable (path OK, nothing listening).
    Timeouts / network-unreachable mean we should use apiserver port-forward.
    """
    if not pod_ip or not (1 <= port <= 65535):
        return False
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(pod_ip, port),
            timeout=timeout,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except ConnectionRefusedError:
        return True
    except (asyncio.TimeoutError, OSError, ConnectionError):
        return False


def open_kube_portforward(kube_env: Any, container_port: int) -> tuple[Any, Any]:
    """Open an apiserver port-forward and return ``(pf, app_socket)``.

    Uses a fresh CoreV1Api so long-lived watches do not share the SSL pool.
    Caller must ``close_kube_portforward(pf)``.
    """
    from kubernetes.stream import portforward

    from .runtime import build_core_v1_api

    if not getattr(kube_env, "pod_name", None):
        raise RuntimeError("pod name is required for api port-forward")
    cfg = kube_env.config
    api = build_core_v1_api(
        kubeconfig=getattr(cfg, "kubeconfig", None),
        kube_context=getattr(cfg, "context", None),
    )
    pf = portforward(
        api.connect_get_namespaced_pod_portforward,
        kube_env.pod_name,
        cfg.namespace,
        ports=str(container_port),
    )
    # Keep ApiClient alive for the websocket lifetime.
    pf._docker_rt_api_client = api.api_client  # type: ignore[attr-defined]
    sock = pf.socket(container_port)
    return pf, sock


def close_kube_portforward(pf: Any) -> None:
    """Close port-forward websocket and its dedicated ApiClient."""
    try:
        pf.close()
    except Exception:
        pass
    client = getattr(pf, "_docker_rt_api_client", None)
    try:
        delattr(pf, "_docker_rt_api_client")
    except Exception:
        pass
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _bridge_to_blocking_socket(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_sock: Any,
) -> None:
    """Bidirectional copy between an asyncio client and a blocking socket."""
    loop = asyncio.get_running_loop()
    try:
        remote_sock.setblocking(True)
    except Exception:
        pass

    async def client_to_remote() -> None:
        try:
            while True:
                data = await client_reader.read(65536)
                if not data:
                    break
                await loop.run_in_executor(None, remote_sock.sendall, data)
        except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                remote_sock.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    async def remote_to_client() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, remote_sock.recv, 65536)
                if not data:
                    break
                client_writer.write(data)
                await client_writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                client_writer.close()
                await client_writer.wait_closed()
            except Exception:
                pass

    t1 = asyncio.create_task(client_to_remote())
    t2 = asyncio.create_task(remote_to_client())
    await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    for t in (t1, t2):
        if not t.done():
            t.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)


class PortForwarder:
    """Listen on host ports and proxy to the Pod (direct TCP or apiserver PF)."""

    def __init__(self) -> None:
        self._servers: list[asyncio.AbstractServer] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self.published: list[PublishedBinding] = []
        self._pod_ip: str | None = None
        self._kube_env: Any = None
        self._mode: str = "direct"
        self._stopped = False

    @property
    def running(self) -> bool:
        return bool(self._servers) and not self._stopped

    @property
    def mode(self) -> str:
        return self._mode

    async def start(
        self,
        pod_ip: str | None,
        mappings: list[PortMapping],
        *,
        kube_env: Any = None,
        mode: str = "direct",
    ) -> list[PublishedBinding]:
        mode = (mode or "direct").strip().lower()
        if mode not in {"direct", "api"}:
            raise ValueError(f"unsupported port-forward mode: {mode!r}")
        if mode == "direct" and not pod_ip:
            raise RuntimeError("pod IP is required for direct port forwarding")
        if mode == "api" and (
            kube_env is None or not getattr(kube_env, "pod_name", None)
        ):
            raise RuntimeError("kube_env with pod_name is required for api port-forward")
        if self._servers:
            await self.stop()
        self._stopped = False
        self._mode = mode
        self._pod_ip = pod_ip or None
        self._kube_env = kube_env
        self.published = []
        started: list[asyncio.AbstractServer] = []
        published: list[PublishedBinding] = []
        try:
            for m in mappings:
                binding, server = await self._listen_one(m)
                started.append(server)
                published.append(binding)
        except Exception:
            for srv in started:
                srv.close()
                await srv.wait_closed()
            raise
        self._servers = started
        self.published = published
        logger.info(
            "port forward active mode=%s pod=%s maps=%s",
            mode,
            pod_ip or getattr(kube_env, "pod_name", "?"),
            [
                f"{b.host_ip}:{b.host_port}->{b.container_port}/{b.protocol}"
                for b in published
            ],
        )
        return published

    async def _listen_one(
        self, mapping: PortMapping
    ) -> tuple[PublishedBinding, asyncio.AbstractServer]:
        host = mapping.host_ip or "0.0.0.0"
        port = mapping.host_port if mapping.host_port is not None else 0
        cport = mapping.container_port
        tport = mapping.connect_port

        async def _on_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await self._handle_client(reader, writer, tport)

        try:
            server = await asyncio.start_server(_on_client, host=host, port=port)
        except OSError as exc:
            raise RuntimeError(
                f"failed to bind {host}:{port or '<ephemeral>'} for "
                f"container port {cport}: {exc}"
            ) from exc

        socks = list(server.sockets or [])
        if not socks:
            server.close()
            await server.wait_closed()
            raise RuntimeError(f"no socket after bind for container port {cport}")
        bound_port = int(socks[0].getsockname()[1])
        binding = PublishedBinding(
            container_port=cport,
            host_ip=host,
            host_port=bound_port,
            protocol=mapping.protocol,
        )
        return binding, server

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        container_port: int,
    ) -> None:
        if self._stopped:
            client_writer.close()
            return
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)

            def _done(t: asyncio.Task[Any]) -> None:
                self._tasks.discard(t)

            task.add_done_callback(_done)

        if self._mode == "api":
            await self._handle_client_api(client_reader, client_writer, container_port)
        else:
            await self._handle_client_direct(client_reader, client_writer, container_port)

    async def _handle_client_direct(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        container_port: int,
    ) -> None:
        if not self._pod_ip:
            client_writer.close()
            return
        try:
            remote_reader, remote_writer = await asyncio.open_connection(
                self._pod_ip, container_port
            )
        except OSError as exc:
            logger.debug(
                "portforward direct connect %s:%s failed: %s",
                self._pod_ip,
                container_port,
                exc,
            )
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except Exception:
                pass
            return

        t1 = asyncio.create_task(_pipe(client_reader, remote_writer))
        t2 = asyncio.create_task(_pipe(remote_reader, client_writer))
        self._tasks.add(t1)
        self._tasks.add(t2)

        def _done(t: asyncio.Task[Any]) -> None:
            self._tasks.discard(t)

        t1.add_done_callback(_done)
        t2.add_done_callback(_done)
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for t in (t1, t2):
            if not t.done():
                t.cancel()

    async def _handle_client_api(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        container_port: int,
    ) -> None:
        try:
            pf, remote_sock = await asyncio.to_thread(
                open_kube_portforward, self._kube_env, container_port
            )
        except Exception as exc:
            logger.debug(
                "portforward api open pod=%s port=%s failed: %s",
                getattr(self._kube_env, "pod_name", None),
                container_port,
                exc,
            )
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except Exception:
                pass
            return
        try:
            await _bridge_to_blocking_socket(client_reader, client_writer, remote_sock)
        finally:
            await asyncio.to_thread(close_kube_portforward, pf)

    async def stop(self) -> None:
        self._stopped = True
        servers = self._servers
        self._servers = []
        for srv in servers:
            srv.close()
        for srv in servers:
            try:
                await srv.wait_closed()
            except Exception:
                pass
        tasks = list(self._tasks)
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.published = []
        self._pod_ip = None
        self._kube_env = None


def pick_free_port(host: str = "127.0.0.1") -> int:
    """Helper for tests: allocate an unused TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])
