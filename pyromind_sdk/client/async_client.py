"""
Async PyroMind API Client

This module provides the main async client for the PyroMind API.
"""

from typing import Optional
from .async_base import PyroMindAsyncClient as _PyroMindAsyncClientBase
from .async_echomind import AsyncEchoMindClient
from .async_inference import AsyncInferenceClient
from .async_instance import AsyncInstanceClient
from .async_sandbox import AsyncSandboxClient
from .async_training import AsyncTrainingClient


class PyroMindAsyncAPIClient:
    """
    Main async client for PyroMind API

    This client provides access to all PyroMind API resources through
    async methods. It aggregates sub-clients for different resource types.

    Usage:
        async with PyroMindAsyncAPIClient(api_key="your-api-key") as client:
            # List resources concurrently
            sandboxes, instances = await asyncio.gather(
                client.sandboxes.list(),
                client.instances.list()
            )

    Args:
        api_key: Bearer token for API authentication. If not provided, will try to
                read from PYROMIND_API_KEY environment variable.
        base_url: Base URL for the API. If not provided, will try to read from
                 PYROMIND_BASE_URL environment variable. Defaults to
                 https://api.pyromind.ai/api/v1
        cluster: Target cluster identifier. Will be sent as X-Cluster header
                on every request. If not provided, will try to read from
                PYROMIND_CLUSTER environment variable. Defaults to "default".
        timeout: Request timeout in seconds (default: 30)
        max_retries: Maximum number of retries for failed requests (default: 3)

    Raises:
        ValueError: If api_key is not provided and PYROMIND_API_KEY environment
                   variable is not set.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cluster: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 3
    ):
        # Initialize base client with common settings
        self._base_client = _PyroMindAsyncClientBase(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )

        # Initialize sub-clients
        self.echomind = AsyncEchoMindClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )
        self.inference = AsyncInferenceClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )
        self.instances = AsyncInstanceClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )
        self.sandboxes = AsyncSandboxClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )
        self.training = AsyncTrainingClient(
            api_key=api_key,
            base_url=base_url,
            cluster=cluster,
            timeout=timeout,
            max_retries=max_retries
        )

    async def close(self):
        """Close all client sessions"""
        await self._base_client.close()
        await self.echomind.close()
        await self.inference.close()
        await self.instances.close()
        await self.sandboxes.close()
        await self.training.close()

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()


# Alias for backward compatibility (must be exported separately)
PyroMindAsyncClient = PyroMindAsyncAPIClient