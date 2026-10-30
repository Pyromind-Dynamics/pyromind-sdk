"""Async storage-related REST API client."""

from __future__ import annotations

from .async_base import PyroMindAsyncAPIError, PyroMindAsyncClient
from .models import (
    FileShareCreateResponse,
    FileShareDeleteResponse,
    FileShareListQuery,
    FileSharePage,
    FileShareRequest,
    FileShareTask,
    TransferActionResponse,
    TransferClearResponse,
    TransferCreateResponse,
    TransferListQuery,
    TransferPage,
    TransferRequest,
    TransferTask,
)


class AsyncFileShareResource:
    """Async same-cluster sharing operations exposed by storage."""

    _PREFIX = "/storage/file_share"

    def __init__(self, storage: AsyncStorageAPIClient):
        self._storage = storage

    @staticmethod
    def _coerce_request(
        request: FileShareRequest | dict | None,
        *,
        source_path: str | None,
        target_user_input: str | None,
        target_user_id: int | None,
    ) -> FileShareRequest:
        if request is not None:
            if any(
                value is not None
                for value in (source_path, target_user_input, target_user_id)
            ):
                raise TypeError("pass either request or individual share arguments")
            return (
                request
                if isinstance(request, FileShareRequest)
                else FileShareRequest(**request)
            )
        if source_path is None:
            raise TypeError("source_path is required when request is not provided")
        return FileShareRequest(
            source_path=source_path,
            target_user_input=target_user_input,
            target_user_id=target_user_id,
        )

    async def create(
        self,
        request: FileShareRequest | dict | None = None,
        *,
        source_path: str | None = None,
        target_user_input: str | None = None,
        target_user_id: int | None = None,
    ) -> FileShareCreateResponse:
        """Create a same-cluster share invitation using a UID or email."""

        payload = self._coerce_request(
            request,
            source_path=source_path,
            target_user_input=target_user_input,
            target_user_id=target_user_id,
        )
        response = await self._storage.post(
            self._PREFIX,
            json_data=payload.model_dump(exclude_none=True),
        )
        data = self._storage._extract_file_share_data(response)
        return FileShareCreateResponse(**data)

    async def list_page(
        self, query: FileShareListQuery | dict | None = None
    ) -> FileSharePage:
        """Fetch one page of sent or received share tasks."""

        query = query or FileShareListQuery()
        if not isinstance(query, FileShareListQuery):
            query = FileShareListQuery(**query)
        response = await self._storage.get(
            f"{self._PREFIX}/list",
            params=query.to_params(),
        )
        data = self._storage._extract_file_share_data(response) or {}
        rows = data.get("list", []) if isinstance(data, dict) else []
        return FileSharePage(
            tasks=[FileShareTask(**row) for row in rows if isinstance(row, dict)],
            total=int(data.get("total", 0) or 0) if isinstance(data, dict) else 0,
            page_number=query.page_number,
            page_size=query.page_size,
        )

    async def list(
        self, query: FileShareListQuery | dict | None = None
    ) -> list[FileShareTask]:
        """List all sent or received share tasks, paging automatically."""

        query = query or FileShareListQuery()
        if not isinstance(query, FileShareListQuery):
            query = FileShareListQuery(**query)
        tasks: list[FileShareTask] = []
        page_number = query.page_number
        while True:
            page = await self.list_page(
                query.model_copy(update={"page_number": page_number})
            )
            tasks.extend(page.tasks)
            if not page.tasks or len(tasks) >= page.total:
                return tasks
            page_number += 1

    async def get(self, biz_no: str) -> FileShareTask:
        """Fetch one share task visible to the current participant."""

        response = await self._storage.get(f"{self._PREFIX}/{biz_no}")
        return FileShareTask(**self._storage._extract_file_share_data(response))

    async def cancel(self, biz_no: str) -> FileShareTask:
        """Cancel a pending share invitation created by the current user."""

        response = await self._storage.post(f"{self._PREFIX}/{biz_no}/cancel")
        return FileShareTask(**self._storage._extract_file_share_data(response))

    async def delete(self, biz_nos: str | list[str]) -> FileShareDeleteResponse:
        """Hide terminal share tasks from the current user's list only."""

        selected = [biz_nos] if isinstance(biz_nos, str) else biz_nos
        response = await self._storage.post(
            f"{self._PREFIX}/delete",
            json_data={"biz_nos": selected},
        )
        return FileShareDeleteResponse(
            **self._storage._extract_file_share_data(response)
        )

    async def retry_notification(self, biz_no: str) -> FileShareTask:
        """Retry delivery of a pending invitation or result notification."""

        response = await self._storage.post(
            f"{self._PREFIX}/{biz_no}/notification/retry"
        )
        return FileShareTask(**self._storage._extract_file_share_data(response))


class AsyncStorageAPIClient(PyroMindAsyncClient):
    """Async client for storage APIs, transfers, and same-cluster file sharing."""

    _TRANSFER_PREFIX = "/storage/transfer"

    @property
    def file_shares(self) -> AsyncFileShareResource:
        """Return the async same-cluster file sharing resource."""

        return AsyncFileShareResource(self)

    @property
    def shares(self) -> AsyncFileShareResource:
        """Short alias for :attr:`file_shares`."""

        return self.file_shares

    @property
    def transfers(self) -> AsyncStorageAPIClient:
        """Return this storage API client as the plural transfer resource."""

        return self

    @property
    def transfer(self) -> AsyncStorageAPIClient:
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
            raise PyroMindAsyncAPIError(
                message or "Transfer API request failed",
                response=response,
            )
        return self._extract_data(response)

    def _extract_file_share_data(self, response):
        """Extract share response data and raise for HTTP-200 business failures."""

        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code")
            else:
                message = response.get("message")
            raise PyroMindAsyncAPIError(
                message or "File share API request failed",
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

    async def submit(
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
        response = await self.post(
            self._TRANSFER_PREFIX,
            json_data=payload.model_dump(exclude_none=True),
        )
        return TransferCreateResponse(**self._extract_transfer_data(response))

    async def list_page(
        self, query: TransferListQuery | dict | None = None
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
        self, query: TransferListQuery | dict | None = None
    ) -> list[TransferTask]:
        """List all transfer tasks, paging automatically."""

        query = query or TransferListQuery()
        if not isinstance(query, TransferListQuery):
            query = TransferListQuery(**query)
        tasks: list[TransferTask] = []
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
        self, ids: list[int] | None = None
    ) -> TransferClearResponse:
        """Delete finished tasks; pass ``ids`` to delete only selected tasks."""

        body = {"ids": ids} if ids is not None else None
        response = await self.post(f"{self._TRANSFER_PREFIX}/clear", json_data=body)
        return TransferClearResponse(**self._extract_transfer_data(response))
