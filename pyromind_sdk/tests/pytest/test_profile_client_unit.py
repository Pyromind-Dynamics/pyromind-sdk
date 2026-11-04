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


def test_reset_storage_credentials_returns_the_pair_once():
    client = ProfileClient(
        api_key="test-key",
        base_url="https://api-portal.pyromind.ai/api/v1",
    )
    client.post = MagicMock(
        return_value={
            "success": True,
            "data": {
                "access_key": "NEW_STORAGE_ACCESS_KEY",
                "secret_key": "new-storage-secret",
                "url": "https://storage.pyromind.ai",
                "bucket_name": "10000042",
                "credentials_masked": False,
                "has_credentials": True,
                "cleanup_pending_clusters": ["cn-east-1"],
            },
        }
    )

    try:
        credentials = client.reset_storage_credentials()

        client.post.assert_called_once_with("/storage_credentials/reset")
        assert credentials.access_key == "NEW_STORAGE_ACCESS_KEY"
        assert credentials.secret_key == "new-storage-secret"
        assert credentials.credentials_masked is False
        assert credentials.cleanup_pending_clusters == ["cn-east-1"]
    finally:
        client.close()
