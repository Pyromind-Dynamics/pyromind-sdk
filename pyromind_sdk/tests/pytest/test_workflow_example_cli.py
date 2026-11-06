"""Validate the documented example CLI without contacting an API."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyromind_sdk.examples.openapi import workflow_cli


NODE_INFO = {
    "Producer": {
        "input": {"required": {}, "optional": {}},
        "output": ["STRING"],
        "output_name": ["result"],
    }
}


def _workflow() -> dict:
    return {
        "id": "workflow",
        "nodes": [{"id": "one", "data": {"nodeType": "Producer", "config": {}}}],
        "edges": [],
    }


def _run_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workflow: object, *args: str) -> int:
    input_path = tmp_path / "workflow.json"
    input_path.write_text(json.dumps(workflow), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["workflow_cli.py", "validate", str(input_path), *args])
    with pytest.raises(SystemExit) as exc:
        workflow_cli.main()
    return exc.value.code


def _mock_client(monkeypatch: pytest.MonkeyPatch, result: object = NODE_INFO, error: Exception | None = None):
    state = SimpleNamespace(created=0, fetched=0, closed=0)

    class Client:
        def __init__(self):
            state.created += 1
            self.studio = SimpleNamespace(get_node_info=self.get_node_info)

        def get_node_info(self):
            state.fetched += 1
            if error is not None:
                raise error
            return result

        def close(self):
            state.closed += 1

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()

    monkeypatch.setattr(workflow_cli, "PyroMindAPIClient", Client)
    return state


@pytest.mark.parametrize("args", [(), ("--with-node-info",)])
def test_xyflow_validation_fetches_definitions_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, args: tuple[str, ...]
) -> None:
    state = _mock_client(monkeypatch)
    assert _run_cli(monkeypatch, tmp_path, _workflow(), *args) == 0
    assert (state.created, state.fetched, state.closed) == (1, 1, 1)


def test_failed_definition_fetch_fails_validation_and_closes_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    state = _mock_client(monkeypatch, error=RuntimeError("API unavailable"))
    assert _run_cli(monkeypatch, tmp_path, _workflow()) == 1
    assert (state.created, state.fetched, state.closed) == (1, 1, 1)
    assert "Node definitions are required" in capsys.readouterr().out


@pytest.mark.parametrize("node_info", [{}, []])
def test_missing_or_malformed_definitions_fail_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, node_info: object
) -> None:
    state = _mock_client(monkeypatch, result=node_info)
    assert _run_cli(monkeypatch, tmp_path, _workflow()) == 1
    assert state.closed == 1


@pytest.mark.parametrize("workflow", [
    {
        "id": "workflow",
        "nodes": [{"id": "one", "data": {"nodeType": "PrimitiveNode", "config": {"value": "hello"}}}],
        "edges": [],
    },
    {
        "version": "1.0",
        "nodes": {"producer": {"index": "one", "type": "Producer", "inputs": {}, "outputs": ["result"]}},
    },
])
def test_primitive_only_and_lite_validation_need_no_client_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workflow: dict
) -> None:
    def unexpected_client():
        pytest.fail("This validation does not require API access")

    monkeypatch.setattr(workflow_cli, "PyroMindAPIClient", unexpected_client)
    assert _run_cli(monkeypatch, tmp_path, workflow) == 0


def test_explicit_lite_definition_fetch_failure_fails_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _mock_client(monkeypatch, error=RuntimeError("API unavailable"))
    workflow = {
        "version": "1.0",
        "nodes": {"producer": {"index": "one", "type": "Producer", "inputs": {}, "outputs": ["result"]}},
    }
    assert _run_cli(monkeypatch, tmp_path, workflow, "--with-node-info") == 1
    assert state.closed == 1


def test_convert_with_unavailable_definitions_retains_generic_conversion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    state = _mock_client(monkeypatch, error=RuntimeError("API unavailable"))
    input_path = tmp_path / "workflow.json"
    output_path = tmp_path / "workflow.lite.json"
    input_path.write_text(json.dumps(_workflow()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["workflow_cli.py", "convert", str(input_path), str(output_path), "--with-node-info"])
    with pytest.raises(SystemExit) as exc:
        workflow_cli.main()
    assert exc.value.code == 0
    assert output_path.exists()
    assert state.closed == 1
    assert "generic parameter extraction" in capsys.readouterr().out


def test_non_object_input_fails_with_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    assert _run_cli(monkeypatch, tmp_path, []) == 1
    assert "Workflow must be a JSON object" in capsys.readouterr().out
