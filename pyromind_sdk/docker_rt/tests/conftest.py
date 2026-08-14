"""Shared fixtures for docker_rt API tests."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = Path(__file__).resolve().parent
for _p in (_ROOT, _TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from .helpers import FakeKubeEnv  # noqa: E402

# Avoid hitting a real cluster for ClusterIP Service DNS during unit tests.
os.environ.setdefault("DOCKER_RT_SERVICE_DNS", "false")
os.environ.setdefault("DOCKER_RT_INSPECT_MODE", "standard")


@pytest.fixture
def fake_kube() -> FakeKubeEnv:
    return FakeKubeEnv()


@pytest.fixture(autouse=True)
def _disable_pyromind_reconcile(monkeypatch):
    """API unit tests use FakeKubeEnv; avoid real k8s-middleware calls."""
    from .. import aio_server as mod

    async def _noop(*args, **kwargs):
        return {"adopted": 0, "reaped": 0}

    monkeypatch.setattr(mod, "reconcile_pyromind_sandboxes", _noop)
