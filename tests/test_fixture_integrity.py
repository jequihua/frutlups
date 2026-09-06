import hashlib
import json
from pathlib import Path


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
