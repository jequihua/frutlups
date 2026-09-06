import ast
import inspect
from pathlib import Path

import pytest
from frutlups import config, gitws, ledger, render, roadmap
from frutlups._paths import repo_path, safe_rel, value


@pytest.mark.parametrize("path", ("../escape", "/absolute", "C:/drive", "bad\\path"))
def test_safe_rel_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="unsafe repository-relative path"):
        safe_rel(path)


def test_repo_path_returns_a_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    assert repo_path(root, "folder/file.txt") == root / "folder/file.txt"


def test_value_reads_mappings_and_attributes() -> None:
    record = type("Record", (), {"path": "attribute"})()

    assert value({"path": "mapping"}, "path") == "mapping"
    assert value(record, "path") == "attribute"


def test_path_and_record_helpers_have_one_home() -> None:
    duplicates = {
        config: {"_relative"},
        roadmap: {"_safe_rel"},
        ledger: {"_safe_rel", "_repo_path"},
        gitws: {"_repo_path", "_value"},
        render: {"_value"},
    }

    for module, forbidden in duplicates.items():
        names = {
            node.name for node in ast.walk(ast.parse(inspect.getsource(module)))
            if isinstance(node, ast.FunctionDef)
        }
        assert names.isdisjoint(forbidden)
