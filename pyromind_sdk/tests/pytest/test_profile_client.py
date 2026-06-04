#!/usr/bin/env python3
"""
Integration tests for profile-related APIs.
"""

import os

import pytest

from pyromind_sdk import ProfileClient, PyroMindAPIClient


@pytest.fixture(scope="module")
def api_key():
    api_key_value = os.getenv("PYROMIND_API_KEY")
    if not api_key_value:
        pytest.skip(
            "PYROMIND_API_KEY environment variable not set. "
            "Please set this environment variable to run integration tests."
        )
    print(f"[INFO] Using API key: {api_key_value[:10]}...{api_key_value[-4:] if len(api_key_value) > 14 else '***'}")
    return api_key_value


@pytest.fixture(scope="module")
def base_url():
    url = os.getenv("PYROMIND_BASE_URL", "https://api-portal.pyromind.ai/api/v1")
    print(f"[INFO] Using base URL: {url}")
    return url


@pytest.fixture(scope="module")
def client(api_key, base_url):
    api_client = PyroMindAPIClient(api_key=api_key, base_url=base_url)
    try:
        yield api_client
    finally:
        api_client.close()


@pytest.fixture(scope="module")
def profile_client(api_key, base_url):
    api_client = ProfileClient(api_key=api_key, base_url=base_url)
    try:
        yield api_client
    finally:
        api_client.close()


def test_main_client_exposes_profile_client(client):
    assert isinstance(client.profile, ProfileClient)


def test_get_user_info(profile_client):
    response = profile_client.get_user_info()

    assert response is not None
    assert response.is_logged_in is True
    assert response.user is not None
    assert response.user.username is not None
    assert response.user.email is not None
    assert response.user.uid is not None

    print(
        f"[TEST] user_info username={response.user.username}, "
        f"email={response.user.email}, uid={response.user.uid}"
    )


def test_get_storage_info(profile_client):
    storage_info = profile_client.get_storage_info()

    assert storage_info is not None
    assert isinstance(storage_info.access_key, str)
    assert len(storage_info.access_key.strip()) > 0
    assert storage_info.uid is not None
    assert storage_info.url is not None

    print(
        f"[TEST] storage_info access_key_prefix={storage_info.access_key[:8]}, "
        f"uid={storage_info.uid}, url={storage_info.url}"
    )


def test_list_keys(profile_client):
    keys = profile_client.list_keys()

    assert isinstance(keys, list)

    for index, key in enumerate(keys):
        assert key.id is not None
        assert key.user_id is not None
        assert key.name is not None
        assert key.pub_key is not None
        print(
            f"[TEST] key[{index}] id={key.id}, user_id={key.user_id}, "
            f"name={key.name}, key_type={key.key_type}"
        )


def test_profile_values_are_consistent(profile_client):
    user_info = profile_client.get_user_info()
    storage_info = profile_client.get_storage_info()

    assert str(user_info.user.uid) == str(storage_info.uid)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])