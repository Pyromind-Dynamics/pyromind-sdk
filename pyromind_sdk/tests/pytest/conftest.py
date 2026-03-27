"""
Pytest configuration and shared fixtures for integration tests.

This module provides:
1. Collection modification to prevent test_yaml_nodes.py functions from being collected
2. Shared fixtures for all integration tests (API client, storage client, test prefixes)
"""

import os
import pytest
import uuid
from pyromind_sdk import PyroMindAPIClient, StorageClient


# =============================================================================
# Collection Hooks
# =============================================================================

def pytest_collection_modifyitems(config, items):
    """
    Remove test_yaml_file and test_directory from collected items.

    The test_yaml_nodes.py file contains utility functions (test_yaml_file, test_directory)
    that are used by pytest tests but are not pytest test functions themselves.
    """
    items[:] = [item for item in items if item.name not in ("test_yaml_file", "test_directory")]


# =============================================================================
# Environment Variable Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def api_key() -> str:
    """
    Get PYROMIND_API_KEY from environment.

    Returns:
        The API key string.

    Raises:
        pytest.skip.Exception: If PYROMIND_API_KEY is not set.
    """
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Integration tests require a valid API key."
        )
    return api_key


@pytest.fixture(scope="session")
def masked_api_key(api_key: str) -> str:
    """
    Get a masked version of the API key for debugging.

    Shows first 8 characters and last 4 characters with asterisks in between.

    Args:
        api_key: The full API key from the api_key fixture.

    Returns:
        Masked API key string (e.g., "abcd1234****5678").
    """
    if len(api_key) > 12:
        return f"{api_key[:8]}....{api_key[-4:]}"
    return f"{api_key[:4]}****"


@pytest.fixture(scope="session")
def base_url() -> str:
    """
    Get PYROMIND_BASE_URL from environment or use default.

    Returns:
        The base URL for the PyroMind API.
    """
    return os.getenv("PYROMIND_BASE_URL", "https://api.pyromind.ai/api/v1")


# =============================================================================
# Client Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def client(api_key: str, base_url: str) -> PyroMindAPIClient:
    """
    Create a PyroMindAPIClient for integration tests.

    This fixture is session-scoped, meaning the same client instance
    will be used for all tests in the session.

    Args:
        api_key: The API key from the api_key fixture.
        base_url: The base URL from the base_url fixture.

    Returns:
        An initialized PyroMindAPIClient instance.
    """
    client = PyroMindAPIClient(api_key=api_key, base_url=base_url)
    yield client
    client.close()


@pytest.fixture(scope="session")
def storage_client() -> StorageClient:
    """
    Create a StorageClient for integration tests.

    This fixture is session-scoped and will be skipped if storage
    credentials are not configured.

    The StorageClient requires:
    - PYROMIND_API_KEY (access key)
    - PYROMIND_STORAGE_SECRET_KEY (secret key)

    Returns:
        An initialized StorageClient instance.

    Raises:
        pytest.skip.Exception: If storage credentials are not configured.
    """
    # Check for required storage credentials
    api_key = os.getenv("PYROMIND_API_KEY")
    secret_key = os.getenv("PYROMIND_STORAGE_SECRET_KEY")

    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY not set. Storage tests require this for access_key."
        )

    if not secret_key:
        pytest.skip(
            "PYROMIND_STORAGE_SECRET_KEY not set. "
            "Storage tests require storage credentials."
        )

    try:
        client = StorageClient()
        yield client
        client.close()
    except ValueError as e:
        pytest.skip(f"Failed to create StorageClient: {e}")


# =============================================================================
# Test Utility Fixtures
# =============================================================================

@pytest.fixture(scope="function")
def test_prefix() -> str:
    """
    Generate a UUID-based unique prefix for each test.

    This is useful for creating unique resource names in tests to avoid
    conflicts between test runs.

    Returns:
        A unique string prefix (e.g., "test_a1b2c3d4").
    """
    unique_id = uuid.uuid4().hex[:8]
    return f"test_{unique_id}"
