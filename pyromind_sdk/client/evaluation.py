"""Evaluation API client.

The console calls the same service through ``/evaluation_api`` with a login
cookie. This client calls the Bearer-token routes mounted at
``{base_url}/evaluations``. ``base_url`` already ends with ``/api/v1``.

A run is created from a saved job and finishes later. ``create_run`` returns
as soon as the workflow is submitted. ``wait_run`` polls ``get_run`` until
``live`` is false, then ``read_atif`` returns the trajectory.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Union
from urllib.parse import unquote

import aiohttp
import requests

from .async_base import PyroMindAsyncAPIError, PyroMindAsyncClient
from .base import PyroMindAPIError, PyroMindClient


JsonDict = Dict[str, Any]
Payload = Union[Mapping[str, Any], Any]

# Server-side terminal task statuses. ``get_run`` also returns ``live``, which
# is the value ``wait_run`` uses when the response contains it.
_TERMINAL_TASK_STATUSES = {"succeeded", "failed", "error", "terminated"}


class EvaluationRunTimeout(TimeoutError):
    """``wait_run`` exceeded its budget while the run was still active."""

    def __init__(self, run_id: int, timeout: float, run: JsonDict):
        self.run_id = run_id
        self.timeout = timeout
        self.run = run
        status = run.get("task_status") or "active"
        super().__init__(
            f"Evaluation run {run_id} is still {status} after {timeout:g} seconds"
        )


@dataclass(frozen=True)
class EvaluationAsset:
    """Bytes returned by ``GET /evaluations/runs/{run_id}/asset``."""

    content: bytes
    media_type: str
    filename: str


def _resource_id(value: Any, name: str) -> int:
    """Accept the integer assigned by ``create_job`` or ``create_run``."""
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a positive integer")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise TypeError(f"{name} must be a positive integer, got {value!r}")
    if number <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return number


def _page(value: Optional[int], name: str, upper: int) -> Optional[int]:
    if value is None:
        return None
    number = _resource_id(value, name)
    if number > upper:
        raise ValueError(f"{name} must be between 1 and {upper}")
    return number


def _query(**params: Any) -> Optional[JsonDict]:
    return {key: value for key, value in params.items() if value is not None} or None


def _json_body(payload: Payload) -> JsonDict:
    if hasattr(payload, "model_dump"):
        body = payload.model_dump(exclude_unset=True)
    elif isinstance(payload, Mapping):
        body = dict(payload)
    else:
        raise TypeError("Evaluation job payload must be a dict")
    if not isinstance(body, dict):
        raise TypeError("Evaluation job payload must be a dict")
    return body


def _run_is_live(run: JsonDict) -> bool:
    if "live" in run:
        return bool(run["live"])
    status = str(run.get("task_status") or "").strip().lower()
    if not status:
        return True
    return status not in _TERMINAL_TASK_STATUSES


def _asset_path(path: str) -> str:
    text = str(path or "").strip()
    parts = text.replace("\\", "/").split("/")
    if (
        not text
        or text.startswith(("/", "\\"))
        or "://" in text
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("asset path must stay inside the run ATIF directory")
    return "/".join(parts)


def _media_type(header: Optional[str]) -> str:
    value = str(header or "").split(";", 1)[0].strip().lower()
    return value or "application/octet-stream"


def _filename_from_disposition(header: Optional[str], fallback: str) -> str:
    text = str(header or "")
    marker = "filename*=utf-8''"
    lower = text.lower()
    start = lower.find(marker)
    if start >= 0:
        raw = text[start + len(marker):].split(";", 1)[0].strip().strip('"')
        if raw:
            return unquote(raw)
    quoted = 'filename="'
    start = lower.find(quoted)
    if start >= 0:
        raw = text[start + len(quoted):].split('"', 1)[0]
        if raw:
            return unquote(raw)
    return fallback


def _asset(content: bytes, media_header: Optional[str], disposition: Optional[str], path: str) -> EvaluationAsset:
    return EvaluationAsset(
        content=content,
        media_type=_media_type(media_header),
        filename=_filename_from_disposition(disposition, path.rsplit("/", 1)[-1]),
    )


class EvaluationClient(PyroMindClient):
    """Synchronous client for ``/api/v1/evaluations``.

    These routes are served by the portal. Keep the portal address after the
    API key is exchanged for a short-lived token.
    """

    KEEP_PORTAL_BASE = True

    def _data(self, response: Any) -> Any:
        return self._extract_data(response)

    def list_scripts(self) -> JsonDict:
        """GET /evaluations/scripts

        Returns ``items`` (builtin scripts and their supported display types)
        and ``display_types``.
        """
        return self._data(self.get("/evaluations/scripts"))

    def list_workflows(self) -> JsonDict:
        """GET /evaluations/workflows

        Returns ``items``. The server requires the Studio service to be activated.
        """
        return self._data(self.get("/evaluations/workflows"))

    def list_workflow_nodes(self, workflow_name: str) -> JsonDict:
        """GET /evaluations/workflow_nodes?workflow_name=...

        Returns ``items`` with ``value`` (template node id) and ``label`` for
        every executable node. Local value nodes (PrimitiveNode / StartNode)
        are excluded; their ids cannot be used as ``extra_params.output_node_id``.
        """
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name must be a non-empty string")
        return self._data(self.get(
            "/evaluations/workflow_nodes",
            params={"workflow_name": name},
        ))

    def list_jobs(
        self,
        project_id: Optional[str] = None,
        search_key: Optional[str] = None,
        page_number: int = 1,
        page_size: int = 20,
    ) -> JsonDict:
        """GET /evaluations/jobs

        Returns one page: ``items``, ``total``, ``page_number``, ``page_size``.
        Items include ``latest_run`` and ``run_count``. They omit script and HTML bodies.
        ``page_size`` cannot exceed 100.
        """
        return self._data(self.get(
            "/evaluations/jobs",
            params=_query(
                project_id=project_id,
                search_key=search_key,
                page_number=_page(page_number, "page_number", 10**9),
                page_size=_page(page_size, "page_size", 100),
            ),
        ))

    def create_job(self, payload: Payload) -> JsonDict:
        """POST /evaluations/jobs

        ``payload`` is the ``EvaluationJobCreateRequest`` body: ``name``,
        ``display_type``, ``sample_data``, ``script_source``, ``script_id`` or
        ``script_content``, ``extra_params``, and the custom HTML fields.
        The returned job contains the server-assigned ``id``.
        """
        return self._data(self.post("/evaluations/jobs", json_data=_json_body(payload)))

    def get_job(self, job_id: int) -> JsonDict:
        """GET /evaluations/jobs/{job_id}

        Returns the job, including ``script_content``, ``html_template``, and
        ``render_script``. A stored API key is returned blank.
        """
        job_id = _resource_id(job_id, "job_id")
        return self._data(self.get(f"/evaluations/jobs/{job_id}"))

    def update_job(self, job_id: int, payload: Payload) -> JsonDict:
        """PUT /evaluations/jobs/{job_id}

        ``payload`` contains only the ``EvaluationJobUpdateRequest`` fields to change.
        """
        job_id = _resource_id(job_id, "job_id")
        return self._data(self.put(
            f"/evaluations/jobs/{job_id}",
            json_data=_json_body(payload),
        ))

    def delete_job(self, job_id: int, force: bool = False) -> JsonDict:
        """DELETE /evaluations/jobs/{job_id}

        Returns ``{"deleted": true}``. An active run produces HTTP 409 unless
        ``force`` is true.
        """
        job_id = _resource_id(job_id, "job_id")
        return self._data(self.delete(
            f"/evaluations/jobs/{job_id}",
            params=_query(force="true" if force else None),
        ))

    def create_run(self, job_id: int) -> JsonDict:
        """POST /evaluations/jobs/{job_id}/runs

        ``job_id`` is ``job["id"]`` from ``create_job``. The response is the new
        run record. Its workflow may still be starting, so ``live`` is normally true.
        """
        job_id = _resource_id(job_id, "job_id")
        return self._data(self.post(f"/evaluations/jobs/{job_id}/runs"))

    def list_runs(
        self,
        job_id: int,
        page_number: int = 1,
        page_size: int = 100,
    ) -> JsonDict:
        """GET /evaluations/jobs/{job_id}/runs

        Returns one page of runs. ``page_size`` cannot exceed 200.
        """
        job_id = _resource_id(job_id, "job_id")
        return self._data(self.get(
            f"/evaluations/jobs/{job_id}/runs",
            params=_query(
                page_number=_page(page_number, "page_number", 10**9),
                page_size=_page(page_size, "page_size", 200),
            ),
        ))

    def get_run(self, run_id: int) -> JsonDict:
        """GET /evaluations/runs/{run_id}

        Returns the run plus its job summary. ``live`` is false for Succeeded,
        Failed, Error, and Terminated. ``result_available`` is true once an ATIF
        path has been recorded.
        """
        run_id = _resource_id(run_id, "run_id")
        data = self._data(self.get(f"/evaluations/runs/{run_id}"))
        if not isinstance(data, dict):
            raise PyroMindAPIError(
                message=f"GET /evaluations/runs/{run_id} returned no run record",
                status_code=None,
            )
        return data

    def wait_run(
        self,
        run_id: int,
        timeout: float = 3600,
        interval: float = 5,
    ) -> JsonDict:
        """Poll GET /evaluations/runs/{run_id} until ``live`` is false.

        This is client-side polling. Each request still uses the HTTP timeout
        configured on the client. ``timeout`` is the total time spent waiting.
        The returned run may be failed; its ``task_status`` and ``error_message``
        describe the outcome.
        """
        run_id = _resource_id(run_id, "run_id")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if interval <= 0:
            raise ValueError("interval must be greater than 0")
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_run(run_id)
            if not _run_is_live(run):
                return run
            if time.monotonic() >= deadline:
                raise EvaluationRunTimeout(run_id, timeout, run)
            time.sleep(min(interval, max(deadline - time.monotonic(), 0)))

    def stop_run(self, run_id: int) -> JsonDict:
        """POST /evaluations/runs/{run_id}/stop

        Returns the run after the stop request. A run that is already finished
        produces HTTP 409.
        """
        run_id = _resource_id(run_id, "run_id")
        return self._data(self.post(f"/evaluations/runs/{run_id}/stop"))

    def delete_run(self, run_id: int, force: bool = False) -> JsonDict:
        """DELETE /evaluations/runs/{run_id}

        Returns ``{"deleted": true}``. An active run produces HTTP 409 unless
        ``force`` is true.
        """
        run_id = _resource_id(run_id, "run_id")
        return self._data(self.delete(
            f"/evaluations/runs/{run_id}",
            params=_query(force="true" if force else None),
        ))

    def read_atif(self, run_id: int) -> JsonDict:
        """GET /evaluations/runs/{run_id}/atif

        Returns ``{"run": ..., "atif": ...}``. Read the trajectory with
        ``result["atif"]``. A missing result is HTTP 404. A file over 20 MB is HTTP 413.
        """
        run_id = _resource_id(run_id, "run_id")
        data = self._data(self.get(f"/evaluations/runs/{run_id}/atif"))
        if not isinstance(data, dict):
            raise PyroMindAPIError(
                message=f"GET /evaluations/runs/{run_id}/atif returned no ATIF payload",
                status_code=None,
            )
        return data

    def read_asset(self, run_id: int, path: str) -> EvaluationAsset:
        """GET /evaluations/runs/{run_id}/asset?path=

        Returns the file bytes. ``path`` is relative to the ATIF file's directory,
        for example ``assets/asset_0001_image.png``. HTTPS image URLs are not
        fetched here.
        """
        run_id = _resource_id(run_id, "run_id")
        relative = _asset_path(path)
        url = self._build_url(f"/evaluations/runs/{run_id}/asset")
        try:
            response = self._raw_request(
                "GET",
                url,
                params={"path": relative},
                headers={"Accept": "*/*"},
            )
        except requests.exceptions.RequestException as exc:
            raise PyroMindAPIError(
                message=f"GET {url} request failed: {type(exc).__name__}: {exc}",
                status_code=None,
            ) from exc
        if not response.ok:
            self._handle_error_response(response, f"GET {url}")
        return _asset(
            response.content,
            response.headers.get("Content-Type"),
            response.headers.get("Content-Disposition"),
            relative,
        )


class AsyncEvaluationClient(PyroMindAsyncClient):
    """Asynchronous client for the same ``/api/v1/evaluations`` routes."""

    KEEP_PORTAL_BASE = True

    def _data(self, response: Any) -> Any:
        return self._extract_data(response)

    async def list_scripts(self) -> JsonDict:
        """GET /evaluations/scripts"""
        return self._data(await self.get("/evaluations/scripts"))

    async def list_workflows(self) -> JsonDict:
        """GET /evaluations/workflows"""
        return self._data(await self.get("/evaluations/workflows"))

    async def list_workflow_nodes(self, workflow_name: str) -> JsonDict:
        """GET /evaluations/workflow_nodes?workflow_name=..."""
        name = str(workflow_name or "").strip()
        if not name:
            raise ValueError("workflow_name must be a non-empty string")
        return self._data(await self.get(
            "/evaluations/workflow_nodes",
            params={"workflow_name": name},
        ))

    async def list_jobs(
        self,
        project_id: Optional[str] = None,
        search_key: Optional[str] = None,
        page_number: int = 1,
        page_size: int = 20,
    ) -> JsonDict:
        """GET /evaluations/jobs"""
        return self._data(await self.get(
            "/evaluations/jobs",
            params=_query(
                project_id=project_id,
                search_key=search_key,
                page_number=_page(page_number, "page_number", 10**9),
                page_size=_page(page_size, "page_size", 100),
            ),
        ))

    async def create_job(self, payload: Payload) -> JsonDict:
        """POST /evaluations/jobs"""
        return self._data(await self.post("/evaluations/jobs", json_data=_json_body(payload)))

    async def get_job(self, job_id: int) -> JsonDict:
        """GET /evaluations/jobs/{job_id}"""
        job_id = _resource_id(job_id, "job_id")
        return self._data(await self.get(f"/evaluations/jobs/{job_id}"))

    async def update_job(self, job_id: int, payload: Payload) -> JsonDict:
        """PUT /evaluations/jobs/{job_id}"""
        job_id = _resource_id(job_id, "job_id")
        return self._data(await self.put(
            f"/evaluations/jobs/{job_id}",
            json_data=_json_body(payload),
        ))

    async def delete_job(self, job_id: int, force: bool = False) -> JsonDict:
        """DELETE /evaluations/jobs/{job_id}"""
        job_id = _resource_id(job_id, "job_id")
        return self._data(await self.delete(
            f"/evaluations/jobs/{job_id}",
            params=_query(force="true" if force else None),
        ))

    async def create_run(self, job_id: int) -> JsonDict:
        """POST /evaluations/jobs/{job_id}/runs"""
        job_id = _resource_id(job_id, "job_id")
        return self._data(await self.post(f"/evaluations/jobs/{job_id}/runs"))

    async def list_runs(
        self,
        job_id: int,
        page_number: int = 1,
        page_size: int = 100,
    ) -> JsonDict:
        """GET /evaluations/jobs/{job_id}/runs"""
        job_id = _resource_id(job_id, "job_id")
        return self._data(await self.get(
            f"/evaluations/jobs/{job_id}/runs",
            params=_query(
                page_number=_page(page_number, "page_number", 10**9),
                page_size=_page(page_size, "page_size", 200),
            ),
        ))

    async def get_run(self, run_id: int) -> JsonDict:
        """GET /evaluations/runs/{run_id}"""
        run_id = _resource_id(run_id, "run_id")
        data = self._data(await self.get(f"/evaluations/runs/{run_id}"))
        if not isinstance(data, dict):
            raise PyroMindAsyncAPIError(
                message=f"GET /evaluations/runs/{run_id} returned no run record",
                status_code=None,
            )
        return data

    async def wait_run(
        self,
        run_id: int,
        timeout: float = 3600,
        interval: float = 5,
    ) -> JsonDict:
        """Poll GET /evaluations/runs/{run_id} until ``live`` is false."""
        run_id = _resource_id(run_id, "run_id")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0")
        if interval <= 0:
            raise ValueError("interval must be greater than 0")
        deadline = time.monotonic() + timeout
        while True:
            run = await self.get_run(run_id)
            if not _run_is_live(run):
                return run
            if time.monotonic() >= deadline:
                raise EvaluationRunTimeout(run_id, timeout, run)
            await asyncio.sleep(min(interval, max(deadline - time.monotonic(), 0)))

    async def stop_run(self, run_id: int) -> JsonDict:
        """POST /evaluations/runs/{run_id}/stop"""
        run_id = _resource_id(run_id, "run_id")
        return self._data(await self.post(f"/evaluations/runs/{run_id}/stop"))

    async def delete_run(self, run_id: int, force: bool = False) -> JsonDict:
        """DELETE /evaluations/runs/{run_id}"""
        run_id = _resource_id(run_id, "run_id")
        return self._data(await self.delete(
            f"/evaluations/runs/{run_id}",
            params=_query(force="true" if force else None),
        ))

    async def read_atif(self, run_id: int) -> JsonDict:
        """GET /evaluations/runs/{run_id}/atif"""
        run_id = _resource_id(run_id, "run_id")
        data = self._data(await self.get(f"/evaluations/runs/{run_id}/atif"))
        if not isinstance(data, dict):
            raise PyroMindAsyncAPIError(
                message=f"GET /evaluations/runs/{run_id}/atif returned no ATIF payload",
                status_code=None,
            )
        return data

    async def read_asset(self, run_id: int, path: str) -> EvaluationAsset:
        """GET /evaluations/runs/{run_id}/asset?path="""
        run_id = _resource_id(run_id, "run_id")
        relative = _asset_path(path)
        url = self._build_url(f"/evaluations/runs/{run_id}/asset")
        session = await self._get_session()
        try:
            async with self._raw_request(
                session,
                "GET",
                url,
                params={"path": relative},
                headers={"Accept": "*/*"},
            ) as response:
                if not response.ok:
                    await self._handle_error_response(response, f"GET {url}")
                content = await response.read()
                return _asset(
                    content,
                    response.headers.get("Content-Type"),
                    response.headers.get("Content-Disposition"),
                    relative,
                )
        except aiohttp.ClientError as exc:
            raise PyroMindAsyncAPIError(
                message=f"GET {url} request failed: {type(exc).__name__}: {exc}",
                status_code=None,
            ) from exc
