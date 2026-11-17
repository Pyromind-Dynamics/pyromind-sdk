# Unit tests for env var fallback and endpoint resolution
from unittest import mock

import pytest

from pyromind_sdk.client.base import (
    PyroMindClient,
    DEFAULT_API_BASE_URL,
)
from pyromind_sdk.client.models import InferenceJobRequest
from pyromind_sdk.client.storage import StorageClient


@pytest.fixture(autouse=True)
def mock_networking():
    with (
        mock.patch("pyromind_sdk.client.base.requests.Session"),
        mock.patch("pyromind_sdk.client.base.Retry"),
        mock.patch("pyromind_sdk.client.base.HTTPAdapter"),
        mock.patch("pyromind_sdk.client.storage.Minio"),
    ):
        yield


# --- PyroMindClient ---

class TestPyroMindClientBaseUrl:
    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_BASE_URL", raising=False)
        client = PyroMindClient(api_key="test-key", cluster="us-west-2")
        # Exchange starts at the portal; the direct URL comes from its answer.
        assert client.base_url == DEFAULT_API_BASE_URL.rstrip("/")

    def test_explicit_base_url(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_BASE_URL", raising=False)
        client = PyroMindClient(
            api_key="test-key",
            cluster="us-west-2",
            base_url="https://custom.example.com/api",
        )
        assert client.base_url == "https://custom.example.com/api"

    def test_env_var_base_url(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "https://env.example.com/api")
        client = PyroMindClient(api_key="test-key", cluster="us-west-2")
        assert client.base_url == "https://env.example.com/api"

    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "")
        client = PyroMindClient(api_key="test-key", cluster="us-west-2")
        assert client.base_url == DEFAULT_API_BASE_URL.rstrip("/")


class TestPyroMindClientCluster:
    def test_unset_cluster_is_allowed_until_exchange(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == ""

    def test_explicit_cluster(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = PyroMindClient(api_key="test-key", cluster="us-west-1")
        assert client.cluster == "us-west-1"

    def test_env_var_cluster(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1")
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == "us-west-1"

    def test_empty_env_var_is_allowed_until_exchange(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "")
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == ""


# --- PyroMindAPIClient ---

class TestPyroMindAPIClientBaseUrl:
    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "")
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")
        with (
            mock.patch("pyromind_sdk.client.client.PyroMindClient"),
            mock.patch("pyromind_sdk.client.client.SandboxClient"),
            mock.patch("pyromind_sdk.client.client.JupyterLabClient"),
            mock.patch("pyromind_sdk.client.client.InferenceClient"),
            mock.patch("pyromind_sdk.client.client.StudioClient"),
            mock.patch("pyromind_sdk.client.client.EchoMindClient"),
            mock.patch("pyromind_sdk.client.client.ProfileClient"),
        ):
            from pyromind_sdk import PyroMindAPIClient
            client = PyroMindAPIClient(api_key="test-key")


class TestPyroMindAPIClientCluster:
    def test_empty_env_var_constructs_without_a_cluster(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "")
        with (
            mock.patch("pyromind_sdk.client.client.PyroMindClient"),
            mock.patch("pyromind_sdk.client.client.SandboxClient"),
            mock.patch("pyromind_sdk.client.client.JupyterLabClient"),
            mock.patch("pyromind_sdk.client.client.InferenceClient"),
            mock.patch("pyromind_sdk.client.client.StudioClient"),
            mock.patch("pyromind_sdk.client.client.EchoMindClient"),
            mock.patch("pyromind_sdk.client.client.ProfileClient"),
        ):
            from pyromind_sdk import PyroMindAPIClient
            PyroMindAPIClient(api_key="test-key")


def test_async_client_default_timeout_is_600_seconds(monkeypatch):
    from pyromind_sdk.client.async_base import (
        DEFAULT_TIMEOUT,
        PyroMindAsyncClient,
    )

    monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")
    client = PyroMindAsyncClient(api_key="test-key")

    assert DEFAULT_TIMEOUT == 600
    assert client.timeout.total == 600


# --- StorageClient ---

class TestStorageClientEndpoint:
    def test_default_endpoint(self):
        client = StorageClient(access_key="ak", secret_key="sk")
        assert client.endpoint == "storage-us-west-2.pyromind.ai"

    def test_explicit_endpoint(self):
        client = StorageClient(
            endpoint="https://custom.example.com",
            access_key="ak",
            secret_key="sk",
        )
        assert "custom.example.com" in client.endpoint

    def test_cluster_us_west_1(self):
        client = StorageClient(
            access_key="ak",
            secret_key="sk",
            cluster="us-west-1",
        )
        assert client.endpoint == "storage.pyromind.ai"

    def test_cluster_us_west_2(self):
        client = StorageClient(
            access_key="ak",
            secret_key="sk",
            cluster="us-west-2",
        )
        assert client.endpoint == "storage-us-west-2.pyromind.ai"

    def test_unknown_cluster_falls_back(self):
        client = StorageClient(
            access_key="ak",
            secret_key="sk",
            cluster="unknown-region",
        )
        assert client.endpoint == "storage-us-west-2.pyromind.ai"

    def test_env_var_overrides_cluster(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_STORAGE_ENDPOINT", "https://override.example.com")
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = StorageClient(access_key="ak", secret_key="sk")
        assert "override.example.com" in client.endpoint

    def test_pyromind_cluster_env_var(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_STORAGE_ENDPOINT", raising=False)
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1")
        client = StorageClient(access_key="ak", secret_key="sk")
        assert client.endpoint == "storage.pyromind.ai"

    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_STORAGE_ENDPOINT", "")
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = StorageClient(access_key="ak", secret_key="sk")
        assert client.endpoint == "storage-us-west-2.pyromind.ai"

    def test_endpoint_param_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_STORAGE_ENDPOINT", "https://env.example.com")
        client = StorageClient(
            endpoint="https://param.example.com",
            access_key="ak",
            secret_key="sk",
        )
        assert "param.example.com" in client.endpoint

    def test_cluster_param_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1")
        client = StorageClient(
            access_key="ak",
            secret_key="sk",
            cluster="us-west-2",
        )
        assert client.endpoint == "storage-us-west-2.pyromind.ai"


class TestInferenceStartupArgs:
    def test_dict_startup_args_are_serialized_to_argv(self):
        request = InferenceJobRequest(
            model_path="/workspace/models/test",
            startup_args=[{"--max-length": 12323}, {"-q": None}, {"--trust-remote-code": None}],
        )

        assert request.startup_args == ["--max-length", "12323", "-q", "--trust-remote-code"]

    def test_dict_startup_args_do_not_auto_prefix_key(self):
        request = InferenceJobRequest(
            model_path="/workspace/models/test",
            startup_args=[{"max-length": 12323}],
        )

        assert request.startup_args == ["max-length", "12323"]

    def test_legacy_argv_startup_args_still_work(self):
        request = InferenceJobRequest(
            model_path="/workspace/models/test",
            startup_args=["--max-length", "12323"],
        )

        assert request.startup_args == ["--max-length", "12323"]


# --- control plane vs data plane ---

class TestProfileClientBaseUrl:
    def test_profile_client_stays_on_the_portal(self, monkeypatch):
        from pyromind_sdk.client.base import DEFAULT_API_BASE_URL
        from pyromind_sdk.client.profile import ProfileClient

        monkeypatch.delenv("PYROMIND_BASE_URL", raising=False)
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-2")

        # Profile/账号接口只在 portal 上，保持 portal 地址 + 长期 key
        assert ProfileClient(api_key="test-key").base_url == (
            DEFAULT_API_BASE_URL.rstrip("/")
        )


class TestStorageCredentials:
    """storage 有独立的 AK/SK，平台 API key 不能当 storage AK 用。"""

    def test_access_key_comes_from_the_storage_env(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_STORAGE_ACCESS_KEY", "storage-ak")
        monkeypatch.setenv("PYROMIND_STORAGE_SECRET_KEY", "storage-sk")
        monkeypatch.setenv("PYROMIND_API_KEY", "platform-key")

        client = StorageClient(endpoint="https://storage.example.com")

        assert client.access_key == "storage-ak"
        assert client.secret_key == "storage-sk"

    def test_platform_key_is_not_used_as_the_storage_access_key(self, monkeypatch):
        import pytest

        monkeypatch.delenv("PYROMIND_STORAGE_ACCESS_KEY", raising=False)
        monkeypatch.setenv("PYROMIND_STORAGE_SECRET_KEY", "storage-sk")
        monkeypatch.setenv("PYROMIND_API_KEY", "platform-key")

        with pytest.raises(ValueError) as exc:
            StorageClient(endpoint="https://storage.example.com")

        assert "PYROMIND_STORAGE_ACCESS_KEY" in str(exc.value)
