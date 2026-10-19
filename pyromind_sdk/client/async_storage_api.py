"""Async storage-related API resources for the main PyroMind API client."""

from __future__ import annotations

from .async_transfer import AsyncTransferClient


class AsyncStorageAPIClient:
    """Async namespace for storage-related REST API resources."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cluster: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.transfers = AsyncTransferClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries,
        )
        # Singular alias for callers that prefer resource names in the API.
        self.transfer = self.transfers

    async def close(self):
        """Close all storage-related API sessions."""

        await self.transfers.close()
