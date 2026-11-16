"""
Pytest configuration to prevent test_yaml_nodes.py functions from being collected as tests.

The test_yaml_nodes.py file contains utility functions (test_yaml_file, test_directory)
that are used by pytest tests but are not pytest test functions themselves.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    """Remove test_yaml_file and test_directory from collected items"""
    items[:] = [item for item in items if item.name not in ("test_yaml_file", "test_directory")]


@pytest.fixture(autouse=True)
def _stub_token_exchange(monkeypatch):
    """Unit tests must never perform a real credential exchange.

    Every client request now carries a short-lived token; stub the cache so
    transport-level tests keep asserting on the request they actually make.
    """
    from pyromind_sdk import token as sdk_token

    monkeypatch.setattr(
        sdk_token, "fetch_token", lambda *args, **kwargs: ("test-token", 900)
    )

    async def _async_fetch(*args, **kwargs):
        return "test-token", 900

    monkeypatch.setattr(sdk_token, "async_fetch_token", _async_fetch)
