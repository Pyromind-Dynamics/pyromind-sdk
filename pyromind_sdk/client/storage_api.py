"""Storage-related REST API client."""

from __future__ import annotations

from .base import PyroMindAPIError, PyroMindClient
from .models import (
    TransferActionResponse,
    TransferClearResponse,
    TransferCreateResponse,
    TransferListQuery,
    TransferPage,
    TransferRequest,
    TransferTask,
)


class StorageAPIClient(PyroMindClient):
    """Client for storage-related REST APIs, including cross-cluster transfer.

    ``StorageAPIClient`` is the storage namespace exposed by
    :class:`PyroMindAPIClient`. It owns the transfer implementation directly;
    ``transfers`` and ``transfer`` are aliases to this same client so callers
    can use ``client.storage.transfers.submit(...)``.

    This class is separate from :class:`StorageClient`, which manages
    MinIO/S3-compatible object storage and requires storage credentials.
    """

    _TRANSFER_PREFIX = "/storage/transfer"

    @property
    def transfers(self) -> StorageAPIClient:
        """Return this storage API client as the plural transfer resource."""

        return self

    @property
    def transfer(self) -> StorageAPIClient:
        """Return this storage API client as the singular transfer resource."""

        return self

    def _extract_transfer_data(self, response):
        """Extract data and turn HTTP-200 business failures into SDK errors."""

        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code")
            else:
                message = response.get("message")
            raise PyroMindAPIError(
                message or "Transfer API request failed",
                response=response,
            )
        return self._extract_data(response)

    @staticmethod
    def _coerce_request(
        request: TransferRequest | dict | None,
        *,
        source_path: str | None,
        target_cluster: str | None,
        target_path: str | None,
        overwrite: str,
    ) -> TransferRequest:
        if request is not None:
            return (
                request
                if isinstance(request, TransferRequest)
                else TransferRequest(**request)
            )
        if source_path is None or target_cluster is None or target_path is None:
            raise TypeError(
                "source_path, target_cluster, and target_path are required when "
                "a TransferRequest is not provided"
            )
        return TransferRequest(
            source_path=source_path,
            target_cluster=target_cluster,
            target_path=target_path,
            overwrite=overwrite,
        )

    def submit(
        self,
        request: TransferRequest | dict | None = None,
        *,
        source_path: str | None = None,
        target_cluster: str | None = None,
        target_path: str | None = None,
        overwrite: str = "skip",
    ) -> TransferCreateResponse:
        """Queue a file or directory transfer to another cluster."""

        payload = self._coerce_request(
            request,
            source_path=source_path,
            target_cluster=target_cluster,
            target_path=target_path,
            overwrite=overwrite,
        )
        response = self.post(
            self._TRANSFER_PREFIX,
            json_data=payload.model_dump(exclude_none=True),
        )
        return TransferCreateResponse(**self._extract_transfer_data(response))

    def list_page(self, query: TransferListQuery | dict | None = None) -> TransferPage:
        """Fetch one page of transfer tasks."""

        query = query or TransferListQuery()
        if not isinstance(query, TransferListQuery):
            query = TransferListQuery(**query)
        response = self.get(
            f"{self._TRANSFER_PREFIX}/list",
            params=query.to_params(),
        )
        data = self._extract_transfer_data(response) or {}
        rows = data.get("list", []) if isinstance(data, dict) else []
        return TransferPage(
            tasks=[TransferTask(**row) for row in rows if isinstance(row, dict)],
            total=int(data.get("total", 0) or 0) if isinstance(data, dict) else 0,
            page_number=query.page_number,
            page_size=query.page_size,
        )

    def list(self, query: TransferListQuery | dict | None = None) -> list[TransferTask]:
        """List all transfer tasks, paging automatically."""

        query = query or TransferListQuery()
        if not isinstance(query, TransferListQuery):
            query = TransferListQuery(**query)
        tasks: list[TransferTask] = []
        page_number = query.page_number
        while True:
            page = self.list_page(query.model_copy(update={"page_number": page_number}))
            tasks.extend(page.tasks)
            if not page.tasks or len(tasks) >= page.total:
                return tasks
            page_number += 1

    def cancel(self, transfer_id: int) -> TransferActionResponse:
        """Cancel an active transfer task."""

        response = self.post(f"{self._TRANSFER_PREFIX}/{transfer_id}/cancel")
        return TransferActionResponse(**self._extract_transfer_data(response))

    def delete(self, transfer_id: int) -> TransferActionResponse:
        """Delete a cancelled or finished transfer task."""

        response = self._request("DELETE", f"{self._TRANSFER_PREFIX}/{transfer_id}")
        return TransferActionResponse(**self._extract_transfer_data(response))

    def clear_finished(self, ids: list[int] | None = None) -> TransferClearResponse:
        """Delete finished tasks; pass ``ids`` to delete only selected tasks."""

        body = {"ids": ids} if ids is not None else None
        response = self.post(f"{self._TRANSFER_PREFIX}/clear", json_data=body)
        return TransferClearResponse(**self._extract_transfer_data(response))
