from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from ..backend import container_map as mod


def test_container_map_roundtrip(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    map_file = tmp_path / "map.json"
    monkeypatch.setattr(mod, "CONTAINER_MAP_FILE", map_file)

    mod.set_mapping("local-1", "sb-1")
    assert mod.sandbox_to_local("sb-1") == "local-1"
    assert mod.load_map() == {"local-1": "sb-1"}

    mod.remove_mapping("local-1", "sb-1")
    assert mod.load_map() == {}


def test_container_map_remove_by_sandbox(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    map_file = tmp_path / "map.json"
    monkeypatch.setattr(mod, "CONTAINER_MAP_FILE", map_file)

    mod.set_mapping("local-1", "sb-1")
    mod.remove_mapping("", "sb-1")

    assert mod.load_map() == {}
