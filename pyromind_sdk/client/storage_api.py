"""Storage-related REST API client."""

from __future__ import annotations

from .base import PyroMindAPIError, PyroMindClient
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


class FileShareResource:
    """Same-cluster sharing operations exposed by the storage API."""

    _PREFIX = "/storage/file_share"

    def __init__(self, storage: StorageAPIClient):
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

    def create(
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
        response = self._storage.post(
            self._PREFIX,
            json_data=payload.model_dump(exclude_none=True),
        )
        data = self._storage._extract_file_share_data(response)
        return FileShareCreateResponse(**data)

    def list_page(
        self, query: FileShareListQuery | dict | None = None
    ) -> FileSharePage:
        """Fetch one page of sent or received share tasks."""

        query = query or FileShareListQuery()
        if not isinstance(query, FileShareListQuery):
            query = FileShareListQuery(**query)
        response = self._storage.get(
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

    def list(
        self, query: FileShareListQuery | dict | None = None
    ) -> list[FileShareTask]:
        """List all sent or received share tasks, paging automatically."""

        query = query or FileShareListQuery()
        if not isinstance(query, FileShareListQuery):
            query = FileShareListQuery(**query)
        tasks: list[FileShareTask] = []
        page_number = query.page_number
        while True:
            page = self.list_page(
                query.model_copy(update={"page_number": page_number})
            )
            tasks.extend(page.tasks)
            if not page.tasks or len(tasks) >= page.total:
                return tasks
            page_number += 1

    def get(self, biz_no: str) -> FileShareTask:
        """Fetch one share task visible to the current participant."""

        response = self._storage.get(f"{self._PREFIX}/{biz_no}")
        return FileShareTask(**self._storage._extract_file_share_data(response))

    def cancel(self, biz_no: str) -> FileShareTask:
        """Cancel a pending share invitation created by the current user."""

        response = self._storage.post(f"{self._PREFIX}/{biz_no}/cancel")
        return FileShareTask(**self._storage._extract_file_share_data(response))

    def delete(self, biz_nos: str | list[str]) -> FileShareDeleteResponse:
        """Hide terminal share tasks from the current user's list only."""

        selected = [biz_nos] if isinstance(biz_nos, str) else biz_nos
        response = self._storage.post(
            f"{self._PREFIX}/delete",
            json_data={"biz_nos": selected},
        )
        return FileShareDeleteResponse(
            **self._storage._extract_file_share_data(response)
        )

    def retry_notification(self, biz_no: str) -> FileShareTask:
        """Retry delivery of a pending invitation or result notification."""

        response = self._storage.post(
            f"{self._PREFIX}/{biz_no}/notification/retry"
        )
        return FileShareTask(**self._storage._extract_file_share_data(response))


class StorageAPIClient(PyroMindClient):
    """Client for storage REST APIs, transfers, and same-cluster file sharing.

    ``StorageAPIClient`` is the storage namespace exposed by
    :class:`PyroMindAPIClient`. It owns the transfer implementation directly;
    ``transfers`` and ``transfer`` are aliases to this same client so callers
    can use ``client.storage.transfers.submit(...)``. Same-cluster sharing is
    exposed as the separate ``client.storage.file_shares`` resource.

    This class is separate from :class:`StorageClient`, which manages
    MinIO/S3-compatible object storage and requires storage credentials.
    """

    _TRANSFER_PREFIX = "/storage/transfer"

    @property
    def file_shares(self) -> FileShareResource:
        """Return the same-cluster file sharing resource."""

        return FileShareResource(self)

    @property
    def shares(self) -> FileShareResource:
        """Short alias for :attr:`file_shares`."""

        return self.file_shares

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

    def _extract_file_share_data(self, response):
        """Extract share response data and raise for HTTP-200 business failures."""

        if isinstance(response, dict) and response.get("success") is False:
            error = response.get("error")
            if isinstance(error, dict):
                message = error.get("message") or error.get("code")
            else:
                message = response.get("message")
            raise PyroMindAPIError(
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
