# Unit tests for env var fallback and endpoint resolution
from unittest import mock

import pytest

from pyromind_sdk.client.base import PyroMindClient, DEFAULT_API_BASE_URL, DEFAULT_CLUSTER
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
        client = PyroMindClient(api_key="test-key")
        assert client.base_url == DEFAULT_API_BASE_URL.rstrip("/")

    def test_explicit_base_url(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_BASE_URL", raising=False)
        client = PyroMindClient(api_key="test-key", base_url="https://custom.example.com/api")
        assert client.base_url == "https://custom.example.com/api"

    def test_env_var_base_url(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "https://env.example.com/api")
        client = PyroMindClient(api_key="test-key")
        assert client.base_url == "https://env.example.com/api"

    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "")
        client = PyroMindClient(api_key="test-key")
        assert client.base_url == DEFAULT_API_BASE_URL.rstrip("/")


class TestPyroMindClientCluster:
    def test_default_cluster(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == DEFAULT_CLUSTER

    def test_explicit_cluster(self, monkeypatch):
        monkeypatch.delenv("PYROMIND_CLUSTER", raising=False)
        client = PyroMindClient(api_key="test-key", cluster="us-west-1")
        assert client.cluster == "us-west-1"

    def test_env_var_cluster(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "us-west-1")
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == "us-west-1"

    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_CLUSTER", "")
        client = PyroMindClient(api_key="test-key")
        assert client.cluster == DEFAULT_CLUSTER


# --- PyroMindAPIClient ---

class TestPyroMindAPIClientBaseUrl:
    def test_empty_env_var_falls_back(self, monkeypatch):
        monkeypatch.setenv("PYROMIND_BASE_URL", "")
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
    def test_empty_env_var_falls_back(self, monkeypatch):
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
            client = PyroMindAPIClient(api_key="test-key")


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
