"""Focused probes for the exact-path OKF/profile observation (M004-S01/S02/S03).

These tests exercise ``frutlups.okf_profile`` as the one public read-only
observation over one explicitly named Markdown path. They cover the exact byte
and frame boundary, the total mapping from every reachable boundary refusal
category to its pinned oracle row, all ten pinned oracle rows with exact reason
codes, deterministic repetition, filesystem purity, the frozen version-1 result
contract, the ten legal layer pairings, causal independence of the two observed
layers, and the exact five-name public surface.

Hostile and limit inputs are generated in memory; no fixture is written here.
The pinned 24-fixture corpus parity lives in
``test_okf_profile_fixture_parity.py``; authority separation lives in
``test_okf_profile_authority_separation.py``.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from frutlups import okf_profile
from frutlups._yaml import (
    DEFAULT_YAML_LIMITS,
    YamlBoundaryError,
    YamlFailure,
    load_yaml_bytes,
)
from frutlups.okf_profile import (
    OKF_PROFILE_OBSERVATION_CONTRACT_ID,
    OKF_PROFILE_OBSERVATION_CONTRACT_VERSION,
    OKFProfileObservation,
    ProfileLayerResult,
    observe_okf_profile_path,
)

_MODULE_PATH = Path(okf_profile.__file__).resolve()

# The ten pinned oracle rows (consumer contract 09 section 4) as
# (okf_result, okf_reason, profile_result, profile_reason) tuples.
ROW_LEGACY = ("not_evaluated", None, "not_applicable", None)
ROW_NO_PROFILE_FIELD = ("pass", None, "not_applicable", None)
ROW_PROFILE_PASS = ("pass", None, "pass", None)
ROW_OUT_OF_SUBSET = ("pass", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET")
ROW_YAML_INVALID = ("fail", "OKF_YAML_INVALID", "fail", "PROFILE_YAML_OUT_OF_SUBSET")
ROW_LIMIT = ("unverified", "OKF_PARSE_LIMIT_EXCEEDED", "fail", "PROFILE_YAML_OUT_OF_SUBSET")
ROW_UNTERMINATED = ("fail", "OKF_FRONTMATTER_MISSING", "not_applicable", None)
ROW_TYPE_MISSING = ("fail", "OKF_TYPE_MISSING", "not_applicable", None)
ROW_TYPE_UNSUPPORTED = ("pass", None, "fail", "PROFILE_TYPE_UNSUPPORTED")
ROW_VERSION_UNSUPPORTED = ("pass", None, "fail", "PROFILE_VERSION_UNSUPPORTED")

ALL_ROWS = frozenset(
    {
        ROW_LEGACY,
        ROW_NO_PROFILE_FIELD,
        ROW_PROFILE_PASS,
        ROW_OUT_OF_SUBSET,
        ROW_YAML_INVALID,
        ROW_LIMIT,
        ROW_UNTERMINATED,
        ROW_TYPE_MISSING,
        ROW_TYPE_UNSUPPORTED,
        ROW_VERSION_UNSUPPORTED,
    }
)

_TOTAL_LIMIT = 1_048_576


def _frame(*yaml_lines: str) -> bytes:
    return ("---\n" + "".join(line + "\n" for line in yaml_lines) + "---\n").encode("utf-8")


def _layers(observation: OKFProfileObservation) -> tuple[str, str | None, str, str | None]:
    return (
        observation.okf_concept.result,
        observation.okf_concept.reason,
        observation.framework_profile.result,
        observation.framework_profile.reason,
    )


class _ObservationCase(unittest.TestCase):
    """Shared temp-file plumbing and the standard whole-result assertion."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def write(self, content: bytes, name: str = "artifact.md") -> Path:
        path = self.tmp / name
        path.write_bytes(content)
        return path

    def observe(self, content: bytes) -> OKFProfileObservation:
        return observe_okf_profile_path(self.write(content))

    def assert_row(
        self,
        content: bytes,
        row: tuple[str, str | None, str, str | None],
        diagnostics: tuple[str, ...] = (),
    ) -> OKFProfileObservation:
        observation = self.observe(content)
        self.assertEqual(_layers(observation), row)
        self.assertEqual(observation.execution_eligibility, "not_evaluated")
        self.assertEqual(observation.diagnostics, diagnostics)
        self.assertEqual(observation.contract_id, OKF_PROFILE_OBSERVATION_CONTRACT_ID)
        self.assertEqual(
            observation.contract_version, OKF_PROFILE_OBSERVATION_CONTRACT_VERSION
        )
        return observation


# ---------------------------------------------------------------------------
# Exact frame boundary (S01)
# ---------------------------------------------------------------------------


class FramingTests(_ObservationCase):
    def test_exact_delimiters_frame_and_evaluate(self) -> None:
        self.assert_row(
            b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\nbody\n',
            ROW_PROFILE_PASS,
        )

    def test_crlf_framing_is_accepted_without_changing_bytes(self) -> None:
        self.assert_row(
            b'---\r\ntype: analysis\r\nframework_profile: "0.1-rc.1"\r\n---\r\nbody\r\n',
            ROW_PROFILE_PASS,
        )

    def test_missing_final_newline_still_frames(self) -> None:
        self.assert_row(b"---\ntype: analysis\n---", ROW_NO_PROFILE_FIELD)

    def test_empty_input_is_legacy(self) -> None:
        self.assert_row(b"", ROW_LEGACY)

    def test_plain_markdown_is_legacy(self) -> None:
        self.assert_row(b"# Title\n\nNo frontmatter here.\n", ROW_LEGACY)

    def test_non_first_line_opener_is_legacy(self) -> None:
        self.assert_row(b"\n---\ntype: analysis\n---\n", ROW_LEGACY)

    def test_indented_opener_is_legacy(self) -> None:
        self.assert_row(b" ---\ntype: analysis\n---\n", ROW_LEGACY)

    def test_tab_indented_opener_is_legacy(self) -> None:
        self.assert_row(b"\t---\ntype: analysis\n---\n", ROW_LEGACY)

    def test_padded_opener_is_legacy(self) -> None:
        self.assert_row(b"--- \ntype: analysis\n---\n", ROW_LEGACY)

    def test_bom_prefixed_opener_is_legacy(self) -> None:
        self.assert_row(b"\xef\xbb\xbf---\ntype: analysis\n---\n", ROW_LEGACY)

    def test_four_dash_opener_is_legacy(self) -> None:
        self.assert_row(b"----\ntype: analysis\n---\n", ROW_LEGACY)

    def test_opener_without_closer_is_unterminated(self) -> None:
        self.assert_row(b"---\ntype: analysis\n", ROW_UNTERMINATED)

    def test_padded_closer_is_content_so_frame_is_unterminated(self) -> None:
        self.assert_row(b"---\ntype: analysis\n--- \nrest\n", ROW_UNTERMINATED)

    def test_indented_closer_is_content_so_frame_is_unterminated(self) -> None:
        self.assert_row(b"---\ntype: analysis\n ---\nrest\n", ROW_UNTERMINATED)

    def test_opener_alone_is_unterminated(self) -> None:
        self.assert_row(b"---\n", ROW_UNTERMINATED)

    def test_empty_frame_is_type_missing(self) -> None:
        self.assert_row(b"---\n---\n", ROW_TYPE_MISSING)

    def test_body_after_closer_is_inert_for_the_observation(self) -> None:
        hostile_body = b"---\nx: [1, 2\n\ttabs: everywhere\n" + b"z" * 4_096 + b"\n"
        self.assert_row(
            b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n' + hostile_body,
            ROW_PROFILE_PASS,
        )

    def test_unicode_nel_line_break_frames_like_the_pinned_checker(self) -> None:
        # str.splitlines treats U+0085 as a line boundary, exactly as the
        # pinned reference checker's framing does; this parity is deliberate.
        self.assert_row("---\u0085---\u0085".encode("utf-8"), ROW_TYPE_MISSING)


# ---------------------------------------------------------------------------
# Read boundary: one open, one bounded read, exact failure diagnostics (S01)
# ---------------------------------------------------------------------------


class ReadBoundaryTests(_ObservationCase):
    def test_missing_path_returns_neutral_layers_and_exact_diagnostic(self) -> None:
        observation = observe_okf_profile_path(self.tmp / "missing.md")
        self.assertEqual(_layers(observation), ROW_LEGACY)
        self.assertEqual(observation.diagnostics, ("artifact read failed",))
        self.assertEqual(observation.execution_eligibility, "not_evaluated")

    def test_directory_path_returns_read_failed(self) -> None:
        observation = observe_okf_profile_path(self.tmp)
        self.assertEqual(_layers(observation), ROW_LEGACY)
        self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_embedded_nul_path_returns_read_failed(self) -> None:
        observation = observe_okf_profile_path(str(self.tmp / "a\x00b.md"))
        self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_non_regular_file_returns_read_failed(self) -> None:
        # A character device is not an artifact; refusing before the read keeps
        # the operation bounded (the Windows console device blocks reads).
        import os

        observation = observe_okf_profile_path(os.devnull)
        self.assertEqual(_layers(observation), ROW_LEGACY)
        self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_pre_stat_refuses_non_regular_before_any_open(self) -> None:
        # A path whose pre-open stat reports a non-regular file must be refused
        # without any open at all -- the open of a FIFO or device is exactly
        # the operation that can block.
        import os

        fifo_stat = os.stat_result((0o010644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        self.assertFalse(__import__("stat").S_ISREG(fifo_stat.st_mode))
        with mock.patch.object(os, "stat", return_value=fifo_stat), mock.patch.object(
            os, "open", side_effect=AssertionError("open reached for a non-regular path")
        ):
            observation = observe_okf_profile_path(str(self.tmp / "fifo.md"))
        self.assertEqual(_layers(observation), ROW_LEGACY)
        self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_post_open_downgrade_is_refused_and_the_descriptor_closed(self) -> None:
        # Pre-open stat says regular, but the descriptor no longer is (the
        # replacement race): the opened descriptor must be verified, closed
        # exactly once, and never read.
        import os

        path = self.write(b"---\ntype: analysis\n---\n")
        fifo_stat = os.stat_result((0o010644, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        closes: list[int] = []
        real_close = os.close

        def recording_close(descriptor):  # type: ignore[no-untyped-def]
            closes.append(descriptor)
            real_close(descriptor)

        with mock.patch.object(os, "fstat", return_value=fifo_stat), mock.patch.object(
            os, "close", recording_close
        ), mock.patch.object(
            os, "fdopen", side_effect=AssertionError("read wrapper reached")
        ):
            observation = observe_okf_profile_path(path)
        self.assertEqual(observation.diagnostics, ("artifact read failed",))
        self.assertEqual(len(closes), 1)

    def test_open_flags_are_exactly_the_platform_branch(self) -> None:
        import os
        import sys

        flags = okf_profile._OPEN_FLAGS
        if sys.platform == "win32":
            self.assertEqual(flags, os.O_RDONLY | os.O_BINARY)
            self.assertFalse(hasattr(os, "O_NONBLOCK"))
        else:
            self.assertEqual(flags, os.O_RDONLY | os.O_NONBLOCK)
            self.assertFalse(hasattr(os, "O_BINARY"))

    def test_special_shaped_paths_are_refused_via_the_deterministic_branch(self) -> None:
        # On platforms without the real special file the shape is exercised
        # through the same deterministic pre-stat branch the real one takes:
        # FIFO, socket, and character device modes all refuse before any open.
        import os

        special_stat = __import__("stat")
        for label, mode in (
            ("fifo", 0o010000),
            ("socket", 0o140000),
            ("character device", 0o020000),
        ):
            with self.subTest(shape=label):
                shaped = os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))
                self.assertFalse(special_stat.S_ISREG(shaped.st_mode))
                with mock.patch.object(os, "stat", return_value=shaped), mock.patch.object(
                    os, "open", side_effect=AssertionError("blocking open reached")
                ):
                    observation = observe_okf_profile_path(str(self.tmp / "pipe.md"))
                self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_subprocess_falsifier_open_before_classification_blocks(self) -> None:
        # The Phase C falsifier, durable: a subprocess performing
        # open-before-classification against a blocking special path must time
        # out, while a subprocess running the final code returns promptly.
        # Where the platform provides mkfifo the probe uses a real FIFO with no
        # writer; elsewhere the same subprocess mechanics run a deterministic
        # blocking-open stand-in. No skip on either branch.
        import os
        import subprocess
        import sys

        package_root = str(Path(okf_profile.__file__).resolve().parents[1])
        env = dict(os.environ)
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")

        if hasattr(os, "mkfifo"):
            special = self.tmp / "pipe.md"
            os.mkfifo(special)
            final_snippet = (
                "from frutlups.okf_profile import observe_okf_profile_path\n"
                f"o = observe_okf_profile_path({str(special)!r})\n"
                "print('|'.join(o.diagnostics))\n"
            )
            mutant_snippet = (
                # Open-before-classification: the pre-correction sequence.
                f"handle = open({str(special)!r}, 'rb')\n"
                "print('unreachable: open returned')\n"
            )
        else:
            regular = self.write(b"---\ntype: analysis\n---\n", name="blockable.md")
            blocker = (
                "import builtins, time\n"
                "real_open = builtins.open\n"
                "def blocking_open(file, *a, **k):\n"
                f"    if str(file).endswith('blockable.md'):\n"
                "        time.sleep(600)\n"
                "    return real_open(file, *a, **k)\n"
                "builtins.open = blocking_open\n"
                "import os\n"
                "real_os_open = os.open\n"
                "def blocking_os_open(file, flags, *a, **k):\n"
                "    if str(file).endswith('blockable.md'):\n"
                "        time.sleep(600)\n"
                "    return real_os_open(file, flags, *a, **k)\n"
            )
            final_snippet = (
                blocker
                + "from frutlups.okf_profile import observe_okf_profile_path\n"
                # The final code classifies via the pre-open stat; for a
                # regular file it proceeds through os.open, which is not the
                # patched blocking builtin, and returns promptly.
                + f"o = observe_okf_profile_path({str(regular)!r})\n"
                + "print('|'.join(o.diagnostics) or 'ok')\n"
            )
            mutant_snippet = (
                blocker
                # Open-before-classification with the blocking primitive.
                + f"handle = blocking_open({str(regular)!r}, 'rb')\n"
                + "print('unreachable: open returned')\n"
            )

        final = subprocess.run(
            [sys.executable, "-c", final_snippet],
            capture_output=True, text=True, timeout=60, env=env,
        )
        self.assertEqual(final.returncode, 0, final.stderr)
        with self.assertRaises(subprocess.TimeoutExpired):
            subprocess.run(
                [sys.executable, "-c", mutant_snippet],
                capture_output=True, text=True, timeout=5, env=env,
            )

    def test_link_to_regular_file_observes_the_target(self) -> None:
        # The exact-path contract observes the regular target of an explicitly
        # supplied symlink: classification must FOLLOW the link (a
        # symlink-following stat), never classify the link object itself.
        import os

        target = self.write(
            b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n', name="target.md"
        )
        direct = observe_okf_profile_path(target)
        self.assertEqual(_layers(direct), ROW_PROFILE_PASS)

        link = self.tmp / "link.md"
        try:
            os.symlink(target, link)
        except OSError:
            link = None  # symlink creation needs elevation on stock Windows
        if link is not None:
            self.assertTrue(os.path.islink(link))
            self.assertEqual(observe_okf_profile_path(link), direct)

        # Deterministic follow-semantics branch, independent of symlink
        # privilege: os.lstat is patched to describe the supplied path as a
        # symlink object while the real symlink-following os.stat still
        # reports the regular target. Classifying via anything but the
        # following stat would refuse the artifact and fail here.
        link_stat = os.stat_result((0o120777, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        self.assertTrue(__import__("stat").S_ISLNK(link_stat.st_mode))
        with mock.patch.object(os, "lstat", return_value=link_stat):
            followed = observe_okf_profile_path(target)
        self.assertEqual(followed, direct)

    def test_windows_console_device_name_returns_promptly(self) -> None:
        # On Windows "CON" opens as the console and a read would block forever;
        # on other platforms it is an ordinary missing file. Either way the
        # observation must return the read-failed diagnostic without blocking.
        import threading

        results: list[tuple[str, ...]] = []

        def call() -> None:
            results.append(observe_okf_profile_path("CON").diagnostics)

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "observation blocked on a device path")
        self.assertEqual(results, [("artifact read failed",)])

    def test_non_path_input_returns_read_failed(self) -> None:
        for bad in (3, b"path.md", None, ["a.md"]):
            with self.subTest(input=bad):
                observation = observe_okf_profile_path(bad)  # type: ignore[arg-type]
                self.assertEqual(_layers(observation), ROW_LEGACY)
                self.assertEqual(observation.diagnostics, ("artifact read failed",))

    def test_string_and_path_inputs_are_equivalent(self) -> None:
        path = self.write(b"---\ntype: analysis\n---\n")
        self.assertEqual(
            observe_okf_profile_path(path), observe_okf_profile_path(str(path))
        )

    def test_exactly_one_descriptor_open_read_and_close(self) -> None:
        import os

        path = self.write(b"---\ntype: analysis\n---\n")
        opens: list[tuple[object, int]] = []
        reads: list[object] = []
        closes: list[int] = []
        real_os_open = os.open
        real_fdopen = os.fdopen

        def recording_os_open(file, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            descriptor = real_os_open(file, flags, *args, **kwargs)
            opens.append((file, flags))
            return descriptor

        class _RecordingHandle:
            def __init__(self, handle) -> None:  # type: ignore[no-untyped-def]
                self._handle = handle

            def read(self, size=-1):  # type: ignore[no-untyped-def]
                reads.append(size)
                return self._handle.read(size)

            def __enter__(self):  # type: ignore[no-untyped-def]
                self._handle.__enter__()
                return self

            def __exit__(self, *exc_info):  # type: ignore[no-untyped-def]
                closes.append(1)
                return self._handle.__exit__(*exc_info)

        def recording_fdopen(descriptor, *args, **kwargs):  # type: ignore[no-untyped-def]
            return _RecordingHandle(real_fdopen(descriptor, *args, **kwargs))

        with mock.patch.object(os, "open", recording_os_open), mock.patch.object(
            os, "fdopen", recording_fdopen
        ):
            observe_okf_profile_path(path)
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0][1], okf_profile._OPEN_FLAGS)
        self.assertEqual(reads, [_TOTAL_LIMIT + 1])
        self.assertEqual(closes, [1])

    def test_total_bytes_at_limit_is_observed(self) -> None:
        head = b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n'
        content = head + b"a" * (_TOTAL_LIMIT - len(head))
        self.assertEqual(len(content), _TOTAL_LIMIT)
        self.assert_row(content, ROW_PROFILE_PASS)

    def test_total_bytes_over_limit_is_a_bounded_refusal(self) -> None:
        head = b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n'
        content = head + b"a" * (_TOTAL_LIMIT + 1 - len(head))
        self.assertEqual(len(content), _TOTAL_LIMIT + 1)
        self.assert_row(content, ROW_LIMIT)

    def test_over_limit_is_refused_before_decode_and_before_yaml(self) -> None:
        # The oversize tail is invalid UTF-8: were the artifact decoded first,
        # the result would be the UTF-8 diagnostic instead of the refusal.
        content = b"---\ntype: analysis\n---\n" + b"a" * _TOTAL_LIMIT + b"\xff"
        with mock.patch.object(
            okf_profile, "load_yaml_bytes", side_effect=AssertionError("yaml reached")
        ):
            observation = observe_okf_profile_path(self.write(content))
        self.assertEqual(_layers(observation), ROW_LIMIT)

    def test_invalid_utf8_returns_neutral_layers_and_exact_diagnostic(self) -> None:
        observation = self.assert_row(
            b"---\ntype: a\xff\n---\n", ROW_LEGACY, ("artifact is not valid UTF-8",)
        )
        self.assertEqual(observation.okf_concept, ProfileLayerResult("not_evaluated", None))
        self.assertEqual(
            observation.framework_profile, ProfileLayerResult("not_applicable", None)
        )

    def test_invalid_utf8_never_reaches_yaml(self) -> None:
        path = self.write(b"---\nk: v\xc3\x28\n---\n")
        with mock.patch.object(
            okf_profile, "load_yaml_bytes", side_effect=AssertionError("yaml reached")
        ):
            observation = observe_okf_profile_path(path)
        self.assertEqual(observation.diagnostics, ("artifact is not valid UTF-8",))


# ---------------------------------------------------------------------------
# One engine, zero-or-one calls (S01)
# ---------------------------------------------------------------------------


class EngineCallTests(_ObservationCase):
    def _count_calls(self, content: bytes) -> tuple[int, list[tuple[object, bool]]]:
        calls: list[tuple[object, bool]] = []
        real = load_yaml_bytes

        def counting(data, *, limits, representation_only=False):  # type: ignore[no-untyped-def]
            calls.append((limits, representation_only))
            return real(data, limits=limits, representation_only=representation_only)

        with mock.patch.object(okf_profile, "load_yaml_bytes", counting):
            observe_okf_profile_path(self.write(content))
        return len(calls), calls

    def test_legacy_input_makes_zero_yaml_boundary_calls(self) -> None:
        count, _ = self._count_calls(b"# plain\n")
        self.assertEqual(count, 0)

    def test_unterminated_input_makes_zero_yaml_boundary_calls(self) -> None:
        count, _ = self._count_calls(b"---\ntype: analysis\n")
        self.assertEqual(count, 0)

    def test_framed_input_makes_exactly_one_representation_only_call(self) -> None:
        count, calls = self._count_calls(b"---\ntype: analysis\n---\n")
        self.assertEqual(count, 1)
        self.assertIs(calls[0][0], DEFAULT_YAML_LIMITS)
        self.assertIs(calls[0][1], True)

    def test_refused_input_still_makes_exactly_one_call(self) -> None:
        count, calls = self._count_calls(b"---\ntype: a\ntype: b\n---\n")
        self.assertEqual(count, 1)
        self.assertIs(calls[0][1], True)

    def test_a_programming_error_in_the_engine_propagates(self) -> None:
        # The adapter maps only typed boundary refusals; a defect must surface
        # rather than being absorbed into an oracle row.
        with mock.patch.object(
            okf_profile, "load_yaml_bytes", side_effect=RuntimeError("defect")
        ):
            with self.assertRaises(RuntimeError):
                observe_okf_profile_path(self.write(b"---\ntype: analysis\n---\n"))

    def test_module_imports_no_yaml_engine_and_only_the_accepted_boundary(self) -> None:
        tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertNotIn("yaml", imported)
        frutlups_imports = {name for name in imported if name.startswith("frutlups")}
        self.assertEqual(frutlups_imports, {"frutlups._yaml"})
        source = _MODULE_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "yaml.scan",
            "yaml.compose",
            "yaml.safe_load",
            "yaml.load(",
            "SafeLoader(",
            "subprocess",
        ):
            self.assertNotIn(forbidden, source)


# ---------------------------------------------------------------------------
# All ten pinned oracle rows from constructed inputs (S01)
# ---------------------------------------------------------------------------


class OracleRowTests(_ObservationCase):
    def test_all_ten_rows_reproduce_with_exact_reason_codes(self) -> None:
        cases: list[tuple[str, bytes, tuple[str, str | None, str, str | None]]] = [
            ("row 1 legacy", b"# Legacy\n", ROW_LEGACY),
            ("row 2 registry type no profile field", _frame("type: analysis"), ROW_NO_PROFILE_FIELD),
            (
                "row 3 minimal profile valid",
                _frame("type: analysis", 'framework_profile: "0.1-rc.1"'),
                ROW_PROFILE_PASS,
            ),
            (
                "row 3 enriched profile valid",
                _frame(
                    "type: framework_doc",
                    'framework_profile: "0.1-rc.1"',
                    'framework_id: "fid-1"',
                    'title: "T"',
                    "tags:",
                    "  - alpha",
                    'unknown_extension: "tolerated"',
                    "llloom:",
                    '  claim_id: "c-1"',
                ),
                ROW_PROFILE_PASS,
            ),
            (
                "row 4 flow collection",
                _frame("type: analysis", "tags: [a, b]"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "row 5 duplicate key",
                _frame("type: analysis", "k: 1", "k: 2"),
                ROW_YAML_INVALID,
            ),
            (
                "row 6 alias cycle",
                _frame("type: analysis", "a: &x", "  b: *x"),
                ROW_LIMIT,
            ),
            ("row 7 unterminated", b"---\ntype: analysis\n", ROW_UNTERMINATED),
            ("row 8 type missing", _frame('title: "No type"'), ROW_TYPE_MISSING),
            ("row 8 type empty", _frame("type:"), ROW_TYPE_MISSING),
            ("row 9 unknown type", _frame("type: made_up"), ROW_TYPE_UNSUPPORTED),
            (
                "row 10 unknown version",
                _frame("type: analysis", 'framework_profile: "9.9"'),
                ROW_VERSION_UNSUPPORTED,
            ),
            (
                "row 10 newer rc",
                _frame("type: analysis", 'framework_profile: "0.1-rc.2"'),
                ROW_VERSION_UNSUPPORTED,
            ),
        ]
        seen: set[tuple[str, str | None, str, str | None]] = set()
        for label, content, row in cases:
            with self.subTest(case=label):
                self.assert_row(content, row)
                seen.add(row)
        self.assertEqual(seen, ALL_ROWS)

    def test_producer_subset_feature_matrix(self) -> None:
        cases = [
            ("flow mapping", "k: {a: 1}"),
            ("flow sequence", "k: [1, 2]"),
            ("anchor and alias", "a: &x v\nb: *x"),
            ("merge key", "d: &d\n  a: 1\ne:\n  <<: *d"),
            ("safe explicit tag", "k: !!str v"),
            ("single quoted string", "k: 'v'"),
            ("literal block scalar", "k: |\n  text"),
            ("folded block scalar", "k: >\n  text"),
            ("unquoted yaml11 bool word", "k: no"),
            ("capitalized bool", "k: True"),
            ("capitalized null", "k: Null"),
            ("bare leading zero", "k: 007"),
            ("negative zero", "k: -0"),
            ("underscore grouping", "k: 1_000"),
            ("sexagesimal-looking string", "k: 1:20"),
            ("exponent-looking string", "k: 1e3"),
            ("float value", "k: 1.5"),
            ("native timestamp", "k: 2026-05-28T14:30:00Z"),
            ("native date", "k: 2026-05-28"),
            ("non-string integer key", "5: v"),
            ("non-string boolean key", "true: v"),
            ("numeric-looking sequence item", "k:\n  - 007"),
        ]
        for label, body in cases:
            with self.subTest(case=label):
                self.assert_row(
                    _frame("type: analysis", *body.split("\n")), ROW_OUT_OF_SUBSET
                )

    def test_in_subset_forms_stay_conformant(self) -> None:
        cases = [
            ("comments", "k: v  # trailing"),
            ("double quoted numeric string", 'k: "007"'),
            ("double quoted timestamp string", 'k: "2026-05-28T14:30:00Z"'),
            ("canonical integer", "k: 42"),
            ("canonical zero", "k: 0"),
            ("canonical booleans", "k: true\nk2: false"),
            ("canonical null", "k: null"),
            ("block sequence of scalars", "tags:\n  - a\n  - b"),
            ("tool namespace mapping", "llloom:\n  claim_id: \"c-1\""),
            ("unicode escape", 'k: "\\u0041"'),
        ]
        for label, body in cases:
            with self.subTest(case=label):
                self.assert_row(
                    _frame(
                        "type: analysis",
                        'framework_profile: "0.1-rc.1"',
                        *body.split("\n"),
                    ),
                    ROW_PROFILE_PASS,
                )

    def test_type_field_semantics_are_node_level_and_lexeme_based(self) -> None:
        cases = [
            ("non-scalar type mapping", _frame("type: {a: 1}"), ROW_TYPE_MISSING),
            ("non-scalar type sequence", _frame("type:\n  - a"), ROW_TYPE_MISSING),
            ("root is a sequence", _frame("- a"), ROW_TYPE_MISSING),
            ("root is a scalar", _frame("just-a-scalar"), ROW_TYPE_MISSING),
            ("null type lexeme is a present unknown type", _frame("type: null"), ROW_TYPE_UNSUPPORTED),
            ("zero type lexeme is a present unknown type", _frame("type: 0"), ROW_TYPE_UNSUPPORTED),
            (
                "merge-injected type is not a top-level field",
                _frame("base: &b", "  type: analysis", "<<: *b"),
                ROW_TYPE_MISSING,
            ),
            (
                "alias-valued type is present but out of subset",
                _frame("x: &a analysis", "type: *a"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "alias to explicit null as type is a present non-empty lexeme",
                _frame("x: &a null", "type: *a"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "alias to tilde as type is a present non-empty lexeme",
                _frame("x: &a ~", "type: *a"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "aliased key naming type is a present key",
                _frame("x: &a type", "*a : analysis"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "tagged type value keeps its non-empty lexeme",
                _frame("type: !frobnicate analysis"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "failed int construction on type keeps its lexeme",
                _frame("type: !!int abc"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "quoted type key still counts",
                _frame('"type": analysis', 'framework_profile: "0.1-rc.1"'),
                ROW_PROFILE_PASS,
            ),
        ]
        for label, content, row in cases:
            with self.subTest(case=label):
                self.assert_row(content, row)

    def test_alias_to_empty_type_is_type_missing_before_subset(self) -> None:
        # An alias to an explicit empty lexeme yields the empty type value, and
        # the type failure precedes producer-subset policy exactly as it does
        # for a literal empty type -- matching the pinned checker.
        observation = self.observe(_frame('x: &a ""', "type: *a"))
        # The pinned checker reports the empty lexeme through the shared node,
        # so the type is present-but-empty: OKF fail, profile not_applicable.
        self.assertEqual(_layers(observation), ROW_TYPE_MISSING)

    def test_precedence_between_layers_and_rows(self) -> None:
        cases = [
            (
                "missing type beats out-of-subset",
                _frame("tags: [a, b]"),
                ROW_TYPE_MISSING,
            ),
            (
                "invalid yaml beats everything framed",
                _frame("type: analysis", 'x: "bad" junk"'),
                ROW_YAML_INVALID,
            ),
            (
                "resource refusal beats syntax evaluation",
                b"---\n" + b"[" * 3_000 + b"]" * 3_000 + b"\n---\n",
                ROW_LIMIT,
            ),
            (
                "subset failure beats unknown type",
                _frame("type: made_up", "tags: [a]"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "subset failure beats unknown version",
                _frame("type: analysis", "framework_profile: [1]"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "type support beats version support",
                _frame("type: made_up", 'framework_profile: "9.9"'),
                ROW_TYPE_UNSUPPORTED,
            ),
            (
                "registry type with missing version field is not opted in",
                _frame("type: analysis", 'title: "T"'),
                ROW_NO_PROFILE_FIELD,
            ),
            (
                "block-mapping version value is present but not a scalar, so not opted in",
                _frame("type: analysis", "framework_profile:", "  v: 1"),
                ROW_NO_PROFILE_FIELD,
            ),
            (
                "empty version value is version-unsupported, not unprofiled",
                _frame("type: analysis", "framework_profile:"),
                ROW_VERSION_UNSUPPORTED,
            ),
            (
                "unquoted version lexeme resolving as a plain string still matches",
                _frame("type: analysis", "framework_profile: 0.1-rc.1"),
                ROW_PROFILE_PASS,
            ),
            (
                "float-resolved version spelling is out of subset",
                _frame("type: analysis", "framework_profile: 0.1"),
                ROW_OUT_OF_SUBSET,
            ),
            (
                "non-scalar version mapping is out of subset by flow spelling",
                _frame("type: analysis", "framework_profile: {v: 1}"),
                ROW_OUT_OF_SUBSET,
            ),
        ]
        for label, content, row in cases:
            with self.subTest(case=label):
                self.assert_row(content, row)


# ---------------------------------------------------------------------------
# Total boundary-category mapping (S01)
# ---------------------------------------------------------------------------


class BoundaryCategoryMappingTests(_ObservationCase):
    """Every reachable boundary refusal category maps to its pinned row.

    Each case asserts the observation row and, white-box, reproduces the
    boundary category for the identical framed bytes through the accepted
    engine, so the mapping is attributed rather than assumed.
    """

    def _assert_category(self, frontmatter: str, category: YamlFailure) -> None:
        with self.assertRaises(YamlBoundaryError) as caught:
            load_yaml_bytes(frontmatter.encode("utf-8"), limits=DEFAULT_YAML_LIMITS)
        self.assertEqual(caught.exception.category, category)

    def test_resource_categories_map_to_the_bounded_refusal_row(self) -> None:
        # Frontmatter over 65,536 bytes with every line inside the line limit.
        oversize_frontmatter = "\n".join(
            f'k{index}: "' + "a" * 7_000 + '"' for index in range(10)
        )
        too_many_lines = "type: analysis\n" + "# pad\n" * 500
        long_line = 'k: "' + "a" * 8_192 + '"'
        # A plain scalar folded over three lines: one node over the scalar cap.
        long_scalar = "k: " + "a" * 6_000 + "\n  " + "b" * 6_000 + "\n  " + "c" * 6_000
        wide_flow_mapping = "k: {" + ", ".join(f"a{index}: 1" for index in range(501)) + "}"
        wide_flow_sequence = "k: [" + ", ".join(["a"] * 1_001) + "]"
        # Flow sequences on wrapped lines: over the node cap without touching
        # the per-sequence, token, or line dimensions.
        many_nodes = (
            "s1: [" + ", ".join(["a"] * 999) + "]\ns2: [" + ", ".join(["a"] * 999) + "]"
        )
        cases = [
            ("frontmatter bytes", oversize_frontmatter, YamlFailure.INPUT_TOO_LARGE),
            ("line count", too_many_lines.rstrip("\n"), YamlFailure.TOO_MANY_LINES),
            ("line length", long_line, YamlFailure.LINE_TOO_LONG),
            (
                "alias count",
                "a: &x v\nk: [" + ", ".join(["*x"] * 51) + "]",
                YamlFailure.TOO_MANY_ALIASES,
            ),
            (
                "nesting depth",
                "\n".join("  " * i + f"m{i}:" for i in range(33)) + "\n" + "  " * 33 + "leaf: v",
                YamlFailure.TOO_DEEP,
            ),
            ("node count", many_nodes, YamlFailure.TOO_MANY_NODES),
            ("scalar length", long_scalar, YamlFailure.SCALAR_TOO_LONG),
            ("mapping size", wide_flow_mapping, YamlFailure.MAPPING_TOO_LARGE),
            ("sequence size", wide_flow_sequence, YamlFailure.SEQUENCE_TOO_LARGE),
            (
                "alias cycle",
                "type: analysis\na: &x\n  b: *x",
                YamlFailure.ALIAS_CYCLE,
            ),
        ]
        for label, frontmatter, category in cases:
            with self.subTest(case=label):
                self._assert_category(frontmatter, category)
                self.assert_row(
                    ("---\n" + frontmatter + "\n---\n").encode("utf-8"), ROW_LIMIT
                )

    def test_category_mapping_is_total_over_the_boundary_vocabulary(self) -> None:
        # Every boundary category is either a bounded resource refusal (row 6),
        # a conclusive load failure (row 5), or one of the three categories this
        # module makes unreachable by owning the read and the strict decode.
        resource = okf_profile._RESOURCE_REFUSALS
        conclusive = {
            YamlFailure.INVALID_YAML,
            YamlFailure.MULTIPLE_DOCUMENTS,
            YamlFailure.DUPLICATE_KEY,
        }
        unreachable = {
            YamlFailure.READ_FAILED,
            YamlFailure.INPUT_NOT_BYTES,
            YamlFailure.INVALID_UTF8,
            # Never raised in representation-only mode: every tag is
            # representation evidence there.
            YamlFailure.UNSUPPORTED_TAG,
        }
        self.assertEqual(resource | conclusive | unreachable, set(YamlFailure))
        self.assertFalse(resource & conclusive)
        self.assertFalse(resource & unreachable)
        # The token ceiling maps to row 6 by this set; its at-limit and
        # maximum-plus-one evidence through the public observation lives in
        # test_okf_profile_fixture_parity.py.
        self.assertIn(YamlFailure.TOO_MANY_TOKENS, resource)

    def test_conclusive_categories_map_to_the_invalid_row(self) -> None:
        cases = [
            ("malformed quoting", 'type: analysis\nx: "bad" junk"', YamlFailure.INVALID_YAML),
            ("tab indentation", "type: analysis\nx:\n\ty: 1", YamlFailure.INVALID_YAML),
            ("undefined alias", "type: analysis\nk: *missing", YamlFailure.INVALID_YAML),
            (
                # After `...` a second document needs an explicit `---` start;
                # without one the stream is a conclusive parse error.
                "document end then bare content",
                "type: analysis\n...\ntype: other",
                YamlFailure.INVALID_YAML,
            ),
            (
                # A `---` line with inline content starts a second document
                # without closing the Markdown frame (the frame closer must be
                # exactly `---`).
                "multiple documents",
                "type: analysis\n...\n--- {second: 1}",
                YamlFailure.MULTIPLE_DOCUMENTS,
            ),
            ("literal duplicate key", "type: a\ntype: b", YamlFailure.DUPLICATE_KEY),
            ("semantic duplicate key", "type: analysis\n1: x\n01: y", YamlFailure.DUPLICATE_KEY),
            (
                "boolean synonym duplicate key",
                "type: analysis\nyes: x\non: y",
                YamlFailure.DUPLICATE_KEY,
            ),
            (
                "duplicate merge keys",
                "type: analysis\nd: &d\n  a: 1\ne:\n  <<: *d\n  <<: *d",
                YamlFailure.DUPLICATE_KEY,
            ),
        ]
        for label, frontmatter, category in cases:
            with self.subTest(case=label):
                self._assert_category(frontmatter, category)
                self.assert_row(
                    ("---\n" + frontmatter + "\n---\n").encode("utf-8"), ROW_YAML_INVALID
                )

    def test_tags_and_complex_keys_are_representation_evidence_not_failures(self) -> None:
        # The pinned checker never constructs values, so unknown, forbidden, or
        # constructor-incompatible tags and complex keys are valid YAML that is
        # merely out of the producer subset. Representation-only mode reproduces
        # that exactly; the default constructed mode still refuses unknown tags
        # for every ordinary boundary consumer.
        cases = [
            ("python object tag", "type: analysis\nk: !!python/object/apply:os.system ['x']"),
            ("unknown local tag", "type: analysis\nk: !frobnicate 1"),
            ("unknown global tag", "type: analysis\nk: !<tag:example.com,2026:thing> 1"),
            ("failed int construction", "type: analysis\nk: !!int abc"),
            ("failed bool construction", "type: analysis\nk: !!bool abc"),
            ("failed timestamp construction", "type: analysis\nk: !!timestamp abc"),
            ("out-of-range implicit date", "type: analysis\nk: 2026-13-40"),
            ("complex sequence key", "type: analysis\n? [a, b]\n: v"),
            ("complex mapping key", "type: analysis\n? {a: 1}\n: v"),
        ]
        for label, frontmatter in cases:
            with self.subTest(case=label):
                self.assert_row(
                    ("---\n" + frontmatter + "\n---\n").encode("utf-8"), ROW_OUT_OF_SUBSET
                )
        with self.assertRaises(YamlBoundaryError) as caught:
            load_yaml_bytes(b"k: !frobnicate 1", limits=DEFAULT_YAML_LIMITS)
        self.assertEqual(caught.exception.category, YamlFailure.UNSUPPORTED_TAG)


# ---------------------------------------------------------------------------
# Determinism, purity, and non-echo (S01)
# ---------------------------------------------------------------------------


class DeterminismAndPurityTests(_ObservationCase):
    def test_repeated_calls_return_equal_values(self) -> None:
        for label, content in [
            ("profile valid", b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n'),
            ("hostile refusal", b"---\ntype: a\ntype: b\n---\n"),
            ("invalid utf8", b"---\nk: \xff\n---\n"),
        ]:
            with self.subTest(case=label):
                path = self.write(content, name=f"{label.replace(' ', '_')}.md")
                first = observe_okf_profile_path(path)
                second = observe_okf_profile_path(path)
                self.assertEqual(first, second)

    def test_observation_never_mutates_bytes_mtime_or_directory(self) -> None:
        hostile = b"---\ntype: a\ntype: b\nk: !!python/object:x {}\n---\n" + b"\xff"
        path = self.write(hostile, name="hostile.md")
        before_bytes = path.read_bytes()
        before_stat = path.stat()
        before_entries = sorted(entry.name for entry in self.tmp.iterdir())
        observe_okf_profile_path(path)
        self.assertEqual(path.read_bytes(), before_bytes)
        after_stat = path.stat()
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(after_stat.st_size, before_stat.st_size)
        self.assertEqual(
            sorted(entry.name for entry in self.tmp.iterdir()), before_entries
        )

    def test_no_hostile_content_path_or_traceback_is_echoed(self) -> None:
        marker = "HOSTILE_MARKER_9f2c"
        content = ("---\ntype: a\ntype: b\nsecret: " + marker + "\n---\n").encode("utf-8")
        path = self.write(content, name=marker + ".md")
        observation = observe_okf_profile_path(path)
        rendered = repr(observation)
        self.assertNotIn(marker, rendered)
        self.assertNotIn(str(self.tmp), rendered)
        self.assertNotIn("Traceback", rendered)
        for diagnostic in observation.diagnostics:
            self.assertLessEqual(len(diagnostic), 240)

    def test_diagnostics_vocabulary_is_fixed_and_bounded(self) -> None:
        vocabulary = {"artifact read failed", "artifact is not valid UTF-8"}
        produced = [
            observe_okf_profile_path(self.tmp / "missing.md"),
            self.observe(b"---\nk: \xff\n---\n"),
            self.observe(b"---\ntype: analysis\n---\n"),
            self.observe(b"---\ntype: a\ntype: b\n---\n"),
        ]
        for observation in produced:
            for diagnostic in observation.diagnostics:
                self.assertIn(diagnostic, vocabulary)
                self.assertLessEqual(len(diagnostic), 240)
        self.assertEqual(produced[2].diagnostics, ())
        self.assertEqual(produced[3].diagnostics, ())


# ---------------------------------------------------------------------------
# Frozen version-1 result contract (S02)
# ---------------------------------------------------------------------------


class ContractShapeTests(_ObservationCase):
    def test_contract_constants_are_fixed(self) -> None:
        self.assertEqual(
            OKF_PROFILE_OBSERVATION_CONTRACT_ID, "frutlups.okf_profile_observation"
        )
        self.assertEqual(OKF_PROFILE_OBSERVATION_CONTRACT_VERSION, 1)

    def test_observation_dataclass_field_order_and_types(self) -> None:
        fields = [field.name for field in dataclasses.fields(OKFProfileObservation)]
        self.assertEqual(
            fields,
            [
                "contract_id",
                "contract_version",
                "okf_concept",
                "framework_profile",
                "execution_eligibility",
                "diagnostics",
            ],
        )
        layer_fields = [field.name for field in dataclasses.fields(ProfileLayerResult)]
        self.assertEqual(layer_fields, ["result", "reason"])

    def test_result_values_are_frozen(self) -> None:
        observation = self.observe(b"---\ntype: analysis\n---\n")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            observation.execution_eligibility = "granted"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            observation.okf_concept.result = "fail"  # type: ignore[misc]

    def test_no_combined_truth_value_or_custom_truthiness_exists(self) -> None:
        for cls in (OKFProfileObservation, ProfileLayerResult):
            self.assertIsNone(cls.__dict__.get("__bool__"))
            self.assertIsNone(cls.__dict__.get("__len__"))
        observation = self.observe(b"---\ntype: analysis\n---\n")
        public_attributes = {name for name in dir(observation) if not name.startswith("_")}
        forbidden = {
            "valid",
            "success",
            "eligible",
            "accepted",
            "safe",
            "can_run",
            "ok",
            "is_valid",
        }
        self.assertFalse(public_attributes & forbidden)

    def test_execution_eligibility_is_always_the_literal_not_evaluated(self) -> None:
        contents = [
            b"# legacy\n",
            b'---\ntype: analysis\nframework_profile: "0.1-rc.1"\n---\n',
            b"---\ntype: a\ntype: b\n---\n",
            b"---\nk: \xff\n---\n",
        ]
        for content in contents:
            self.assertEqual(self.observe(content).execution_eligibility, "not_evaluated")
        self.assertEqual(
            observe_okf_profile_path(self.tmp / "missing.md").execution_eligibility,
            "not_evaluated",
        )


class LegalPairingTests(_ObservationCase):
    def test_produced_outcomes_are_exactly_the_pinned_pairings(self) -> None:
        vocab_okf = {"pass", "fail", "unverified", "not_evaluated"}
        vocab_profile = {"pass", "fail", "not_applicable"}
        contents = [
            b"# legacy\n",
            _frame("type: analysis"),
            _frame("type: analysis", 'framework_profile: "0.1-rc.1"'),
            _frame("type: analysis", "tags: [a]"),
            _frame("type: a", "type: b"),
            b"---\n" + b"[" * 3_000 + b"]" * 3_000 + b"\n---\n",
            b"---\nopen only\n",
            _frame("type:"),
            _frame("type: made_up"),
            _frame("type: analysis", 'framework_profile: "9.9"'),
        ]
        seen = set()
        for content in contents:
            observation = self.observe(content)
            outcome = _layers(observation)
            self.assertIn(outcome, ALL_ROWS)
            self.assertIn(observation.okf_concept.result, vocab_okf)
            self.assertIn(observation.framework_profile.result, vocab_profile)
            seen.add(outcome)
        self.assertEqual(seen, ALL_ROWS)

    def test_constructor_seam_refuses_every_illegal_combination(self) -> None:
        seam = okf_profile._observation
        legal = okf_profile._LEGAL_LAYER_OUTCOMES
        self.assertEqual(len(legal), 10)
        illegal = [
            ("pass", "OKF_YAML_INVALID", "pass", None),
            ("pass", None, "not_applicable", "PROFILE_TYPE_UNSUPPORTED"),
            ("fail", "OKF_TYPE_MISSING", "fail", "PROFILE_YAML_OUT_OF_SUBSET"),
            ("fail", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET"),
            ("unverified", "OKF_PARSE_LIMIT_EXCEEDED", "not_applicable", None),
            ("unverified", "OKF_YAML_UNSUPPORTED", "fail", "PROFILE_YAML_OUT_OF_SUBSET"),
            ("not_evaluated", None, "fail", "PROFILE_YAML_OUT_OF_SUBSET"),
            ("pass", None, "fail", None),
            ("pass", None, "fail", "MADE_UP_REASON"),
            ("ok", None, "pass", None),
        ]
        for combination in illegal:
            with self.subTest(combination=combination):
                with self.assertRaises(ValueError):
                    seam(*combination)

    def test_legal_diagnostics_set_is_pinned_exactly(self) -> None:
        # The complete legal diagnostics universe is three tuples. Pinning the
        # module constant itself means a future code change that extends the
        # vocabulary (and wires a new producing branch) fails here even if the
        # sampled-input tests never exercise that branch.
        self.assertEqual(
            okf_profile._LEGAL_DIAGNOSTICS,
            frozenset(
                {(), ("artifact read failed",), ("artifact is not valid UTF-8",)}
            ),
        )
        self.assertEqual(okf_profile._MAX_DIAGNOSTIC_LENGTH, 240)

    def test_constructor_seam_refuses_illegal_diagnostics(self) -> None:
        seam = okf_profile._observation
        neutral = ("not_evaluated", None, "not_applicable", None)
        with self.assertRaises(ValueError):
            seam(*neutral, diagnostics=["artifact read failed"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            seam(*neutral, diagnostics=("made up diagnostic",))
        with self.assertRaises(ValueError):
            seam(*neutral, diagnostics=("a" * 241,))
        with self.assertRaises(ValueError):
            seam("pass", None, "pass", None, diagnostics=("artifact read failed",))
        # Exactly one fixed message per pre-evaluation failure: multi-member
        # and duplicated tuples are refused even when every member is legal.
        with self.assertRaises(ValueError):
            seam(
                *neutral,
                diagnostics=("artifact read failed", "artifact is not valid UTF-8"),
            )
        with self.assertRaises(ValueError):
            seam(*neutral, diagnostics=("artifact read failed", "artifact read failed"))


class LayerIndependenceTests(_ObservationCase):
    """Changing evidence relevant to one layer leaves the other layer's result
    untouched (mutation-style, S02)."""

    def test_profile_evidence_changes_never_move_the_okf_result(self) -> None:
        variants = [
            _frame("type: analysis", 'framework_profile: "0.1-rc.1"'),
            _frame("type: analysis", 'framework_profile: "9.9"'),
            _frame("type: analysis", 'framework_profile: "0.1-rc.2"'),
            _frame("type: analysis"),
            _frame("type: made_up"),
            _frame("type: analysis", "tags: [a, b]"),
            _frame("type: analysis", "k: 'single'"),
        ]
        profiles = set()
        for content in variants:
            observation = self.observe(content)
            self.assertEqual(observation.okf_concept, ProfileLayerResult("pass", None))
            profiles.add(_layers(observation)[2:])
        self.assertGreater(len(profiles), 1)

    def test_okf_evidence_changes_never_move_the_profile_result(self) -> None:
        not_applicable_variants = [
            (b"# legacy\n", "not_evaluated"),
            (b"---\nopen only\n", "fail"),
            (_frame("type:"), "fail"),
            (_frame("type: analysis"), "pass"),
        ]
        okf_results = set()
        for content, okf_result in not_applicable_variants:
            observation = self.observe(content)
            self.assertEqual(
                observation.framework_profile, ProfileLayerResult("not_applicable", None)
            )
            self.assertEqual(observation.okf_concept.result, okf_result)
            okf_results.add(okf_result)
        self.assertEqual(okf_results, {"not_evaluated", "fail", "pass"})

        subset_fail_variants = [
            (_frame("type: analysis", "tags: [a]"), "pass"),
            (_frame("type: a", "type: b", "tags: [a]"), "fail"),
            (b"---\n" + b"[" * 3_000 + b"]" * 3_000 + b"\n---\n", "unverified"),
        ]
        for content, okf_result in subset_fail_variants:
            observation = self.observe(content)
            self.assertEqual(
                observation.framework_profile,
                ProfileLayerResult("fail", "PROFILE_YAML_OUT_OF_SUBSET"),
            )
            self.assertEqual(observation.okf_concept.result, okf_result)


# ---------------------------------------------------------------------------
# Data-induced scalar-key construction exceptions (M004 correction 035)
# ---------------------------------------------------------------------------


class ScalarKeyExceptionTests(_ObservationCase):
    """A bounded artifact whose scalar mapping key is an invalid explicit-tag
    spelling must not leak a PyYAML construction exception through the public
    observation. The pinned reference checker raises for these inputs; the M004
    contract owns a total result over them (architect Prompt 035 decision).
    """

    # (label, key line) -- an explicit-tag key PyYAML cannot construct.
    KEYS = (
        ("invalid bool key", "? !!bool abc\n:  value"),
        ("malformed timestamp key", "? !!timestamp abc\n:  value"),
        ("empty int key", '? !!int ""\n:  value'),
        ("sign-only int key", '? !!int "-"\n:  value'),
        ("empty float key", '? !!float ""\n:  value'),
    )

    def _document(self, key_block: str, *, type_first: bool) -> bytes:
        typed = 'type: analysis\nframework_profile: 0.1-rc.1'
        body = (typed + "\n" + key_block) if type_first else (key_block + "\n" + typed)
        return ("---\n" + body + "\n---\n").encode("utf-8")

    def test_invalid_key_returns_out_of_subset_in_either_order(self) -> None:
        for label, key_block in self.KEYS:
            for type_first in (False, True):
                with self.subTest(case=label, type_first=type_first):
                    observation = self.assert_row(
                        self._document(key_block, type_first=type_first), ROW_OUT_OF_SUBSET
                    )
                    # No exception text and no malformed lexeme is echoed.
                    rendered = repr(observation)
                    self.assertNotIn("abc", rendered)
                    self.assertNotIn("KeyError", rendered)
                    self.assertNotIn("groupdict", rendered)

    def test_invalid_key_is_deterministic_and_leaves_the_file_untouched(self) -> None:
        for label, key_block in self.KEYS:
            with self.subTest(case=label):
                path = self.write(
                    self._document(key_block, type_first=False), name="k.md"
                )
                before = path.read_bytes()
                before_stat = path.stat()
                before_entries = sorted(e.name for e in self.tmp.iterdir())
                first = observe_okf_profile_path(path)
                second = observe_okf_profile_path(path)
                self.assertEqual(first, second)
                self.assertEqual(_layers(first), ROW_OUT_OF_SUBSET)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(path.stat().st_mtime_ns, before_stat.st_mtime_ns)
                self.assertEqual(
                    sorted(e.name for e in self.tmp.iterdir()), before_entries
                )

    def test_invalid_key_never_raises_through_the_public_function(self) -> None:
        for label, key_block in self.KEYS:
            with self.subTest(case=label):
                # The whole point: no exception escapes, in either key order.
                for type_first in (False, True):
                    observe_okf_profile_path(
                        self.write(
                            self._document(key_block, type_first=type_first),
                            name="k2.md",
                        )
                    )

    def test_invalid_key_with_missing_or_empty_type_keeps_type_precedence(self) -> None:
        # An unavailable key identity does not change the accepted precedence:
        # a missing or empty literal `type` still yields the type-missing row.
        for label, key_block in self.KEYS:
            with self.subTest(case=label, type_state="missing"):
                self.assert_row(
                    ("---\n" + key_block + "\nk: 1\n---\n").encode("utf-8"),
                    ROW_TYPE_MISSING,
                )
            with self.subTest(case=label, type_state="empty"):
                self.assert_row(
                    ("---\ntype:\n" + key_block + "\n---\n").encode("utf-8"),
                    ROW_TYPE_MISSING,
                )

    def test_invalid_key_beside_a_real_duplicate_takes_invalid_precedence(self) -> None:
        # A genuine semantic duplicate elsewhere still wins with its accepted
        # conclusive-invalid row; the unavailable invalid-key identity does not
        # suppress it.
        for label, key_block in self.KEYS:
            with self.subTest(case=label):
                self.assert_row(
                    ("---\ntype: analysis\n" + key_block + "\na: 1\na: 2\n---\n").encode(
                        "utf-8"
                    ),
                    ROW_YAML_INVALID,
                )

    def test_repeated_invalid_keys_do_not_manufacture_a_duplicate(self) -> None:
        self.assert_row(
            b"---\ntype: analysis\n? !!bool abc\n: v\n? !!bool def\n: w\n---\n",
            ROW_OUT_OF_SUBSET,
        )


# ---------------------------------------------------------------------------
# Exact public surface (S03)
# ---------------------------------------------------------------------------


class PublicSurfaceTests(unittest.TestCase):
    FIVE_NAMES = (
        "OKF_PROFILE_OBSERVATION_CONTRACT_ID",
        "OKF_PROFILE_OBSERVATION_CONTRACT_VERSION",
        "OKFProfileObservation",
        "ProfileLayerResult",
        "observe_okf_profile_path",
    )

    def test_exactly_the_five_names_are_re_exported(self) -> None:
        import frutlups

        for name in self.FIVE_NAMES:
            self.assertIn(name, frutlups.__all__)
            self.assertIs(getattr(frutlups, name), getattr(okf_profile, name))
        self.assertEqual(len(frutlups.__all__), 147)
        self.assertEqual(len(set(frutlups.__all__)), 147)

    def test_no_private_observation_helper_is_re_exported(self) -> None:
        import frutlups

        for name in frutlups.__all__:
            if name in self.FIVE_NAMES:
                continue
            resolved = getattr(frutlups, name)
            module = getattr(resolved, "__module__", "")
            self.assertNotEqual(module, "frutlups.okf_profile", name)
        for forbidden in (
            "observe_okf_profile",
            "PROFILE_TYPE_REGISTRY",
            "MAX_ARTIFACT_BYTES",
            "YamlFailure",
            "load_yaml_bytes",
        ):
            self.assertNotIn(forbidden, frutlups.__all__)

    def test_module_all_is_exactly_the_five_names(self) -> None:
        self.assertEqual(
            tuple(sorted(okf_profile.__all__)), tuple(sorted(self.FIVE_NAMES))
        )

    def test_path_parameter_is_positional_only(self) -> None:
        import inspect

        signature = inspect.signature(observe_okf_profile_path)
        parameters = list(signature.parameters.values())
        self.assertEqual(len(parameters), 1)
        self.assertEqual(parameters[0].name, "path")
        self.assertEqual(parameters[0].kind, inspect.Parameter.POSITIONAL_ONLY)
        with self.assertRaises(TypeError):
            observe_okf_profile_path(path="x.md")  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
