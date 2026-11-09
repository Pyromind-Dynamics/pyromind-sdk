"""Optional blank inputs should use defaults without changing string inputs."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


wrapper = importlib.import_module("pyromind_sdk.nodes.function_call_wrapper")


def _run_wrapper(
    tmp_path: Path,
    source: str,
    inputs: dict,
    input_types: dict,
    input_dir: Path | None = None,
) -> dict:
    python_file = tmp_path / "node.py"
    python_file.write_text(source, encoding="utf-8")
    output = tmp_path / "result.json"
    with pytest.MonkeyPatch.context() as patch:
        if input_dir is None:
            patch.delenv("PYROMIND_SDK_INPUT_DIR", raising=False)
        else:
            patch.setenv("PYROMIND_SDK_INPUT_DIR", str(input_dir))
        wrapper.function_call_wrapper(
            python_code=str(python_file),
            function_name="run",
            inputs=inputs,
            input_types=input_types,
            output_paths={"result": str(output)},
        )
    return json.loads(output.read_text(encoding="utf-8"))


def test_private_input_files_apply_optional_defaults_and_python_defaults(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir(mode=0o700)
    names = ("count", "rate", "enabled", "choice", "text", "mixed")
    for name in names:
        path = input_dir / name
        path.write_text("", encoding="utf-8")
        path.chmod(0o600)

    result = _run_wrapper(
        tmp_path,
        "def run(count=1, rate=2.5, enabled=True, choice='A', text='default', mixed='default'):\n"
        "    return {'result': {'count': count, 'rate': rate, 'enabled': enabled, "
        "'choice': choice, 'text': text, 'mixed': mixed}}\n",
        {name: {"__pyromind_input_file__": name} for name in names},
        {
            "required": {},
            "optional": {
                "count": ["INT", {"default": 7}],
                "rate": ["FLOAT", {}],
                "enabled": ["BOOLEAN", {}],
                "choice": [["A", "B"], {}],
                "text": ["STRING", {}],
                "mixed": ["STRING|INT", {}],
            },
        },
        input_dir,
    )

    assert result == {
        "count": 7,
        "rate": 2.5,
        "enabled": True,
        "choice": "A",
        "text": "",
        "mixed": "",
    }


def test_old_input_files_keep_optional_blank_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    monkeypatch.setattr(wrapper, "_LEGACY_INPUT_ROOT", str(legacy_root))
    for name, contents in (("required_count", "3\n"), ("rate", "\n"), ("name", "\n")):
        (legacy_root / name).write_text(contents, encoding="utf-8")

    result = _run_wrapper(
        tmp_path,
        "def run(required_count, rate=1.0, name='x'):\n"
        "    return {'result': {'required_count': required_count, 'rate': rate, 'name': name}}\n",
        {name: f"/tmp/{name}" for name in ("required_count", "rate", "name")},
        {
            "required": {"required_count": ["INT", {}]},
            "optional": {"rate": ["FLOAT", {}], "name": ["STRING", {"default": "anonymous"}]},
        },
    )

    assert result == {"required_count": 3, "rate": 1.0, "name": "anonymous"}


@pytest.mark.parametrize("input_name,input_type,value", [
    ("required_count", "INT", ""),
    ("optional_count", "INT", "not-an-int"),
    ("optional_count", "INT", "  "),
])
def test_required_or_invalid_nonblank_numeric_still_fails(
    tmp_path: Path, input_name: str, input_type: str, value: str
) -> None:
    input_types = {"required": {}, "optional": {}}
    bucket = "required" if input_name.startswith("required") else "optional"
    input_types[bucket][input_name] = [input_type, {}]
    with pytest.raises(ValueError):
        _run_wrapper(
            tmp_path,
            "def run(**kwargs):\n    return {'result': kwargs}\n",
            {input_name: value},
            input_types,
        )
