from pyromind_sdk.common.utils import (
    DEFAULT_API_BASE_URL,
    api_base_url_for_cluster,
    cluster_value,
    normalise_cluster,
)


def test_cluster_value_keeps_the_pre_environment_suffix():
    assert cluster_value("us-west-2", "prod") == "us-west-2"
    assert cluster_value("us-west-2", "pre") == "us-west-2#pre"


def test_normalise_cluster_drops_only_the_prod_suffix():
    assert normalise_cluster("us-west-2#prod") == "us-west-2"
    assert normalise_cluster("us-west-2") == "us-west-2"
    assert normalise_cluster("us-west-2#pre") == "us-west-2#pre"
    assert normalise_cluster("  us-west-2#PRE2  ") == "us-west-2#pre2"
    assert normalise_cluster("") == ""


def test_api_base_url_for_cluster_selects_the_environment_portal():
    assert api_base_url_for_cluster("us-west-2") == DEFAULT_API_BASE_URL
    assert (
        api_base_url_for_cluster("us-west-2#pre")
        == "https://pre-api-portal.pyromind.ai/api/v1"
    )
    assert (
        api_base_url_for_cluster("us-west-2#pre2")
        == "https://pre2-api-portal.pyromind.ai/api/v1"
    )


def test_api_base_url_for_cluster_keeps_an_explicit_base():
    assert (
        api_base_url_for_cluster(
            "us-west-2#pre", "https://custom.example/api/v1/"
        )
        == "https://custom.example/api/v1"
    )
