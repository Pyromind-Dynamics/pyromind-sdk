"""Storage-related API resources for the main PyroMind API client."""

from __future__ import annotations

from .transfer import TransferClient


class StorageAPIClient:
    """Namespace for storage-related REST API resources.

    This class is intentionally separate from :class:`StorageClient`, which
    manages MinIO/S3-compatible object storage and requires storage
    credentials. The main API client uses this namespace for REST resources
    such as cross-cluster transfers.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        cluster: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
    ):
        self.transfers = TransferClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries,
        )
        # Singular alias for callers that prefer resource names in the API.
        self.transfer = self.transfers

    def close(self):
        """Close all storage-related API sessions."""

        self.transfers.close()
