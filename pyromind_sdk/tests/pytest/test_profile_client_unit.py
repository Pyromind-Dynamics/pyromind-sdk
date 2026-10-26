from unittest.mock import MagicMock

from pyromind_sdk.client.profile import ProfileClient


def test_get_user_clusters_queries_portal_and_returns_enabled_codes():
    client = ProfileClient(
        api_key="test-key",
        base_url="https://api-portal.pyromind.ai/api/v1",
    )
    client.get = MagicMock(
        return_value={
            "success": True,
            "data": {"clusters": ["us-west-1", "us-west-3"]},
        }
    )

    try:
        assert client.get_user_clusters() == ["us-west-1", "us-west-3"]
        client.get.assert_called_once_with("/user_clusters")
    finally:
        client.close()
