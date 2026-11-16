"""Keep staged /tmp inputs readable without following paths outside /tmp."""

from pathlib import Path
from tempfile import TemporaryDirectory

from pyromind_sdk.nodes.function_call_wrapper import (
    _read_tmp_file_if_exists,
    _resolve_inputs_from_tmp_files,
)


def test_parent_traversal_stays_literal() -> None:
    value = "/tmp/../etc/hosts"
    assert Path(value).resolve().is_file()
    assert _resolve_inputs_from_tmp_files({"text": value}) == {"text": value}


def test_symlink_outside_tmp_stays_literal() -> None:
    with TemporaryDirectory(dir="/tmp") as directory:
        link = Path(directory) / "outside"
        link.symlink_to("/etc/hosts")
        assert _read_tmp_file_if_exists(str(link)) == str(link)


def test_staged_tmp_file_still_reads_contents() -> None:
    with TemporaryDirectory(dir="/tmp") as directory:
        staged = Path(directory) / "text"
        staged.write_text("prompt body\n", encoding="utf-8")
        assert _resolve_inputs_from_tmp_files({"text": str(staged)}) == {
            "text": "prompt body"
        }
