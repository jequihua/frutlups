import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest


FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = FIXTURES / "v4_project"
MANIFEST = FIXTURES / "v4_project.manifest.json"


def evidence_sha(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def test_v4_project_fixture_matches_manifest() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest["files"]
    files = [path for path in FIXTURE.rglob("*") if path.is_file()]
    actual = {path.relative_to(FIXTURE).as_posix(): evidence_sha(path) for path in files}

    assert manifest["schema"] == "frutlups.fixture-manifest/1"
    assert manifest["root"] == FIXTURE.name
    assert sorted(actual) == sorted(expected)
    assert actual == expected


def test_v2_export_and_plural_corpora_match_raw_manifest():
    manifest = json.loads((FIXTURES / "v2_project.manifest.json").read_bytes())
    assert manifest["commit"] == "169a74c66140743008bef374f2c7d0389ee81cb9"
    rows = manifest["archive"]["files"]
    assert len(rows) == 67
    for row in rows:
        data = (FIXTURES / "v2_project" / row["path"]).read_bytes()
        assert len(data) == row["bytes"], row["path"]
        assert hashlib.sha256(data).hexdigest() == row["sha256_raw"], row["path"]
    assert {row["path"] for row in manifest["corpora"]} == {"protocol_v2.json", "autonomy_v1.json"}
    for row in manifest["corpora"]:
        data = (FIXTURES / row["path"]).read_bytes()
        assert len(data) == row["bytes"]
        assert hashlib.sha256(data).hexdigest() == row["sha256_raw"]


def test_every_v2_member_survives_clean_git_materialization(tmp_path):
    import shutil
    import subprocess

    manifest = json.loads((FIXTURES / "v2_project.manifest.json").read_bytes())
    source = tmp_path / "source"
    source.mkdir()
    relative = Path("tests/fixtures/v2_project")
    shutil.copyfile(FIXTURES.parent.parent / ".gitignore", source / ".gitignore")
    paths = [relative / row["path"] for row in manifest["archive"]["files"]]
    paths += [Path("tests/fixtures") / row["path"] for row in manifest["corpora"]]
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES.parent.parent / path, target)
    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args],
                              check=True, capture_output=True).stdout
    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    tracked = set(git("ls-files", "-z").decode().split("\0"))
    assert all(path.as_posix() in tracked for path in paths)
    clean = tmp_path / "clean"
    clean.mkdir()
    git("checkout-index", "--all", "--prefix=" + clean.as_posix() + "/")
    for path in paths:
        assert (clean / path).read_bytes() == (source / path).read_bytes(), path


DEVELOPMENT = FIXTURES / "v5_development"
DEVELOPMENT_MANIFEST = json.loads((FIXTURES / "v5_development.manifest.json").read_bytes())


def test_development_source_pin_and_exact_members():
    manifest = DEVELOPMENT_MANIFEST
    assert manifest["root"] == DEVELOPMENT.name
    assert manifest["commit"] == "bc4e9d1290119f54ec62d9e231826a68c2eea34c"
    assert manifest["source_manifest_raw_sha256"] == "4f1664abf830a98ee7ff33a6f2bac7be30afa1adbc74e3f09bd5add34aba8251"
    source = {key: manifest[key] for key in ("kind", "commit", "archive", "members")}
    assert hashlib.sha256((json.dumps(source, indent=2) + "\n").encode()).hexdigest() == manifest["source_manifest_raw_sha256"]
    rows = manifest["members"]
    assert len(rows) == 83
    names = {row["path"] for row in rows}
    assert len(names) == 83
    assert len({name.casefold() for name in names}) == 83
    actual = set()
    for path in DEVELOPMENT.rglob("*"):
        assert not path.is_symlink(), path
        if path.is_file():
            actual.add(path.relative_to(DEVELOPMENT).as_posix())
    assert actual == names
    for row in rows:
        name = row["path"]
        relative = PurePosixPath(name)
        windows = PureWindowsPath(name)
        assert not relative.is_absolute()
        assert not windows.drive and not windows.root
        assert relative.as_posix() == name
        assert "\\" not in name and ":" not in name
        assert all(part not in (".", "..") and not part.endswith((".", " ")) for part in relative.parts)
        assert row["git_mode"] == "100644"
        path = DEVELOPMENT / name
        data = path.read_bytes()
        assert len(data) == row["bytes"], name
        assert hashlib.sha256(data).hexdigest() == row["raw_sha256"], name
        if os.name != "nt":
            assert path.stat().st_mode & 0o111 == 0, name


def test_development_coverage_pin_and_predecessor_accounting():
    coverage = DEVELOPMENT_MANIFEST["coverage"]
    assert DEVELOPMENT_MANIFEST["case_inventory_raw_sha256"] == "37a1207c35cf335cb7145d15a010fa2466cf0b84162359f5a7251341f9a02216"
    canonical = json.dumps(coverage, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(canonical).hexdigest() == "054cfa1b3851b0a529896db7eac8e66980915910616536bca552ee92aa152cb8"
    assert coverage["count"] == 150
    assert coverage["shared_native"] == 147
    assert coverage["template_policy"] == 3
    cases = {(row["corpus"], row["case"]): row for row in coverage["cases"]}
    assert len(cases) == 150
    old = {(name, case["name"]): case for name in ("protocol_v2.json", "autonomy_v1.json")
           for case in json.loads((FIXTURES / name).read_bytes())["cases"]}
    predecessors = {(row["corpus"], row["case"]): row for row in coverage["D023_cases"]}
    assert len(predecessors) == 144
    assert predecessors.keys() == old.keys()
    assert old.keys() <= cases.keys()
    assert sum(row["prior_D023_case"] for row in cases.values()) == 144
    for key, row in predecessors.items():
        assert row["old_valid"] == old[key]["valid"], key
        assert row["new_case"] == key[1], key
        assert row["owner"] == cases[key]["consumer_owner"], key


@pytest.mark.parametrize("name,schema,count,shared,policy", [
    ("protocol_v2.json", "template.conformance/3", 16, 14, 2),
    ("autonomy_v1.json", "template.autonomy-conformance/2", 134, 133, 1),
])
def test_development_case_setup_and_complete_oracles(name, schema, count, shared, policy):
    # Integrity only: do not run an unqualified consumer or change a case's setup.
    corpus = json.loads((DEVELOPMENT / "tests" / "fixtures" / name).read_bytes())
    assert corpus["schema"] == schema
    assert len(corpus["cases"]) == count
    cases = {case["name"]: case for case in corpus["cases"]}
    assert len(cases) == count
    inventory = {row["case"]: row for row in DEVELOPMENT_MANIFEST["coverage"]["cases"]
                 if row["corpus"] == name}
    assert cases.keys() == inventory.keys()
    assert sum(case["target"] == "shared_native" for case in cases.values()) == shared
    assert sum(case["target"] == "template_policy" for case in cases.values()) == policy
    for case_name, case in cases.items():
        row = inventory[case_name]
        canonical = json.dumps(case, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical).hexdigest() == row["source_case_sha256"], case_name
        assert {key: value for key, value in case.items() if key != "events"} == row["oracle"], case_name
        assert len(case["events"]) == row["event_count"], case_name
        assert case["target"] == row["target"], case_name
        assert case["valid"] == row["valid"], case_name
        assert row["installed_owner"] == "M006-S02", case_name
        assert row["qualification"] == "inventory only; no execution or current-native parity claimed", case_name


def test_every_development_member_survives_clean_git_materialization(tmp_path):
    import shutil
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    relative = Path("tests/fixtures/v5_development")
    shutil.copyfile(FIXTURES.parent.parent / ".gitignore", source / ".gitignore")
    rows = DEVELOPMENT_MANIFEST["members"]
    paths = [relative / row["path"] for row in rows]
    paths.append(Path("tests/fixtures/v5_development.manifest.json"))
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES.parent.parent / path, target)

    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args],
                              check=True, capture_output=True, timeout=30).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    entries = {}
    for entry in git("ls-files", "--stage", "-z").decode().split("\0"):
        if entry:
            metadata, path = entry.split("\t")
            entries[path] = metadata.split()[0]
    assert set(entries) == {path.as_posix() for path in paths} | {".gitignore"}
    for row in rows:
        assert entries[(relative / row["path"]).as_posix()] == row["git_mode"], row["path"]
    # Exceptions admit the README only, not arbitrary local state or caches.
    ignored = [relative / "local_state/private.json", relative / "__pycache__/x.pyc",
               Path("local_state/private.json")]
    assert set(git("check-ignore", "--", *(p.as_posix() for p in ignored)).decode().splitlines()) == {
        p.as_posix() for p in ignored}
    clean = tmp_path / "clean"
    clean.mkdir()
    git("checkout-index", "--all", "--prefix=" + clean.as_posix() + "/")
    for path in paths:
        assert (clean / path).read_bytes() == (source / path).read_bytes(), path


FINAL = FIXTURES / "v5_final"
FINAL_RAW = (FIXTURES / "v5_final.manifest.json").read_bytes()
FINAL_MANIFEST = json.loads(FINAL_RAW)
FINAL_COMMIT = "711bce067eb0b3d05abf8cdec54f3cc812f62a5d"
FINAL_PINS = {
    "template-5.0.0.tar": (460800, "a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a"),
    "protocol_v2.json": (25323, "59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891"),
    "autonomy_v1.json": (343766, "ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150"),
}


def raw_identity(path: Path):
    data = path.read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()


def assert_safe_member_name(name):
    relative = PurePosixPath(name)
    windows = PureWindowsPath(name)
    assert not relative.is_absolute() and not windows.drive and not windows.root, name
    assert relative.as_posix() == name, name
    assert "\\" not in name and ":" not in name, name
    assert all(part not in (".", "..") and not part.endswith((".", " ")) for part in relative.parts), name


def test_final_archive_pin_and_safe_exact_members():
    import tarfile

    manifest = FINAL_MANIFEST
    assert hashlib.sha256(FINAL_RAW).hexdigest() == "c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c"
    assert manifest["commit"] == FINAL_COMMIT
    assert manifest["F_base_commit"] == "ae14bf4a52a021e8f5bc9ea50b85d36b98b0d55a"
    assert manifest["root"] == FINAL.name
    assert {path.name for path in FINAL.iterdir()} == set(FINAL_PINS) | {"project"}
    archive = manifest["archive"]
    assert archive["path"] == "v5_final/template-5.0.0.tar"
    assert raw_identity(FINAL / "template-5.0.0.tar") == (archive["bytes"], archive["raw_sha256"]) == FINAL_PINS["template-5.0.0.tar"]
    rows = {row["path"]: row for row in manifest["members"]}
    assert len(rows) == len(manifest["members"]) == archive["regular_files"] == 69
    assert sum(row["bytes"] for row in rows.values()) == archive["regular_file_bytes"] == 389077
    with tarfile.open(FINAL / "template-5.0.0.tar") as source:
        # The tar's own commit comment, not the fixture name, binds the export.
        assert source.pax_headers == {"comment": FINAL_COMMIT} == {"comment": archive["pax_comment"]}
        members = source.getmembers()
        names = [member.name for member in members]
        assert len(names) == len(set(names)) == len({name.casefold() for name in names}) == 92
        directories = set()
        for member in members:
            assert_safe_member_name(member.name)
            assert not member.issym() and not member.islnk(), member.name
            if member.isdir():
                assert member.mode == 0o775, member.name
                directories.add(member.name)
                continue
            assert member.isfile(), member.name
            row = rows[member.name]
            # Tar 0664 and Git 100644 are separate recorded domains.
            assert member.mode == 0o664 and row["tar_mode"] == "0664", member.name
            assert row["git_mode"] == "100644", member.name
            data = source.extractfile(member).read()
            assert (len(data), hashlib.sha256(data).hexdigest()) == (row["bytes"], row["raw_sha256"]), member.name
            assert (FINAL / "project" / member.name).read_bytes() == data, member.name
    assert directories == set(archive["directories"])
    assert set(names) == directories | set(rows)
    actual = set()
    for path in (FINAL / "project").rglob("*"):
        assert not path.is_symlink(), path
        if path.is_file():
            actual.add(path.relative_to(FINAL / "project").as_posix())
            if os.name != "nt":
                assert path.stat().st_mode & 0o111 == 0, path
    assert actual == set(rows)


def test_final_corpora_are_separate_same_commit_development_identical_files():
    rows = {row["path"]: row for row in FINAL_MANIFEST["corpora"]}
    assert rows.keys() == {"protocol_v2.json", "autonomy_v1.json"}
    for name, row in rows.items():
        assert raw_identity(FINAL / name) == (row["bytes"], row["raw_sha256"]) == FINAL_PINS[name]
        assert row["archive_member"] is False
        assert row["source_path"] == "tests/fixtures/" + name
        assert row["identical_to_development"] == "v5_development/tests/fixtures/" + name
        assert (FINAL / name).read_bytes() == (DEVELOPMENT / row["source_path"]).read_bytes()
        corpus = json.loads((FINAL / name).read_bytes())
        assert (corpus["schema"], len(corpus["cases"])) == (row["schema"], row["cases"])
        assert row["source_path"] not in {member["path"] for member in FINAL_MANIFEST["members"]}
    assert (rows["protocol_v2.json"]["cases"], rows["autonomy_v1.json"]["cases"]) == (16, 134)


def test_final_case_accounting_preserves_development_inventory_and_predecessors():
    coverage = FINAL_MANIFEST["coverage"]
    assert (coverage["count"], coverage["shared_native"], coverage["template_policy"],
            coverage["D023_predecessors"]) == (150, 147, 3, 144)
    rows = {(row["corpus"], row["case"]): row for row in coverage["cases"]}
    assert len(rows) == len(coverage["cases"]) == 150
    previous = {(row["corpus"], row["case"]): row for row in DEVELOPMENT_MANIFEST["coverage"]["cases"]}
    predecessors = {(row["corpus"], row["case"]) for row in DEVELOPMENT_MANIFEST["coverage"]["D023_cases"]}
    old = {(name, case["name"]) for name in ("protocol_v2.json", "autonomy_v1.json")
           for case in json.loads((FIXTURES / name).read_bytes())["cases"]}
    assert rows.keys() == previous.keys()
    assert {key for key, row in rows.items() if row["prior_D023_case"]} == predecessors == old
    consumers = {"protocol_v2.json": "test_protocol_v2.py::test_base_event_corpus",
                 "autonomy_v1.json": "test_autonomy.py::test_profile_case"}
    actual = {}
    for name in consumers:
        cases = json.loads((FINAL / name).read_bytes())["cases"]
        assert len({case["name"] for case in cases}) == len(cases)
        actual.update({(name, case["name"]): case for case in cases})
    assert actual.keys() == rows.keys()
    for key, row in rows.items():
        case, before = actual[key], previous[key]
        canonical = json.dumps(case, sort_keys=True, separators=(",", ":")).encode()
        assert hashlib.sha256(canonical).hexdigest() == row["source_case_sha256"] == before["source_case_sha256"], key
        assert (case["target"], case["valid"]) == (row["target"], row["valid"]) == (before["target"], before["valid"]), key
        assert row["development_owner"] == before["consumer_owner"], key
        assert row["development_disposition"] == before["disposition"], key
        assert row["delta"] == "byte-identical case; original dispositions retained", key
        assert row["final_reader_node"] == consumers[key[0]] + "[" + key[1] + "]", key
        if row["target"] == "template_policy":
            assert "not template-policy execution" in row["qualification"], key
        else:
            assert row["target"] == "shared_native", key
            assert "no public, installed or live claim" in row["qualification"], key


def test_final_distribution_source_and_development_delta_accounting():
    membership = FINAL_MANIFEST["membership"]
    assert membership["development_commit"] == DEVELOPMENT_MANIFEST["commit"]
    source = {row["path"]: row for row in membership["source_files"]}
    distribution = {row["path"]: row for row in FINAL_MANIFEST["members"]}
    development = {row["path"]: row for row in DEVELOPMENT_MANIFEST["members"]}
    assert (membership["distribution"], membership["source"], membership["development"]) == (69, 85, 83)
    assert (len(distribution), len(source), len(development)) == (69, 85, 83)
    assert membership["source_only"] == sorted(source.keys() - distribution.keys())
    assert all(path.startswith("tests/") for path in membership["source_only"])
    for path, row in distribution.items():
        assert (row["bytes"], row["raw_sha256"]) == (source[path]["bytes"], source[path]["raw_sha256"]), path
    changed = {path for path in source.keys() & development.keys()
               if source[path]["raw_sha256"] != development[path]["raw_sha256"]}
    added = source.keys() - development.keys()
    assert not development.keys() - source.keys()
    delta = {row["path"]: row for row in FINAL_MANIFEST["development_delta"]}
    assert len(delta) == len(FINAL_MANIFEST["development_delta"]) == 33
    assert delta.keys() == changed | added
    assert {path for path, row in delta.items() if row["status"] == "A"} == added == {"docs/glossary.md", "docs/walkthrough.md"}
    groups = {}
    for path, row in delta.items():
        groups.setdefault(row["group"], set()).add(path)
        assert row["in_distribution"] == (path in distribution), path
    assert groups["release_identity"] == {"README.md", "pyproject.toml"}
    assert groups["configured_git_selection"] == {"scripts/_common.py", "scripts/_git.py"}
    assert groups["selector_and_evidence_presentation"] == {"scripts/_evidence.py", "scripts/_findings.py", "scripts/prompt.py"}
    assert groups["development_tests"] == {path for path in delta if path.startswith("tests/")}
    assert len(groups["development_tests"]) == 9
    assert all(path.endswith(".md") for path in groups["documentation_and_instructions"])
    assert sum(map(len, groups.values())) == 33
    # Only README/pyproject changed in the last release commit, not the whole delta.
    assert FINAL_MANIFEST["release_checkpoint_delta"] == ["M\tREADME.md", "M\tpyproject.toml"]
    checker = json.loads((FIXTURES / "template_4632a891.manifest.json").read_bytes())
    pinned = {row["path"]: row["raw_sha256"] for row in checker["members"]}
    assert FINAL_MANIFEST["checker_pin_delta"]["from"] == checker["commit"]
    scripts = {path for path in distribution if path.startswith("scripts/")}
    assert {path for path in scripts if pinned[path] != distribution[path]["raw_sha256"]} == {
        line.split("\t")[1] for line in FINAL_MANIFEST["checker_pin_delta"]["changed_scripts"]}


def test_every_final_member_survives_clean_git_materialization(tmp_path):
    import shutil
    import subprocess

    source = tmp_path / "source"
    source.mkdir()
    relative = Path("tests/fixtures/v5_final")
    shutil.copyfile(FIXTURES.parent.parent / ".gitignore", source / ".gitignore")
    rows = FINAL_MANIFEST["members"]
    paths = [relative / "project" / row["path"] for row in rows]
    paths += [relative / name for name in FINAL_PINS]
    paths.append(Path("tests/fixtures/v5_final.manifest.json"))
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES.parent.parent / path, target)

    def git(*args):
        return subprocess.run(["git", "-C", str(source), *args],
                              check=True, capture_output=True, timeout=30).stdout

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", ".")
    entries = {}
    for entry in git("ls-files", "--stage", "-z").decode().split("\0"):
        if entry:
            metadata, path = entry.split("\t")
            entries[path] = metadata.split()[0]
    assert set(entries) == {path.as_posix() for path in paths} | {".gitignore"}
    for row in rows:
        assert entries[(relative / "project" / row["path"]).as_posix()] == row["git_mode"], row["path"]
    assert all(mode == "100644" for mode in entries.values())
    # Exceptions admit the README only, not arbitrary local state or caches.
    ignored = [relative / "project/local_state/private.json", relative / "project/__pycache__/x.pyc",
               relative / "project/scripts/__pycache__/x.pyc", relative / "project/frutlups.local.toml",
               relative / "local_state/private.json", Path("local_state/private.json")]
    assert set(git("check-ignore", "--", *(p.as_posix() for p in ignored)).decode().splitlines()) == {
        p.as_posix() for p in ignored}
    clean = tmp_path / "clean"
    clean.mkdir()
    git("checkout-index", "--all", "--prefix=" + clean.as_posix() + "/")
    for path in paths:
        assert (clean / path).read_bytes() == (source / path).read_bytes(), path
