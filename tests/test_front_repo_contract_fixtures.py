"""Integrity checks for the self-contained ``front_repo_contract`` fixture bundle.

The shipped product tests read three repository input families (the accepted
coding/review/self-report prompt scaffolds, the accepted template-v3 layout, and
the pinned OKF/profile reference checker) that used to live above the test tree
through a fixed two-parent lookup. Those inputs are now copied byte-for-byte,
with provenance,
into ``fixtures/front_repo_contract/`` so the flattened front-facing repository
runs its complete suite without reading a parent, sibling, development prompt
directory, root layout, or development checker. Earlier versions located those
inputs through a fixed two-parent test-file assumption; this module now forbids
that release-portability regression.

This module pins that bundle: the manifest is complete and well formed, every
declared fixture exists with the exact committed digest, every destination stays
inside the bundle, and the directory holds nothing beyond the manifest and the
declared fixtures. It deliberately does not re-implement the checker or a parser.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

_TEST_ROOT = Path(__file__).resolve().parent
_BUNDLE = _TEST_ROOT / "fixtures" / "front_repo_contract"
_MANIFEST = _BUNDLE / "manifest.json"
_RELEASE_BUNDLE = _TEST_ROOT / "fixtures" / "release_v0_2_0"
_RELEASE_MANIFEST = _RELEASE_BUNDLE / "manifest.json"
_RELEASE_MANIFEST_SHA256 = "85f2aba1cd7f4df2256f186d84af2168d78b6af61595a9f8cc27052f32305676"

# The exact accepted-commit provenance the bundle must preserve. Pinned here so a
# silent drift in either the manifest or a copied fixture fails loudly.
_SOURCE_COMMIT = "80f9b52e10ba278b7c44c476fcf3cd525f22e987"
_EXPECTED = {
    "coding_prompt.md": (
        "prompts/templates/coding_prompt.md",
        "05c2f34172da7f59eb0eadfd51bba0b09bc5ce2d6edd49e2515a59d3443dc917",
        "configured coding scaffold",
    ),
    "review_prompt.md": (
        "prompts/templates/review_prompt.md",
        "2741646b8c014101b7e7c4b36f08412eb8830bd3f61d28a6c9c925a91ba1a4a2",
        "configured review scaffold",
    ),
    "self_report.md": (
        "prompts/templates/self_report.md",
        "72ca37cbbdfc4581d17b0d6dc6b3a0c59cd3b9be3883fa9ea7b902105b9a13ac",
        "configured self-report scaffold",
    ),
    "frutlups.layout.yaml": (
        "frutlups.layout.yaml",
        "963dd4ae11fa4a762e1dde1e2d48289f64f29e07238455bfd5f5ef5ae1774c48",
        "template-v3 layout contract",
    ),
    "okf_yaml_profile.py": (
        "scripts/okf_yaml_profile.py",
        "8cb8035839866fd78289b67ac5da02ffb42dce28c2f5cf72d8287d2d462959ef",
        "test-only OKF/profile reference oracle",
    ),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FrontRepoContractFixtureBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_MANIFEST.is_file(), "fixture bundle manifest is missing")
        self.manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        self.records = {rec["destination"]: rec for rec in self.manifest["fixtures"]}

    def test_manifest_records_the_accepted_source_commit(self) -> None:
        self.assertEqual(self.manifest["source_commit"], _SOURCE_COMMIT)

    def test_manifest_is_complete_and_well_formed(self) -> None:
        # Exactly the expected destinations, each with full provenance fields and
        # no machine-local value anywhere in the manifest text.
        self.assertEqual(set(self.records), set(_EXPECTED))
        for dest, rec in self.records.items():
            src, digest, purpose = _EXPECTED[dest]
            self.assertEqual(rec["source_path"], src, dest)
            self.assertEqual(rec["destination"], dest)
            self.assertEqual(rec["sha256"], digest, dest)
            self.assertEqual(rec["purpose"], purpose, dest)
            self.assertFalse(Path(rec["source_path"]).is_absolute(), dest)
        text = _MANIFEST.read_text(encoding="utf-8")
        for forbidden in ("C:" "\\", "C:/", "/home/", "/Users/"):
            self.assertNotIn(forbidden, text)

    def test_every_declared_fixture_exists_with_the_committed_digest(self) -> None:
        for dest, (_src, digest, _purpose) in _EXPECTED.items():
            path = _BUNDLE / dest
            self.assertTrue(path.is_file(), f"missing fixture: {dest}")
            self.assertEqual(_sha256(path), digest, f"digest drift: {dest}")

    def test_every_destination_stays_inside_the_bundle(self) -> None:
        bundle = _BUNDLE.resolve()
        for dest in self.records:
            resolved = (_BUNDLE / dest).resolve()
            self.assertTrue(
                resolved == bundle or bundle in resolved.parents,
                f"fixture escapes the bundle: {dest}",
            )
            self.assertEqual(Path(dest).name, dest, f"nested destination: {dest}")

    def test_bundle_holds_only_the_manifest_and_declared_fixtures(self) -> None:
        present = {p.name for p in _BUNDLE.iterdir() if p.is_file()}
        self.assertEqual(present, {"manifest.json"} | set(_EXPECTED))
        self.assertEqual(
            [p.name for p in _BUNDLE.iterdir() if p.is_dir() and p.name != "__pycache__"], [],
            "unexpected subdirectory in the fixture bundle",
        )


class ReleaseTestAuthorityBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(_RELEASE_MANIFEST.is_file(), "release-authority manifest is missing")
        self.manifest = json.loads(_RELEASE_MANIFEST.read_text(encoding="utf-8"))

    def test_manifest_pins_both_released_authorities(self) -> None:
        self.assertEqual(_sha256(_RELEASE_MANIFEST), _RELEASE_MANIFEST_SHA256)
        self.assertEqual(self.manifest["schema"], "frutlups.release_test_authority.v1")
        self.assertEqual(self.manifest["frutlups_source"]["tag"], "v0.2.0")
        self.assertEqual(
            self.manifest["frutlups_source"]["peeled_commit"],
            "a5a5750f09830e3c1405c0fb0432efd528c00b97",
        )
        self.assertEqual(self.manifest["drive_source"]["tag"], "v0.6.0")
        self.assertEqual(
            self.manifest["drive_source"]["peeled_commit"],
            "adf7092f51b2e5cffceba271f9d723f50b0d4028",
        )

    def test_manifest_is_a_complete_digest_map_of_portable_authority_bytes(self) -> None:
        records = self.manifest["members"]
        paths = [record["path"] for record in records]
        self.assertEqual(self.manifest["member_count"], 158)
        self.assertEqual(paths, sorted(set(paths)))
        present = {
            path.relative_to(_RELEASE_BUNDLE).as_posix()
            for path in _RELEASE_BUNDLE.rglob("*")
            if path.is_file()
            and path != _RELEASE_MANIFEST
            and "__pycache__" not in path.relative_to(_RELEASE_BUNDLE).parts
        }
        self.assertEqual(present, set(paths))
        for record in records:
            target = _RELEASE_BUNDLE / record["path"]
            self.assertFalse(target.is_symlink(), record["path"])
            self.assertEqual(target.stat().st_size, record["bytes"], record["path"])
            self.assertEqual(_sha256(target), record["sha256"], record["path"])

    def test_projected_tests_never_bind_a_fixed_two_parent_repository_root(self) -> None:
        fixed_depth = ".parents[" + "2]"
        offenders = []
        for path in sorted(_TEST_ROOT.glob("test_*.py")):
            if fixed_depth in path.read_text(encoding="utf-8"):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
