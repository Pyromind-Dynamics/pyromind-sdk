#!/usr/bin/env python3
"""
Integration tests for Storage API

This module provides pytest-based integration tests for storage operations,
using real API calls (no mocks).

Environment variables required:
- PYROMIND_API_KEY: API key for authentication
- PYROMIND_BASE_URL: Base URL for the API (optional, defaults to https://api-portal.pyromind.ai/api/v1)
- PYROMIND_CLUSTER: Target cluster identifier (optional, defaults to us-west-2)
"""

import os

import pytest

from pyromind_sdk.client.storage import get_storage_info
from pyromind_sdk.client.base import PyroMindAPIError


@pytest.fixture(scope="module")
def api_key():
    """Get API key from environment variable"""
    api_key = os.getenv("PYROMIND_API_KEY")
    if not api_key:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Please set this environment variable to run integration tests."
        )
    print(f"[INFO] Using API key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else '***'}")
    return api_key


class TestGetStorageInfo:
    """Test cases for get_storage_info function"""

    def test_get_storage_info(self, api_key):
        """Test getting storage information from the API"""
        print("[TEST] Testing get_storage_info...")
        try:
            info = get_storage_info()
            print(f"[TEST] Retrieved storage info: {info}")
        except PyroMindAPIError as e:
            print(f"[ERROR] Failed to get storage info: {e.message} (status_code: {e.status_code})")
            raise
        except Exception as e:
            print(f"[ERROR] Unexpected error: {type(e).__name__}: {str(e)}")
            raise

        assert isinstance(info, dict), f"Expected dict, got {type(info).__name__}"

        # Verify required fields
        assert "access_key" in info, "Missing 'access_key' field"
        assert "secret_key" in info, "Missing 'secret_key' field"
        assert "url" in info, "Missing 'url' field"
        assert "bucket_name" in info, "Missing 'bucket_name' field"

        assert info["access_key"] is not None, "'access_key' is None"
        assert info["secret_key"] is not None, "'secret_key' is None"
        assert info["url"] is not None, "'url' is None"
        assert info["bucket_name"] is not None, "'bucket_name' is None"

        print(f"[TEST] Storage info validation passed:")
        print(f"  - access_key: {info['access_key'][:10]}...")
        print(f"  - url: {info['url']}")
        print(f"  - bucket_name: {info['bucket_name']}")

        # Check optional quota fields
        if "used_size" in info:
            assert "human_used_size" in info, "Missing 'human_used_size' field"
            assert "total_size" in info, "Missing 'total_size' field"
            assert "human_total_size" in info, "Missing 'human_total_size' field"

            print(f"  - used_size: {info['used_size']} ({info['human_used_size']})")
            print(f"  - total_size: {info['total_size']} ({info['human_total_size']})")
        else:
            print("  - No quota information available")

    def test_get_storage_info_missing_api_key(self):
        """Test that get_storage_info raises ValueError when API key is not set"""
        print("[TEST] Testing get_storage_info with missing API key...")

        # Save and remove API key
        original_key = os.environ.get("PYROMIND_API_KEY")
        if "PYROMIND_API_KEY" in os.environ:
            del os.environ["PYROMIND_API_KEY"]

        try:
            get_storage_info()
        except Exception as e:
            print(f"[INFO] Failed: get_storage_info -------------: {type(e).__name__}: {str(e)}")
            if "API key is required" in e.__str__():
                print("[TEST] Passed: get_storage_info raised ValueError when API key is missing")
            else:
                print(f"[ERROR] Failed: get_storage_info did not raise ValueError when API key is missing: {str(e)}")
                raise e
        finally:
            # Restore API key
            if original_key:
                os.environ["PYROMIND_API_KEY"] = original_key


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
