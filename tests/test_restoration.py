"""Cheap restoration contracts; no prepared Git histories or native symlinks."""
import ast
from pathlib import Path

import pytest

from _restoration import restore_file


@pytest.mark.parametrize("original", [b"original\r\n\x00", None])
def test_restore_only_the_named_file(tmp_path, original):
    target, neighbor = tmp_path / "changed", tmp_path / "neighbor"
    target.write_bytes(b"mutated")
    neighbor.write_bytes(b"preserved")
    restore_file(tmp_path, target, original)
    if original is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == original
    assert neighbor.read_bytes() == b"preserved"
    assert sorted(p.name for p in tmp_path.iterdir()) == (["neighbor"] if original is None else ["changed", "neighbor"])


@pytest.mark.parametrize("outside,symlink", [(True, False), (False, True)])
@pytest.mark.parametrize("original", [b"restored", None])
def test_refusal_precedes_every_write_or_unlink(tmp_path, monkeypatch, outside, symlink, original):
    root = tmp_path / "owned"
    root.mkdir()
    target = (tmp_path if outside else root) / "target"
    target.write_bytes(b"unchanged")
    if symlink:
        is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda p: True if p == target else is_symlink(p))
    mutations = []
    def forbidden(path, *args, **kwargs):
        mutations.append(path)
        pytest.fail("refusal allowed a write or unlink")
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)
    with pytest.raises(ValueError, match="owned root without symlinks"):
        restore_file(root, target, original)
    assert mutations == []
    assert target.read_bytes() == b"unchanged"


@pytest.mark.parametrize("module,independent,grouped,variants", [
    ("test_checkpoints_v2.py", "test_interrupted_merge_refuses_drift_without_adoption",
     "test_interrupted_merge_reversible_drift_group", ("report", "neighbor", "product", "runtime")),
    ("test_milestones_v2.py", "test_completed_holistic_candidate_drift_refuses_reuse",
     "test_completed_holistic_reversible_drift_group", ("product", "runtime", "neighbor", "output")),
])
def test_exact_six_story_selection(module, independent, grouped, variants):
    tree = ast.parse(Path(__file__).with_name(module).read_text())
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    decorator, = functions[independent].decorator_list
    assert ast.literal_eval(decorator.args[0]) == "drift"
    assert ast.literal_eval(decorator.args[1]) == ["index", "head"]
    loops = [n for n in functions[grouped].body if isinstance(n, ast.For)]
    assert len(loops) == 1
    assert ast.literal_eval(loops[0].iter) == variants
    stories = (*variants, "index", "head")
    assert len(stories) == len(set(stories)) == 6
