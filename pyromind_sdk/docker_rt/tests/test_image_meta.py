"""Image identity metadata: config digest, size, push digest."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile

from ..api import images as images_mod
from ..backend import image_meta, kaniko


def _docker_save(tmp_path):
    """A minimal ``docker save`` archive (manifest + config + one layer)."""
    config = json.dumps(
        {
            "architecture": "amd64",
            "os": "linux",
            "config": {},
            "rootfs": {"type": "layers", "diff_ids": []},
        }
    ).encode()
    config_name = "sha256:" + hashlib.sha256(config).hexdigest()
    manifest = [
        {"Config": config_name, "RepoTags": ["example:1"], "Layers": ["layer.tar"]}
    ]
    members = (
        (config_name, config),
        ("layer.tar", b"layer-bytes"),
        ("manifest.json", json.dumps(manifest).encode()),
    )
    path = tmp_path / "image.tar"
    with tarfile.open(path, "w") as bundle:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
    return path, config_name


def test_config_digest_is_the_image_id(tmp_path):
    path, expected = _docker_save(tmp_path)
    assert kaniko.config_digest_from_archive(path) == expected
    assert kaniko.config_digest_from_archive(tmp_path / "missing.tar") == ""


def test_push_digest_roundtrip_and_forget(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    digest = "sha256:" + "a" * 64

    image_meta.record_push_digest("docker.io/user/app:dev", digest)
    assert image_meta.repo_digests("docker.io/user/app:dev") == [
        f"docker.io/user/app@{digest}"
    ]
    assert image_meta.repo_digests("docker.io/user/app:other") == []
    # A malformed digest is not recorded.
    image_meta.record_push_digest("docker.io/user/app:bad", "not-a-digest")
    assert image_meta.repo_digests("docker.io/user/app:bad") == []

    image_meta.forget(["docker.io/user/app:dev"])
    assert image_meta.repo_digests("docker.io/user/app:dev") == []


def test_image_payloads_carry_the_real_identity():
    config_digest = "sha256:" + "b" * 64
    pushed = "docker.io/user/app@sha256:" + "c" * 64

    summary = images_mod.to_image_summary(
        "docker.io/user/app:dev",
        size=1234,
        config_digest=config_digest,
        repo_digests=[pushed],
    )
    assert summary["Id"] == config_digest
    assert summary["Size"] == 1234
    assert summary["RepoDigests"] == [pushed]

    inspect = images_mod.to_image_inspect(
        "docker.io/user/app:dev",
        size=1234,
        config_digest=config_digest,
        repo_digests=[pushed],
    )
    assert inspect["Id"] == config_digest
    assert inspect["Size"] == 1234
    assert inspect["VirtualSize"] == 1234
    assert inspect["RepoDigests"] == [pushed]


def test_unknown_images_keep_the_legacy_name_hash():
    assert images_mod.image_id("busybox:1.36") == images_mod.image_id(
        "busybox:1.36", ""
    )
    assert images_mod.image_id("busybox:1.36").startswith("sha256:")
