"""Shared harness for M006-S02 installed exact-pair offline qualification.

Only this test controller imports the development tree, for process ownership and
artifact inspection. Every product decision runs in a fresh process of the frozen
0.4.0 wheel installed into one isolated session venv; the final T export's own
scripts are the opposite consumer. Provider seats are scripted fakes.
"""

import base64
import configparser
import csv
import email.parser
import hashlib
import io
import json
import os
import shutil
import stat
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from frutlups import procs

TESTS = Path(__file__).resolve().parent
PACKAGE = TESTS.parent
WORKSPACE = PACKAGE.parents[1]
FIXTURES = TESTS / "fixtures"
FINAL = FIXTURES / "v5_final"
ARTIFACTS = WORKSPACE / "02_analysis/m006_s04_admission/r1/artifacts"
TOOLCHAIN = WORKSPACE / "02_analysis/m006_s02_admission/r1/toolchain.json"

WHEEL = dict(path=ARTIFACTS / "frutlups-0.4.0-py3-none-any.whl", bytes=130893,
             sha="628c2eb95723d03e8353dce5b70f0ff24d41f38408ab97d5b83882c8deb3d2e4")
DEPENDENCY = dict(path=ARTIFACTS / "pyyaml-6.0.3-cp314-cp314-win_amd64.whl", bytes=156429,
                  sha="4a2e8cebe2ff6ab7d1050ecd59c25d4c8bd7e6f400f5f82b96557ac0abafd0ac")
SOURCE_COMMIT = "7351589890e55461133db3a83022747568a4f629"
T_COMMIT = "711bce067eb0b3d05abf8cdec54f3cc812f62a5d"
MANIFEST = dict(path=FIXTURES / "v5_final.manifest.json", bytes=141317,
                sha="c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c")
ARCHIVE = dict(path=FINAL / "template-5.0.0.tar", bytes=460800,
               sha="a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a")
CORPORA = {
    "protocol_v2.json": dict(path=FINAL / "protocol_v2.json", bytes=25323,
                             sha="59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891"),
    "autonomy_v1.json": dict(path=FINAL / "autonomy_v1.json", bytes=343766,
                             sha="ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150"),
}
FAKES = ("fake_pi.py", "fake_claude.py")
SEAT_ROLES = ("coder", "reviewer", "claude_reviewer", "holistic")
PER_COMMAND_GUARD = 600  # harness guard for incidental positive setup, not a product limit
DONE = {"action": "stop", "reason": "done", "detail": "all admitted roadmap milestones completed"}
MILESTONE_BOUNDARY = {"action": "stop", "reason": "boundary",
                      "detail": "milestone completed; mark roadmap milestone done separately"}
SLICE_BOUNDARY = {"action": "stop", "reason": "boundary", "detail": "one slice accepted"}
GRANT_DONE = {"action": "stop", "reason": "boundary", "detail": "current grant boundary completed"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(path):
    raw = Path(path).read_bytes()
    return {"bytes": len(raw), "sha": sha(raw)}


class ArtifactError(AssertionError):
    """A pinned qualification input is missing or differs; nothing may use it."""


def require(pin, path=None):
    """Return the exact pinned bytes, or refuse before any install or launch."""
    path = Path(path or pin["path"])
    if not path.is_file():
        raise ArtifactError(f"missing pinned artifact: {path.name}")
    raw = path.read_bytes()
    if (len(raw), sha(raw)) != (pin["bytes"], pin["sha"]):
        raise ArtifactError(f"pinned artifact identity mismatch: {path.name}")
    return raw


def _safe_member(name):
    member = PurePosixPath(name)
    if member.is_absolute() or ".." in member.parts or "\\" in name or ":" in name:
        raise ArtifactError(f"unsafe archive member: {name}")
    return member


def inspect_wheel(raw, project, version):
    """Complete RECORD, metadata and member identities of one wheel."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        for item in archive.infolist():
            _safe_member(item.filename)
            assert not item.is_dir()
            assert stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG)
        (record,) = [n for n in names if n.endswith(".dist-info/RECORD")]
        rows = list(csv.reader(io.StringIO(archive.read(record).decode("utf-8"))))
        assert len(rows) == len(names) and {r[0] for r in rows} == set(names)
        members = {}
        for name, digest, size in rows:
            data = archive.read(name)
            members[name] = {"bytes": len(data), "sha": sha(data)}
            if name == record:
                assert digest == size == ""
                continue
            algorithm, expected = digest.split("=", 1)
            assert algorithm == "sha256" and int(size) == len(data), name
            assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip("=") == expected, name
        folder = record.rsplit("/", 1)[0]
        meta = email.parser.BytesParser().parsebytes(archive.read(folder + "/METADATA"))
        wheel = email.parser.BytesParser().parsebytes(archive.read(folder + "/WHEEL"))
        assert meta["Name"].lower() == project.lower() and meta["Version"] == version
        result = {"record": record, "members": members, "metadata": dict(meta.items()),
                  "tags": wheel.get_all("Tag", []), "requires": meta.get_all("Requires-Dist", []),
                  "requires_python": meta["Requires-Python"]}
        if project == "frutlups":
            parser = configparser.ConfigParser()
            parser.read_string(archive.read(folder + "/entry_points.txt").decode("utf-8"))
            result["console_scripts"] = dict(parser["console_scripts"])
            result["payload"] = {n: archive.read(n) for n in names if not n.startswith(folder + "/")}
        return result


def verified_template():
    """The 69 final T members, after manifest, archive and member identity checks."""
    manifest = json.loads(require(MANIFEST))
    assert manifest["commit"] == T_COMMIT
    assert manifest["archive"]["raw_sha256"] == ARCHIVE["sha"]
    members = {row["path"]: row for row in manifest["members"]}
    assert len(members) == 69
    files = {}
    with tarfile.open(fileobj=io.BytesIO(require(ARCHIVE))) as source:
        assert source.pax_headers == {"comment": T_COMMIT}
        for item in source.getmembers():
            _safe_member(item.name)
            assert not item.issym() and not item.islnk(), item.name
            if item.isdir():
                continue
            assert item.isfile() and item.mode == 0o664 and item.name not in files, item.name
            data = source.extractfile(item).read()
            row = members[item.name]
            assert (len(data), sha(data)) == (row["bytes"], row["raw_sha256"]), item.name
            files[item.name] = data
    assert files.keys() == members.keys()
    for name, pin in CORPORA.items():
        row = next(r for r in manifest["corpora"] if r["path"] == name)
        assert (row["bytes"], row["raw_sha256"]) == (pin["bytes"], pin["sha"])
        require(pin)
    return manifest, files


def toolchain():
    """Selected Python, configured Git and the console launcher, by pinned identity."""
    pins = json.loads(TOOLCHAIN.read_bytes())
    import platform
    import setuptools
    assert platform.python_version() == pins["python"], "selected Python version differs"
    git = shutil.which("git")
    if not git:
        raise ArtifactError("configured Git is not available")
    paths = {"selected_python": Path(sys.executable), "configured_git": Path(git).resolve(),
             "fake_console_launcher": Path(setuptools.__file__).parent / "cli-64.exe"}
    for key, path in paths.items():
        expected = pins["files"][key]
        if identity(path) != {"bytes": expected["bytes"], "sha": expected["raw_sha256"]}:
            raise ArtifactError(f"toolchain identity mismatch: {key}")
    return paths


INSPECTION = r"""
import base64, csv, hashlib, io, json, pathlib, sys
import importlib.metadata as m
prefix = pathlib.Path(sys.prefix).resolve()
expected = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
forbidden = [p.lower() for p in sys.argv[2:]]
import frutlups, yaml
from frutlups.seats import pi
result = {"version": frutlups.__version__, "yaml": yaml.__version__,
          "isolated": sys.flags.isolated, "no_site_user": sys.flags.no_user_site,
          "pyvenv_isolated": "include-system-site-packages = false" in (prefix / "pyvenv.cfg").read_text(),
          "distributions": {}}
for p in sys.path:
    low = p.lower()
    assert not any(low.startswith(f) for f in forbidden), p
    if "site-packages" in low:
        assert pathlib.Path(p).resolve().is_relative_to(prefix), p
for name, module in (("frutlups", frutlups), ("PyYAML", yaml)):
    dist = m.distribution(name)
    origin = pathlib.Path(module.__file__).resolve()
    assert origin.is_relative_to(prefix), origin
    url = json.loads(dist.read_text("direct_url.json"))
    assert "archive_info" in url and not url.get("dir_info", {}).get("editable", False)
    for path, record in expected[name].items():
        if path.endswith("/RECORD"):
            continue
        raw = pathlib.Path(dist.locate_file(path)).read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (record["bytes"], record["sha"]), path
    checked = 0
    for path, digest, size in csv.reader(io.StringIO(dist.read_text("RECORD"))):
        if not digest:
            assert path.endswith("/RECORD") or path.endswith(".pyc"), path
            continue
        raw = pathlib.Path(dist.locate_file(path)).read_bytes()
        algorithm, wanted = digest.split("=", 1)
        assert algorithm == "sha256" and len(raw) == int(size), path
        assert base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=") == wanted, path
        checked += 1
    result["distributions"][name] = {"version": dist.version, "record_hashes_checked": checked,
                                     "origin_in_venv": True}
result["entry_points"] = [[e.group, e.name, e.value] for e in m.distribution("frutlups").entry_points]
extension = pathlib.Path(str(pi.extension_path())).resolve()
assert extension.is_relative_to(prefix)
result["pi_extension_sha"] = hashlib.sha256(extension.read_bytes()).hexdigest()
print(json.dumps(result))
"""


@dataclass
class Installed:
    """One isolated venv holding exactly the two frozen wheels."""
    scratch: Path
    venv: Path
    python: Path
    console: Path
    git: Path
    seats: dict
    wheel: dict
    dependency: dict
    template: dict
    manifest: dict
    timings: Path
    installed_files: dict = field(default_factory=dict)
    install_seconds: float = 0.0

    def env(self, **extra):
        """Minimal child environment: no ambient Python/Git/pip injection or credentials."""
        system = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT")
               if key in os.environ}
        home = self.scratch / "home"
        env.update(PATH=os.pathsep.join((str(self.python.parent), str(self.git.parent),
                                         str(Path(system) / "System32"))),
                   TEMP=str(self.scratch / "temp"), TMP=str(self.scratch / "temp"),
                   HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), LOCALAPPDATA=str(home),
                   PYTHONDONTWRITEBYTECODE="1", PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1",
                   GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                   TEMPLATE_GIT_EXECUTABLE=str(self.git))
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def record(self, **row):
        """External evidence only: the timings journal lives in pytest's base temp."""
        with self.timings.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")

    def synthetic(self, label, **row):
        """A derived total; it overlaps actual command records and is never summed with them."""
        self.record(record="synthetic", label=label, test=os.environ.get("PYTEST_CURRENT_TEST", ""), **row)

    def register(self, project):
        """Name a project root so its own ledger can be read for seat/verifier time."""
        self.record(record="project", root=str(project.root), portable_root=self.portable(project.root),
                    test=os.environ.get("PYTEST_CURRENT_TEST", ""))

    def portable(self, value):
        """A path-free display copy; the actual value is retained beside it."""
        text = str(value)
        pairs = [(str(self.venv), "<venv>"), (str(self.scratch), "<session>"), (str(self.git), "<git>"),
                 (str(self.timings.parent), "<basetemp>"), (str(WORKSPACE), "<repo>"),
                 (sys.executable, "<selected-python>")]
        for source, label in sorted(pairs, key=lambda pair: len(pair[0]), reverse=True):
            for variant in (source, source.replace("\\", "/")):
                index = text.lower().find(variant.lower())
                while index >= 0:
                    text = text[:index] + label + text[index + len(variant):]
                    index = text.lower().find(variant.lower(), index + len(label))
        return text

    def run(self, argv, cwd, output, *, env=None, timeout=PER_COMMAND_GUARD, stdin=None, label=None, kind="other"):
        """One owned child process with complete retained stdout/stderr files."""
        output = Path(output)
        output.mkdir(parents=True, exist_ok=False)
        env = env or self.env()
        # Controller-only instrumentation (phase observer, pytest plugins, PYTHONPATH) never
        # reaches installed or template children.
        leaked = sorted(key for key in env if key.upper().startswith(("PYTEST", "FRUTLUPS_S02"))
                        or key.upper() in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"))
        assert not leaked, leaked
        argv = [str(a) for a in argv]
        utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        started = time.monotonic()
        result = procs.run_process(argv, Path(cwd), env, timeout, output / "stdout", output / "stderr", stdin)
        elapsed = round(time.monotonic() - started, 3)
        streams = {}
        for name, path in (("stdout", result.stdout_path), ("stderr", result.stderr_path)):
            raw = path.read_bytes() if path.exists() else b""
            streams[name] = {"bytes": len(raw), "sha256": sha(raw), "path": str(path),
                             "portable_path": self.portable(path), "truncated": getattr(result, f"{name}_truncated")}
        self.record(record="command", label=label or output.name, kind=kind, utc_start=utc, argv=argv,
                    portable_argv=[self.portable(a) for a in argv], cwd=str(cwd), portable_cwd=self.portable(cwd),
                    exit=result.exit, timed_out=result.timed_out, exception=result.exception,
                    timeout=timeout, elapsed=elapsed, streams=streams,
                    test=os.environ.get("PYTEST_CURRENT_TEST", ""))
        if timeout == PER_COMMAND_GUARD:
            assert (result.timed_out, result.exception) == (False, None), (output, result)
        assert not result.stdout_truncated and not result.stderr_truncated, output
        return Completed(result.exit, result.stdout_path.read_bytes(), result.stderr_path.read_bytes(),
                         result.timed_out, result.exception, elapsed)

    def rehash(self):
        """Installed payload and frozen inputs are unchanged since installation."""
        current = {rel: identity(self.venv / rel) for rel in self.installed_files}
        assert current == self.installed_files
        require(WHEEL)
        require(DEPENDENCY)
        require(ARCHIVE)
        require(MANIFEST)
        for pin in CORPORA.values():
            require(pin)
        return current


@dataclass
class Completed:
    exit: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    exception: str | None
    elapsed: float

    @property
    def text(self):
        """Decoded stdout with Windows text-mode newlines translated; raw bytes stay retained."""
        return self.stdout.decode("utf-8").replace("\r\n", "\n")

    @property
    def err(self):
        return self.stderr.decode("utf-8", "replace").replace("\r\n", "\n")

    @property
    def rows(self):
        return [json.loads(line) for line in self.text.splitlines() if line.strip()]


PI_SHIM = """#!"{python}" -I -B
import sys
sys.path.insert(0, {seats!r})
import pair_seat
pair_seat.pi()
"""
CLAUDE_SHIM = """#!"{python}" -I -B
import sys
sys.path.insert(0, {seats!r})
import pair_seat
pair_seat.claude()
"""

PAIR_SEAT = r'''"""Harness-owned additions around the byte-exact shared fake seats.

Plan rows may carry a "pair" mapping; without it the shared fake behaves exactly
as in the development suite. Nothing here contacts a provider.
"""
import hashlib
import json
import os
import re
import runpy
import sys
import time
from pathlib import Path

import fake_pi

SEATS = Path(__file__).resolve().parent
_action, _report = fake_pi.action, fake_pi.report
_state = {}


def _normalized(raw):
    return hashlib.sha256(raw if b"\0" in raw else raw.replace(b"\r\n", b"\n")).hexdigest()


def _evidence_paths(prompt):
    """Every changed path named by the cited canonical manifests and complete path index."""
    paths, hashes = [], {}
    for ref in dict.fromkeys(re.findall(r"`(05_governance/reviews/[^`]+_manifest[^`]*\.json)`", prompt)):
        for row in json.loads(Path(ref).read_text("utf-8"))["changed"]:
            hashes[row["path"]] = (row["kind"], row["sha"])
    for ref in dict.fromkeys(re.findall(r"`(05_governance/reviews/[^`]+_index\.json)`", prompt)):
        paths += [row["path"] for row in json.loads(Path(ref).read_text("utf-8"))["paths"]]
    return list(dict.fromkeys(paths + list(hashes))), hashes


def literal_names(directory, count):
    """Spaces, Unicode, brackets, braces, leading dashes and literal pathspec characters."""
    names = []
    for index in range(count):
        stem = ["-leading dash", "sp ace", "ünïcödé ✓", "[bracket]", "{brace}!", "glob*?not", "^tilde~",
                "colon-free:"][index % 8].replace("*", "").replace("?", "").replace(":", "")
        names.append(f"{directory}/{index:04d} {stem} {'x' * 30}.txt")
    return names


def action(prompt):
    selected = dict(_action(prompt))
    pair = selected.get("pair", {})
    _state.update(selected=selected, pair=pair, prompt=prompt)
    if pair.get("observe_reservation"):
        rows = [json.loads(line) for line in Path("05_governance/ledger.jsonl").read_text("utf-8").splitlines() if line]
        done = {row["invocation"] for row in rows if row["ev"] == "attempt_finished"}
        mine = [row for row in rows if row["ev"] == "attempt_started" and row["role"] == "coder"]
        Path(pair["observe_reservation"]).write_text(json.dumps(
            {"role": mine[-1]["role"], "invocation": mine[-1]["invocation"],
             "unfinished": mine[-1]["invocation"] not in done}), encoding="utf-8")
    for rel, value in pair.get("raw_writes", {}).items():
        Path(rel).parent.mkdir(parents=True, exist_ok=True)
        Path(rel).write_bytes(bytes.fromhex(value))
    if "generate" in pair:
        for rel in literal_names(pair["generate"]["dir"], pair["generate"]["count"]):
            Path(rel).parent.mkdir(parents=True, exist_ok=True)
            Path(rel).write_bytes(("literal evidence " + rel + "\n").encode("utf-8"))
    if "huge" in pair:
        spec = pair["huge"]
        body = bytearray(b"evidence line\n" * (spec["mib"] * 1024 * 1024 // 14))
        if spec.get("token"):
            body[-200:-200 + len(spec["token"])] = spec["token"].encode("ascii")
        Path(spec["path"]).write_bytes(bytes(body))
    for rel in pair.get("deletes", []):
        Path(rel).unlink()
    for source, destination in pair.get("moves", {}).items():
        # A worktree-only rename: seats never stage (that is a path violation).
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(source).rename(destination)
    if pair.get("record_pid"):
        Path(pair["record_pid"]).write_text(str(os.getpid()), encoding="utf-8")
    if pair.get("sleep"):
        time.sleep(pair["sleep"])
    if "flood" in pair:
        line = json.dumps({"type": "tool_execution_update", "pad": "x" * 65000}) + "\n"
        count = pair["flood"] // len(line) + 1
        out = sys.stdout
        for _ in range(count):
            out.write(line)
        out.flush()
    if "outcome" in pair and os.environ.get("FRUTLUPS_SEAT") == "coder":
        value = json.loads(re.findall(r"```json\n(.*?)\n```", prompt, re.S)[-1])
        value.update(pair["outcome"])
        selected["text"] = json.dumps(value)
    if "inspect" in pair:
        # Read canonical full evidence: every byte of every path the prompt binds,
        # checked against its stored identity, then report only what was found.
        token = pair["inspect"].encode("utf-8")
        paths, hashes = _evidence_paths(prompt)
        found, verified, total = [], 0, 0
        for rel in paths:
            target = Path(rel)
            if target.is_file():
                raw = target.read_bytes()
                total += len(raw)
                if rel in hashes and hashes[rel][1] == _normalized(raw):
                    verified += 1
                if token in raw:
                    found.append(rel)
        record = {"paths": len(paths), "verified": verified, "bytes": total, "found": found,
                  "prompt_bytes": len(prompt.encode("utf-8")), "order": paths}
        log = Path("local_state/pair-inspected.jsonl")
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        if found:
            ident = pair.get("finding", "M001-S01-R1-F1")
            selected["verdict"] = "needs_work"
            selected["rows"] = [f"| {ident} | P1 | open | Late defect in {found[0]}. |"]
    return selected


def report(prompt, selected):
    text = _report(prompt, selected)
    pair = _state.get("pair", {})
    if pair.get("canary"):
        text = text.replace("Scripted fixture evidence.", "Scripted fixture evidence. " + pair["canary"])
    return text


fake_pi.action, fake_pi.report = action, report
_print = print


def _emit(*args, **kwargs):
    pair = _state.get("pair", {})
    if args and isinstance(args[0], str) and args[0].startswith('{"type": "message_end"'):
        event = json.loads(args[0])
        usage = pair.get("usage")
        if usage == "complete":
            event["message"]["usage"] = {"input": 17, "cacheRead": 5, "cacheWrite": 2, "output": 3,
                                         "cost": {"total": 0.01}}
        elif usage == "unknown":
            event["message"].pop("usage", None)
        if "final_bytes" in pair:
            event["message"]["content"] = [{"type": "text", "text": "y" * pair["final_bytes"]}]
        args = (json.dumps(event),) + args[1:]
    if args and pair.get("drop_settlement") and args[0] == json.dumps({"type": "agent_settled"}):
        return
    _print(*args, **kwargs)


def pi():
    prompt = next(a[1:] for a in sys.argv[1:] if a.startswith("@"))
    sys.argv = [str(SEATS / "fake_pi.py"), prompt]
    fake_pi.print = _emit
    fake_pi.main()


def claude():
    sys.argv = [str(SEATS / "fake_claude.py")]
    real_stdout = sys.stdout

    class Writer:
        def write(self, text):
            pair = _state.get("pair", {})
            if text.startswith("{") and '"is_error"' in text:
                value = json.loads(text)
                if pair.get("usage") == "complete":
                    value["modelUsage"] = {"fake": {"inputTokens": 1, "cacheReadInputTokens": 10,
                                                    "cacheCreationInputTokens": 2, "outputTokens": 4}}
                elif pair.get("usage") == "unknown":
                    value.pop("usage", None)
                    value.pop("total_cost_usd", None)
                if "raw_pad" in pair:
                    value["pad"] = "z" * pair["raw_pad"]
                text = json.dumps(value)
            return real_stdout.write(text)

        def flush(self):
            real_stdout.flush()

    sys.stdout = Writer()
    try:
        runpy.run_path(str(SEATS / "fake_claude.py"), run_name="__main__")
    finally:
        sys.stdout = real_stdout
'''


def install(scratch, record_path=None):
    """Create one fresh isolated venv and install exactly the two frozen wheels."""
    scratch = Path(scratch)
    tools = toolchain()
    wheel_raw, dependency_raw = require(WHEEL), require(DEPENDENCY)
    wheel = inspect_wheel(wheel_raw, "frutlups", "0.4.0")
    dependency = inspect_wheel(dependency_raw, "PyYAML", "6.0.3")
    manifest, template = verified_template()
    for name in ("temp", "home", "wheelhouse", "seats", "logs", "hooks"):
        (scratch / name).mkdir(parents=True)
    # Install from owned copies whose identity is re-verified; the originals stay untouched.
    copies = {}
    for pin, raw in ((WHEEL, wheel_raw), (DEPENDENCY, dependency_raw)):
        target = scratch / "wheelhouse" / Path(pin["path"]).name
        target.write_bytes(raw)
        require(pin, target)
        copies[target] = pin
    venv = scratch / "v"
    python = venv / "Scripts" / "python.exe"
    installed = Installed(scratch, venv, python, venv / "Scripts" / "frutlups.exe",
                          tools["configured_git"], {}, wheel, dependency, template, manifest,
                          Path(record_path or scratch / "timings.jsonl"))
    base_env = installed.env()
    started = time.monotonic()
    created = installed.run([tools["selected_python"], "-I", "-B", "-m", "venv", venv], scratch,
                            scratch / "logs" / "venv", env=base_env, kind="install")
    assert created.exit == 0, created.stderr
    requirements = scratch / "install.txt"
    requirements.write_text("".join(f"{p.as_uri()} --hash=sha256:{pin['sha']}\n" for p, pin in copies.items()),
                            encoding="utf-8")
    flags = ["-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check"]
    result = installed.run([python, *flags, "install", "--no-index", "--no-deps", "--no-cache-dir",
                            "--require-hashes", "-r", requirements], scratch, scratch / "logs" / "install",
                           env=base_env, kind="install")
    assert result.exit == 0, result.stdout + result.stderr
    check = installed.run([python, *flags, "check"], scratch, scratch / "logs" / "pip-check",
                          env=base_env, kind="install")
    assert (check.exit, check.text.strip()) == (0, "No broken requirements found."), check.stdout
    installed.install_seconds = round(time.monotonic() - started, 3)
    site = venv / "Lib" / "site-packages"
    for info in (wheel, dependency):
        for name, ident in info["members"].items():
            if not name.endswith("/RECORD"):
                assert identity(site / name) == ident, name
                installed.installed_files[(Path("Lib/site-packages") / name).as_posix()] = ident
    installed.installed_files["Scripts/frutlups.exe"] = identity(installed.console)
    # Seats: the pinned launcher plus byte-exact shared fakes and harness additions.
    seats = scratch / "seats"
    for name in FAKES:
        shutil.copyfile(TESTS / name, seats / name)
        assert (seats / name).read_bytes() == (TESTS / name).read_bytes()
    (seats / "pair_seat.py").write_text(PAIR_SEAT, encoding="utf-8")
    for name, shim in (("pi", PI_SHIM), ("claude", CLAUDE_SHIM)):
        launcher = seats / f"fake-{name}.exe"
        shutil.copyfile(tools["fake_console_launcher"], launcher)
        (seats / f"fake-{name}-script.py").write_text(shim.format(python=python, seats=str(seats)),
                                                     encoding="utf-8")
        installed.seats[name] = launcher
    return installed


def inspect_installed(installed, tmp):
    expected = tmp / "expected.json"
    expected.write_text(json.dumps({"frutlups": installed.wheel["members"],
                                    "PyYAML": installed.dependency["members"]}), encoding="utf-8")
    forbidden = [str(PACKAGE / "src"), str(Path(sys.executable).parent.parent)]
    result = installed.run([installed.python, "-I", "-B", "-c", INSPECTION, expected, *forbidden],
                           tmp, tmp / "inspection", kind="install")
    assert result.exit == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


# ---------------------------------------------------------------- projects

MAIN_CONFIG = dict(schema="frutlups/1", until="roadmap", max_corrective_rounds=2, max_jobs=30,
                   max_wall_minutes=15, commit_on_accept=True, prompt_dir="prompts/for_coding_agent",
                   review_prompt_dir="prompts/for_review_agent", reviews_dir="05_governance/reviews",
                   ledger="05_governance/ledger.jsonl")
SEATS_CONFIG = {
    "coder": dict(adapter="pi", provider="fake", model="fake", effort="medium", corrective_effort="high"),
    "reviewer": dict(adapter="claude", model="fake", effort="high"),
    "claude_reviewer": dict(adapter="claude", model="fake", effort="high"),
    "holistic": dict(adapter="claude", model="fake", effort="high"),
}


def _toml(value):
    return json.dumps(value)


class Project:
    """One owned final-template project driven only through installed/T processes."""

    def __init__(self, installed, root, out):
        self.installed, self.root, self.out = installed, Path(root), Path(out)
        self.count = 0
        self.holistic = True
        self.ledger = self.root / "05_governance" / "ledger.jsonl"

    def _output(self, label):
        self.count += 1
        return self.out / f"{self.count:03d}-{label}"

    def f(self, *args, label=None, timeout=PER_COMMAND_GUARD, env=None, argv=None):
        """The real installed console executable, launched from outside any checkout."""
        command = argv or [self.installed.console]
        return self.installed.run([*command, *args], self.root, self._output(label or "f-" + args[0]),
                                  env=env, timeout=timeout, label=label, kind="public")

    def t(self, script, *args, label=None, env=None, timeout=PER_COMMAND_GUARD):
        """The final T export's own script, with the project's selected Python."""
        return self.installed.run([self.installed.python, "-B", f"scripts/{script}.py", *args], self.root,
                                  self._output(label or f"t-{script}"), env=env, timeout=timeout,
                                  label=label, kind="template")

    def tledger(self, *args, **kwargs):
        return self.t("ledger", "--root", self.root, *args, **kwargs)

    def git(self, *args, check=True):
        result = self.installed.run([self.installed.git, "-C", self.root, *args], self.root,
                                    self._output("git"), kind="git")
        if check:
            assert result.exit == 0, result.stdout + result.stderr
        return result.stdout

    def commit_all(self, message):
        self.git("add", "--all")
        self.git("commit", "-qm", message)

    def head(self):
        return self.git("rev-parse", "HEAD").decode().strip()

    def events(self):
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in self.ledger.read_text("utf-8").splitlines() if line.strip()]

    def snapshot(self):
        """Raw ledger, HEAD, raw/semantic index, Git-visible files and job directories."""
        names = self.git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
        files = {}
        for name in sorted(filter(None, names)):
            path = self.root / name
            files[name] = identity(path) if path.is_file() else None
        jobs = self.root / "local_state/frutlups/jobs"
        index = self.root / ".git" / "index"
        return {"head": self.head(), "index": self.git("ls-files", "--stage", "-v", "-z"),
                "raw_index": identity(index) if index.exists() else None,
                "status": self.git("status", "--porcelain=v1", "-z", "--untracked-files=all"),
                "ledger": self.ledger.read_bytes() if self.ledger.exists() else None,
                "files": files, "jobs": sorted(os.listdir(jobs)) if jobs.is_dir() else None,
                "local": {name: identity(self.root / name) for name in
                          ("frutlups.local.toml", "project.local.toml") if (self.root / name).exists()}}

    def plan(self, value):
        (self.root / "local_state").mkdir(exist_ok=True)
        (self.root / "local_state" / "plan.json").write_text(json.dumps(value), encoding="utf-8")

    def counts(self):
        directory = self.root / "local_state"
        return {p.name[:-len("-count.txt")]: int(p.read_text()) for p in directory.glob("*-count.txt")}

    def authority(self, name, text=None):
        rel = f"05_governance/{name}.md"
        path = self.root / rel
        path.write_text(text or f"Owner approved {name} for installed offline qualification.\n",
                        encoding="utf-8")
        return rel

    def admit(self, milestone="M001", run="R001", slices=("M001-S01",), stop=None, *, jobs=20,
              seconds=900, transport=1, fmt=1, rounds=2, commit=True, expect=0):
        """Owner grant through the final T CLI; authority committed before admission."""
        rel = self.authority(f"approval-{run}")
        if commit:
            self.commit_all(f"owned {run} authority")
        args = ["run", "admit", milestone, "--run-id", run]
        for item in slices:
            args += ["--slice", item]
        stop = stop or (milestone if self.holistic else slices[-1])
        args += ["--stop", stop, "--jobs", jobs, "--transport-retries", transport,
                 "--format-retries", fmt, "--seconds", seconds, "--max-corrective-rounds", rounds,
                 "--authority", rel, "--reason", f"Owner approved {run}"]
        result = self.tledger(*args, label=f"admit-{run}")
        if expect == 0:
            assert (result.exit, result.text) == (0, f"run_admitted {run}\n"), result.stdout + result.stderr
        return result

    def status_pair(self, label="status"):
        """F status text equals the final T status text."""
        f = self.f("status", self.root, label=f"{label}-F")
        t = self.tledger("status", label=f"{label}-T")
        assert f.exit == 0 and t.exit == 0, (f.stderr, t.stderr)
        assert f.stdout.replace(b"\r\n", b"\n") == t.stdout.replace(b"\r\n", b"\n"), (f.text, t.text)
        return f.text

    def check(self, label="check"):
        result = self.tledger("check", label=label)
        assert (result.exit, result.text) == (0, "ledger: ok\n"), result.stdout + result.stderr
        return result

    def run_json(self, *args, label=None, **kwargs):
        return self.f("run", self.root, "--json", *args, label=label or "run", **kwargs)


def make_project(installed, tmp, *, milestones=None, holistic=True, risk="ordinary", autonomy=True,
                 verification=None, plan=None, main=None, commit=True, name="p"):
    """Materialize the verified final export and commit owned setup before any grant."""
    root = Path(tmp) / name
    root.mkdir(parents=True)
    for rel, data in installed.template.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    data = yaml.safe_load((root / "roadmap.yaml").read_text("utf-8"))
    data["verification"]["full"] = verification or ["python", "-c", "print('verified')"]
    template_milestone = data["milestones"][0]
    template_slice = template_milestone["slices"][0]
    milestones = milestones or [("M001", "active", ["M001-S01"])]
    data["milestones"] = []
    for mid, status, slices in milestones:
        value = {**json.loads(json.dumps(template_milestone)), "id": mid, "status": status,
                 "holistic_review": holistic, "risk": risk}
        value["slices"] = [{**json.loads(json.dumps(template_slice)), "id": sid} for sid in slices]
        data["milestones"].append(value)
    if autonomy:
        data["autonomy"] = {"schema": "frutlups.autonomy/1"}
    else:
        data.pop("autonomy", None)
    (root / "roadmap.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with (root / ".gitignore").open("a", encoding="utf-8") as stream:
        stream.write("\nfrutlups.local.toml\nproject.local.toml\n")
    config = {**MAIN_CONFIG, **(main or {})}
    lines = [f"{key} = {_toml(value)}" for key, value in config.items() if key not in ("timeouts", "limits")]
    lines += ["[timeouts]", "coder_seconds = 60", "reviewer_seconds = 60", "verification_seconds = 120"]
    if "limits" in config:
        lines += ["[limits]"] + [f"{k} = {_toml(v)}" for k, v in config["limits"].items()]
    for seat, values in SEATS_CONFIG.items():
        lines.append(f"[seats.{seat}]")
        lines += [f"{key} = {_toml(value)}" for key, value in values.items()]
    (root / "frutlups.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    env = installed.env()
    local = {"schema": "frutlups.local/1", "pi": str(installed.seats["pi"]),
             "claude": str(installed.seats["claude"]), "git": str(installed.git),
             "path_dirs": [str(installed.python.parent), str(installed.git.parent)],
             "env_passthrough": [k for k in ("SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP")
                                 if k in env]}
    (root / "frutlups.local.toml").write_text("".join(f"{k} = {_toml(v)}\n" for k, v in local.items()),
                                             encoding="utf-8")
    (root / "project.local.toml").write_text(
        f'schema = "template.local/1"\npython = {_toml(str(installed.python))}\n', encoding="utf-8")
    project = Project(installed, root, Path(tmp) / "out")
    project.holistic = holistic
    installed.register(project)
    project.plan(plan or {"coder": [{"writes": {"07_app/result.txt": "useful installed output\n"}}]})
    for args in (("init", "-q"), ("config", "user.name", "Qualification"),
                 ("config", "user.email", "qualification@example.invalid"),
                 ("config", "core.autocrlf", "false"), ("config", "commit.gpgsign", "false"),
                 ("config", "core.hooksPath", str(installed.scratch / "hooks"))):
        project.git(*args)
    if commit:
        project.commit_all("owned installed qualification setup")
    return project


FAULT = r'''
"""Named fault injection at one durable approval boundary, in an owned fresh process.

Everything else is the real installed operation. The boundary being reached is
recorded before the fault; "death" terminates this process without cleanup.
"""
import ctypes, json, sys
from pathlib import Path
from frutlups import commit, gitws
from frutlups.cli import main

root, scope, boundary, mode, reached = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], Path(sys.argv[5])
active = {"scope": None}


def fault(where):
    if active["scope"] != scope or where != boundary or reached.exists():
        return
    reached.write_text(json.dumps({"boundary": where, "scope": scope, "mode": mode}), encoding="utf-8")
    if mode == "death":
        kernel = ctypes.windll.kernel32
        kernel.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel.TerminateProcess(ctypes.c_void_p(-1), 137)  # current process, no cleanup
    raise RuntimeError("owned fault at " + where)


recover = commit._recover
def tracked(root_, events, rm, execute=False, **kwargs):
    rows = commit.status(root_, events, rm, executable=kwargs.get("executable", "git"))
    pending = [row for row in rows if row["state"] == "pending"]
    active["scope"] = pending[0]["scope"] if execute and pending else None
    fault("before-recovery")
    try:
        return recover(root_, events, rm, execute, **kwargs)
    finally:
        active["scope"] = None
commit._recover = tracked

stage = gitws.stage_exact
def staged(*args, **kwargs):
    fault("before-staging")
    return stage(*args, **kwargs)
gitws.stage_exact = staged

git = gitws._git
def guarded(root_, *args, **kwargs):
    if args and args[0] == "commit":
        fault("after-staging")
        result = git(root_, *args, **kwargs)
        fault("after-commit")
        return result
    return git(root_, *args, **kwargs)
gitws._git = guarded

main(["run", root, "--json"])
'''


CORPUS_F = r'''
"""Installed F reader driver: folds every case, reports observations, judges nothing."""
import json, sys
from pathlib import Path
from frutlups import ledger, roadmap

cases, out = json.loads(Path(sys.argv[1]).read_text("utf-8")), {}
scratch = Path(sys.argv[2])
FIELDS = ("revision", "allowance", "consumed", "remaining", "closed")
for key, case in cases.items():
    model = roadmap.load(Path(case["roadmap"]))
    path = before = None
    try:
        if case["corpus"] == "protocol_v2.json" or case.get("ledger_file"):
            path = scratch / (key.replace("/", "_") + ".jsonl")
            path.write_bytes(b"\n".join(json.dumps(row).encode() for row in case["events"]) + b"\n")
            before = path.read_bytes()
            state = ledger.fold(ledger.read(path), model)
        else:
            state = ledger.fold(tuple(ledger._parse_event(row) for row in case["events"]), model)
    except ledger.LedgerError as exc:
        out[key] = {"error": str(exc), "input_unchanged": path is None or path.read_bytes() == before}
        continue
    current = state.slices["M001-S01"]
    out[key] = {"runs": {rid: {f: ledger._thaw(run[f]) for f in FIELDS} for rid, run in state.runs.items()},
                "head": ledger._thaw(state.run_head), "membership": ledger._thaw(state.run_membership),
                "step": current.step, "round": current.round, "checkpoints": len(state.checkpoints),
                "intents": len(state.intents),
                "holistic": state.holistic.get("M001", {}).get("step"), "done": "M001" in state.milestones_done,
                "unfinished": [k for k, attempt in state.attempts.items() if attempt["finish"] is None],
                "input_unchanged": path is None or path.read_bytes() == before}
print(json.dumps(out))
'''

CORPUS_T = r'''
"""Final T consumer driver: the exported scripts fold every case; reports observations only."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[2]) / "scripts"))
import ledger, roadmap, yaml

cases, out = json.loads(Path(sys.argv[1]).read_text("utf-8")), {}
FIELDS = ("revision", "allowance", "consumed", "remaining", "closed")
for key, case in cases.items():
    rm = yaml.safe_load(Path(case["roadmap"]).read_text("utf-8"))
    try:
        events = [ledger._validate(row, f"line {n}") for n, row in enumerate(case["events"], 1)]
        state = ledger.fold(events, rm)
    except (ValueError, KeyError) as exc:
        out[key] = {"error": str(exc)}
        continue
    current = state["slices"]["M001-S01"]
    out[key] = {"runs": {rid: {f: run[f] for f in FIELDS} for rid, run in state.get("runs", {}).items()},
                "head": state.get("run_head"), "membership": state.get("run_membership"),
                "step": current["step"], "round": current["round"],
                "checkpoints": len(state["checkpoints"]), "intents": len(state["intents"]),
                "holistic": state["holistic"].get("M001", {}).get("step"), "done": "M001" in state["milestones_done"]}
print(json.dumps(out, default=sorted))
'''


SCRUB_UTILITY = r'''
"""Installed utility driver: scrub a synthetic report and parse its grammar. Not public acceptance."""
import json, sys
from dataclasses import asdict
from pathlib import Path
from frutlups import receipt, verdict

text, root, secret = Path(sys.argv[1]).read_text("utf-8"), Path(sys.argv[2]), sys.argv[3]
scrubbed = receipt.scrub_text(text, root, {"PAIR_CANARY_TOKEN": secret})
review = asdict(verdict.parse(scrubbed, native=True))
print(json.dumps({"scrubbed": scrubbed, "review": review}))
'''


def attempts(project):
    """Durable attempts by role, from the actual ledger bytes."""
    counts = {}
    for row in project.events():
        if row["ev"] == "attempt_started":
            counts[row["role"]] = counts.get(row["role"], 0) + 1
    return counts


def evs(project):
    return [row["ev"] for row in project.events()]
