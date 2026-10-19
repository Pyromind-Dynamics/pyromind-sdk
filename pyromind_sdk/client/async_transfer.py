"""Async cross-cluster file transfer API client."""

from __future__ import annotations

from typing import List, Optional, Union

from .async_base import PyroMindAsyncAPIError, PyroMindAsyncClient
from .models import (
    TransferActionResponse,
    TransferClearResponse,
    TransferCreateResponse,
    TransferListQuery,
    TransferPage,
    TransferRequest,
    TransferTask,
)


class AsyncTransferClient(PyroMindAsyncClient):
    """Async client for cross-cluster file transfers."""

    _TRANSFER_PREFIX = "/storage/transfer"

    def _extract_transfer_data(self, response):
        """Extract data and turn HTTP-200 business failures into SDK errors."""

        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code")
            else:
                message = response.get("message")
            raise PyroMindAsyncAPIError(
                message or "Transfer API request failed",
                response=response,
            )
        return self._extract_data(response)

    @staticmethod
    def _coerce_request(
        request: Optional[Union[TransferRequest, dict]],
        *,
        source_path: Optional[str],
        target_cluster: Optional[str],
        target_path: Optional[str],
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

    async def submit(
        self,
        request: Optional[Union[TransferRequest, dict]] = None,
        *,
        source_path: Optional[str] = None,
        target_cluster: Optional[str] = None,
        target_path: Optional[str] = None,
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
        response = await self.post(
            self._TRANSFER_PREFIX,
            json_data=payload.model_dump(exclude_none=True),
        )
        return TransferCreateResponse(**self._extract_transfer_data(response))

    async def list_page(
        self, query: Optional[TransferListQuery] = None
    ) -> TransferPage:
        """Fetch one page of transfer tasks."""

        query = query or TransferListQuery()
        if not isinstance(query, TransferListQuery):
            query = TransferListQuery(**query)
        response = await self.get(
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

    async def list(
        self, query: Optional[TransferListQuery] = None
    ) -> List[TransferTask]:
        """List all transfer tasks, paging automatically."""

        query = query or TransferListQuery()
        if not isinstance(query, TransferListQuery):
            query = TransferListQuery(**query)
        tasks: List[TransferTask] = []
        page_number = query.page_number
        while True:
            page = await self.list_page(
                query.model_copy(update={"page_number": page_number})
            )
            tasks.extend(page.tasks)
            if not page.tasks or len(tasks) >= page.total:
                return tasks
            page_number += 1

    async def cancel(self, transfer_id: int) -> TransferActionResponse:
        """Cancel an active transfer task."""

        response = await self.post(f"{self._TRANSFER_PREFIX}/{transfer_id}/cancel")
        return TransferActionResponse(**self._extract_transfer_data(response))

    async def delete(self, transfer_id: int) -> TransferActionResponse:
        """Delete a cancelled or finished transfer task."""

        response = await self._request(
            "DELETE", f"{self._TRANSFER_PREFIX}/{transfer_id}"
        )
        return TransferActionResponse(**self._extract_transfer_data(response))

    async def clear_finished(
        self, ids: Optional[List[int]] = None
    ) -> TransferClearResponse:
        """Delete finished tasks; pass ``ids`` to delete only selected tasks."""

        body = {"ids": ids} if ids is not None else None
        response = await self.post(f"{self._TRANSFER_PREFIX}/clear", json_data=body)
        return TransferClearResponse(**self._extract_transfer_data(response))
