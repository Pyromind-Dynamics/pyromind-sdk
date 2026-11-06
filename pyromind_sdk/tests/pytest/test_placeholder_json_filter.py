"""Placeholder render filters: {{name|json}} and {{name|jsonstr}}.

Shell quoting alone keeps a value out of the shell, but it does not escape the
value for a surrounding format. When a node author embeds a placeholder inside a
JSON literal, an unfiltered value can still restructure that JSON.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pyromind_sdk.nodes.command_executor import (
    execute_command_template,
    extract_placeholders,
    parse_placeholder_filters,
    replace_template,
)
from pyromind_sdk.nodes.yaml_loader import create_node_class_from_yaml

HOSTILE = '","extra":"injected'


def _shell(tmp_path: Path) -> str:
    installed = shutil.which("sh")
    if installed:
        return installed
    alias = tmp_path / "sh"
    alias.symlink_to("/bin/sh")
    return str(alias)


def _render(template: list, value: str, tmp_path: Path) -> str:
    result = execute_command_template(
        template, inputs={"x": value}, output_names=["output"], timeout=20
    )
    assert result.get("success"), result.get("errors")
    return (result.get("outputs") or {}).get("output", "")


def _json_template(tmp_path: Path, body: str) -> list:
    return [_shell(tmp_path), "-c", "printf %s '" + body + "' > {{output}}"]


def test_unfiltered_placeholder_is_shell_safe_but_not_json_escaped(tmp_path):
    """Documents why the filter is needed: the value stays data for the shell."""
    output = _render(_json_template(tmp_path, '{"payload":"{{x}}"}'), HOSTILE, tmp_path)

    assert json.loads(output) == {"payload": "", "extra": "injected"}


def test_jsonstr_filter_keeps_the_value_inside_its_string(tmp_path):
    output = _render(
        _json_template(tmp_path, '{"payload":"{{x|jsonstr}}"}'), HOSTILE, tmp_path
    )

    parsed = json.loads(output)
    assert parsed == {"payload": HOSTILE}
    assert "extra" not in parsed


def test_json_filter_emits_a_complete_json_literal(tmp_path):
    output = _render(
        _json_template(tmp_path, '{"payload": {{x|json}} }'), HOSTILE, tmp_path
    )

    parsed = json.loads(output)
    assert parsed == {"payload": HOSTILE}
    assert "extra" not in parsed


@pytest.mark.parametrize("value", ["hello", 'a"b', "a\\b", "line1\nline2", "héllo"])
def test_filters_round_trip_ordinary_values(tmp_path, value):
    quoted = json.loads(
        _render(_json_template(tmp_path, '{"p":"{{x|jsonstr}}"}'), value, tmp_path)
    )
    bare = json.loads(
        _render(_json_template(tmp_path, '{"p": {{x|json}} }'), value, tmp_path)
    )

    assert quoted == {"p": value}
    assert bare == {"p": value}


def test_unknown_filter_is_rejected(tmp_path):
    result = execute_command_template(
        _json_template(tmp_path, "{{x|bogus}}"),
        inputs={"x": "v"},
        output_names=["output"],
        timeout=20,
    )

    assert result.get("success") is False
    assert "Unsupported placeholder filter" in str(result.get("errors"))


def test_conflicting_filters_are_rejected(tmp_path):
    result = execute_command_template(
        [_shell(tmp_path), "-c", "echo {{x|json}} {{x|jsonstr}} > {{output}}"],
        inputs={"x": "v"},
        output_names=["output"],
        timeout=20,
    )

    assert result.get("success") is False
    assert "conflicting filters" in str(result.get("errors"))


def test_extract_placeholders_drops_filters():
    assert extract_placeholders(["echo {{a}} {{b|json}} {{c|jsonstr}}"]) == {"a", "b", "c"}
    assert parse_placeholder_filters("{{a}} {{b|json}}") == {"a": None, "b": "json"}


def test_replace_template_applies_filters():
    assert replace_template('{"p": "{{x|jsonstr}}"}', {"x": HOSTILE}) == (
        '{"p": "\\",\\"extra\\":\\"injected"}'
    )
    assert json.loads(replace_template('{"p": {{x|json}}}', {"x": HOSTILE})) == {
        "p": HOSTILE
    }
    assert replace_template("{{x}}", {"x": "v"}, exclude_keys={"x"}) == "{{x}}"


@pytest.mark.parametrize("field", ["command_template", "args_template", "test_command_template"])
def test_yaml_loader_rejects_filters_unrenderable_by_middleware(field):
    config = {
        "name": "JsonPayloadNode",
        "base_class": "PodExecutionNode",
        "command_template": ["echo", "{{x}}"],
        "parameters": [
            {"name": "x", "dtype": "STRING", "type": "input", "required_type": "required"},
        ],
    }
    config[field] = ["echo", "{{x|jsonstr}}"]

    with pytest.raises(ValueError, match="Filtered placeholders are not supported"):
        create_node_class_from_yaml(config, "JsonPayloadNode")
