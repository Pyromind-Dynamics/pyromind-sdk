"""Keep old generated input files compatible without reading arbitrary paths."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest

wrapper = importlib.import_module("pyromind_sdk.nodes.function_call_wrapper")


@pytest.fixture
def legacy_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "legacy-inputs"
    directory.mkdir()
    monkeypatch.setattr(wrapper, "_LEGACY_INPUT_ROOT", str(directory))
    return directory


def _input_types(**types: str) -> dict:
    return {"required": {name: [dtype, {}] for name, dtype in types.items()}, "optional": {}}


def _write_legacy(directory: Path, name: str, contents: str, mode: int = 0o644) -> str:
    path = directory / name
    path.write_text(contents, encoding="utf-8")
    path.chmod(mode)
    return f"/tmp/{name}"


def _call(tmp_path: Path, inputs: dict, input_types: dict, input_dir: Path | None = None) -> dict:
    module_file = tmp_path / "node.py"
    module_file.write_text("def run(**values):\n    return {'result': values}\n", encoding="utf-8")
    output = tmp_path / "result.json"
    with pytest.MonkeyPatch.context() as patch:
        if input_dir is None:
            patch.delenv("PYROMIND_SDK_INPUT_DIR", raising=False)
        else:
            patch.setenv("PYROMIND_SDK_INPUT_DIR", str(input_dir))
        wrapper.function_call_wrapper(
            python_code=str(module_file),
            function_name="run",
            inputs=inputs,
            input_types=input_types,
            output_paths={"result": str(output)},
        )
    return json.loads(output.read_text(encoding="utf-8"))


def test_old_generated_payload_reads_files_before_converting_types(
    tmp_path: Path, legacy_root: Path
) -> None:
    inputs = {
        "lora_rank": _write_legacy(legacy_root, "lora_rank", "16\n"),
        "num_epochs": _write_legacy(legacy_root, "num_epochs", "1.5\n"),
        "enabled": _write_legacy(legacy_root, "enabled", "true\n"),
        "prompt": _write_legacy(legacy_root, "prompt", "  first\nsecond \n\n"),
        "resume_from_checkpoint": _write_legacy(legacy_root, "resume_from_checkpoint", "\n"),
    }
    input_types = _input_types(lora_rank="INT", num_epochs="FLOAT", enabled="BOOLEAN", prompt="STRING")
    input_types["optional"]["resume_from_checkpoint"] = ["STRING", {"default": ""}]

    result = _call(tmp_path, inputs, input_types)

    assert result == {
        "lora_rank": 16,
        "num_epochs": 1.5,
        "enabled": True,
        "prompt": "  first\nsecond ",
        "resume_from_checkpoint": "",
    }
    assert (legacy_root / "lora_rank").stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("contents,expected", [("false\n", False), ("0\n", False), ("yes\n", True)])
def test_legacy_boolean_contents_are_converted(
    tmp_path: Path, legacy_root: Path, contents: str, expected: bool
) -> None:
    inputs = {"flag": _write_legacy(legacy_root, "flag", contents)}
    assert _call(tmp_path, inputs, _input_types(flag="BOOLEAN")) == {"flag": expected}


@pytest.mark.parametrize("inputs,input_types", [
    ({"text": "/tmp/secret"}, _input_types(text="STRING")),
    ({"text": "/tmp/./text"}, _input_types(text="STRING")),
    ({"text": "/tmp/text/child"}, _input_types(text="STRING")),
    ({"text": "/var/tmp/text"}, _input_types(text="STRING")),
    ({"text": "/tmp/text", "count": 2}, _input_types(text="STRING", count="INT")),
    ({"text": "/tmp/text"}, _input_types(text="STRING", count="INT")),
    ({"text": "/tmp/text", "extra": "/tmp/extra"}, _input_types(text="STRING")),
    ({"text": {"path": "/tmp/text"}}, _input_types(text="STRING")),
    ({"text": ["/tmp/text"]}, _input_types(text="STRING")),
    ({"../secret": "/tmp/../secret"}, _input_types(**{"../secret": "STRING"})),
])
def test_only_the_complete_declared_legacy_shape_can_open_files(
    monkeypatch: pytest.MonkeyPatch, inputs: dict, input_types: dict
) -> None:
    def unexpected_open(*args, **kwargs):
        pytest.fail("Non-legacy input must not trigger any file reads")

    monkeypatch.setattr(wrapper.os, "open", unexpected_open)
    assert wrapper._resolve_legacy_input_files(inputs, input_types) == inputs


def test_old_generated_payload_still_works_with_private_dir_env(
    tmp_path: Path, legacy_root: Path
) -> None:
    _write_legacy(legacy_root, "text", "legacy content\n")
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)

    result = _call(tmp_path, {"text": "/tmp/text"}, _input_types(text="STRING"), directory)

    assert result == {"text": "legacy content"}


def test_nonlegacy_tmp_path_stays_literal_with_private_dir_env(
    tmp_path: Path, legacy_root: Path
) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)

    result = _call(tmp_path, {"text": "/tmp/other"}, _input_types(text="STRING"), directory)

    assert result == {"text": "/tmp/other"}


def test_explicit_file_protocol_still_preserves_trailing_newlines(
    tmp_path: Path, legacy_root: Path
) -> None:
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "text"
    path.write_text("  keep\n\n", encoding="utf-8")
    path.chmod(0o600)
    inputs = {"text": {"__pyromind_input_file__": "text"}}

    assert _call(tmp_path, inputs, _input_types(text="STRING"), directory) == {"text": "  keep\n\n"}


def test_explicit_reference_without_input_dir_is_not_treated_as_legacy(
    tmp_path: Path, legacy_root: Path
) -> None:
    _write_legacy(legacy_root, "text", "must not be read\n")
    with pytest.raises(ValueError, match="requires a private input directory"):
        _call(tmp_path, {"text": {"__pyromind_input_file__": "text"}}, _input_types(text="STRING"))


def test_recognized_legacy_payload_reports_missing_file(legacy_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        wrapper._resolve_legacy_input_files({"count": "/tmp/count"}, _input_types(count="INT"))


@pytest.mark.parametrize("mode", [0o620, 0o602, 0o666])
def test_legacy_files_cannot_be_writable_by_other_users(legacy_root: Path, mode: int) -> None:
    inputs = {"count": _write_legacy(legacy_root, "count", "42\n", mode)}
    with pytest.raises(ValueError, match="Unsafe legacy input file"):
        wrapper._resolve_legacy_input_files(inputs, _input_types(count="INT"))


def test_legacy_files_must_belong_to_the_current_user(
    legacy_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = {"count": _write_legacy(legacy_root, "count", "42\n")}
    foreign_uid = os.geteuid() + 1
    monkeypatch.setattr(wrapper.os, "geteuid", lambda: foreign_uid)
    with pytest.raises(ValueError, match="Unsafe legacy input file"):
        wrapper._resolve_legacy_input_files(inputs, _input_types(count="INT"))


def test_legacy_files_cannot_be_symlinks(legacy_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "secret"
    target.write_text("private data", encoding="utf-8")
    (legacy_root / "count").symlink_to(target)
    with pytest.raises(OSError):
        wrapper._resolve_legacy_input_files({"count": "/tmp/count"}, _input_types(count="INT"))


def test_legacy_files_cannot_be_hardlinks(legacy_root: Path, tmp_path: Path) -> None:
    target = tmp_path / "secret"
    target.write_text("private data", encoding="utf-8")
    os.link(target, legacy_root / "count")
    with pytest.raises(ValueError, match="Unsafe legacy input file"):
        wrapper._resolve_legacy_input_files({"count": "/tmp/count"}, _input_types(count="INT"))


@pytest.mark.parametrize("file_type", ["directory", "fifo"])
def test_legacy_inputs_must_be_regular_files(legacy_root: Path, file_type: str) -> None:
    path = legacy_root / "count"
    if file_type == "directory":
        path.mkdir()
    else:
        os.mkfifo(path, 0o600)
    with pytest.raises(ValueError, match="Unsafe legacy input file"):
        wrapper._resolve_legacy_input_files({"count": "/tmp/count"}, _input_types(count="INT"))
