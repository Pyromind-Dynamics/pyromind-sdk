"""The packaged rift-client binaries must match the Go sources."""

from __future__ import annotations

import pytest

from ..backend.rift import source_fingerprint


def test_rift_binaries_match_the_go_sources() -> None:
    if not any(binary.is_file() for binary in source_fingerprint.BINARIES):
        pytest.skip("rift-client binaries are not built in this checkout")
    assert source_fingerprint.problems() == []
