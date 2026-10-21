# CSV manual route

Use Python/PyYAML/Git. Read AGENTS.md, initialization/architect.md and the
[manual loop](operating.md#manual-slice-walkthrough). Choose names that do not exist:

```powershell
git archive --format=zip --output ../csv.zip HEAD
Expand-Archive ../csv.zip ../csv
Set-Location ../csv
```

Copy only the Python block below, unchanged, into `../rehearse.py` (outside the
project). Run `python -B ../rehearse.py` from the extracted `csv` root with your
selected interpreter. Run once; commands/output print and unexpected failures stop.

Simulated roles/reports are not independent review or approval.
The architect sends coding prompts 001/003/005 to the coder,
then records coded/verify and sends review prompts 002/004/006 to the reviewer.
The reviewer returns the named report; the architect saves/records it, accepts
only a pass, inspects the diff and commits. Needs-work returns via 003.

Discovery uses 07_app/tests (`-s`, no `-t .`). REQUIRED in scripts/verify_project.py
owns IDs, not receipts. The architect freezes IDs and critical literal checks
outside coder-owned 07_app/ before issuance. Here checks arrive before S02 to
preserve S01's coverage lesson; real projects install them before first issuance.
The generic verifier refuses until configured.

<!-- rehearsal -->
```python
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import yaml
def put(name, text):
    p = Path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode() if isinstance(text, str) else text)
def run(*args, expected=0):
    argv = [sys.executable if a == 'python' and i == 0 else a
            for i, a in enumerate(args)]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    print(json.dumps([list(args), r.returncode, r.stdout, r.stderr]))
    assert r.returncode == expected, args
    return r.stdout
def native(command, expected=0):
    script, *args = command.split()
    return run('python', '-B', 'scripts/' + script + '.py', *args, expected=expected)
def checkpoint(sid):
    native('ledger coded ' + sid)
    native('verify ' + sid)
    native('prompt ' + sid + ' --review')
def issue(sid):
    native('prompt ' + sid + ' --preview')
    native('prompt ' + sid)
def commit(message):
    run('git', 'diff', '--stat')
    run('git', 'add', '--all')
    run('git', 'diff', '--cached', '--stat')
    run('git', 'commit', '-m', message)
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def normalized(raw):
    return digest(raw if b'\0' in raw else raw.replace(b'\r\n', b'\n'))
# ARCHITECT
rm = yaml.safe_load(Path('roadmap.yaml').read_text())
rm['project'] = 'csv-count'
rm['verification'] = dict(full=['python', '-B', 'scripts/hermetic_verification.py'],
    focused_default=[],
    timeout_seconds=120, observation='fresh_process')
criteria = [
    'UTF-8 nonempty CSV: JSON rows excluding header, exit 0, stderr empty, bytes unchanged.',
    'CRLF name/Ada/Bob: rows 2; quoted Ada CRLF Lovelace: rows 1.',
    'Required child CLI tests pass without skips.']
slices = []
for sid, title, extra in [('M001-S01', 'CSV rows', []),
    ('M001-S02', 'Absent input', [
        'Absent path: exit 2, empty stdout, stderr input not found plus newline, no creation.'])]:
    slices.append(dict(id=sid, title=title, objective=title, acceptance=criteria + extra,
        non_goals=['Empty/malformed CSV, encodings, directories, other argc.'],
        read_first=['00_brief/CONTEXT.md'], kind='code'))
rm['milestones'] = [dict(id='M001', title='CSV CLI', status='active', risk='ordinary',
    holistic_review=False, slices=slices), dict(id='M002', title='Future',
    status='planned', risk='ordinary', holistic_review=False,
    slices=[dict(slices[0], id='M002-S01', title='Future format')])]
put('roadmap.yaml', yaml.safe_dump(rm, sort_keys=False))
put('00_brief/CONTEXT.md', '# CSV count\n\nStatus: active\n\nCount logical rows; preserve input.\n')
launcher = '''import sys
import unittest

REQUIRED = {'test_count.CountTests.test_user_path'}
def ids(node):
    for item in node:
        if isinstance(item, unittest.TestSuite):
            yield from ids(item)
        else:
            yield item.id()

suite = unittest.defaultTestLoader.discover('07_app/tests')
found = set(ids(suite))
if not found:
    sys.exit('zero tests discovered')
if not REQUIRED <= found:
    sys.exit('required test missing')
result = unittest.TextTestRunner().run(suite)
if any(test.id() in REQUIRED for test, reason in result.skipped):
    sys.exit('required test skipped')
if not result.wasSuccessful():
    sys.exit('project tests failed')
'''
put('scripts/verify_project.py', launcher)
p = Path('scripts/hermetic_verification.py')
put(str(p), p.read_bytes().replace(b'COMMANDS: list[object] = []',
    b'COMMANDS: list[object] = [["python", "-B", "scripts/verify_project.py"]]'))
native('roadmap check')
run('git', 'init')
run('git', 'config', 'user.name', 'CSV rehearsal')
run('git', 'config', 'user.email', 'csv@example.invalid')
commit('Baseline')
native('roadmap render')
commit('Render plan')
issue('M001-S01')
# CODER S01 (deliberate defect)
code = '''import csv
import json
import sys

with open(sys.argv[1], encoding='utf-8', newline='') as stream:
    rows = sum(1 for line in stream) - 1
print(json.dumps({'rows': rows}))
'''
tests = '''import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

class CountTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'input.csv'

    def invoke(self):
        cli = Path(__file__).resolve().parents[1] / 'count.py'
        return subprocess.run([sys.executable, '-B', str(cli), str(self.path)],
            capture_output=True, text=True, timeout=10)

    def test_user_path(self):
        cases = [(b'name\\r\\nAda\\r\\nBob\\r\\n', 2)]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                r = self.invoke()
                self.assertEqual(r.returncode, 0)
                self.assertEqual(r.stderr, '')
                self.assertEqual(json.loads(r.stdout), {'rows': expected})
                self.assertEqual(self.path.read_bytes(), raw)
'''
put('07_app/count.py', code)
put('07_app/tests/test_count.py', tests)
put('07_app/input.csv', b'name\r\nAda\r\nBob\r\n')
put('07_app/quoted.csv', b'name\r\n"Ada\r\nLovelace"\r\n')
# Raw SHA identifies worktree bytes; Git stores LF. See the scoped -text policy below before freezing exact-byte evidence.
raw = Path('07_app/input.csv').read_bytes()
print('input raw', digest(raw), 'bytes', len(raw), 'normalized evidence', normalized(raw))
# ARCHITECT
checkpoint('M001-S01')
# SIMULATED REVIEWER
assert json.loads(run('python', '-B', '07_app/count.py', '07_app/quoted.csv')) == {'rows': 2}
# ARCHITECT
def report(sid, round):
    base = '05_governance/reviews/m001/'
    finding, updates = '', ''
    verdict, status = 'pass', 'achieved'
    evidence = 'Receipt covers CSV counts and input preservation.'
    if sid == 'M001-S02':
        evidence += ' Missing-path exit/stdout/stderr also pass.'
    if (sid, round) == ('M001-S01', 1):
        verdict, status = 'needs_work', 'not_achieved'
        evidence = 'Quoted child fails; receipt covers ordinary input.'
        finding = ('| M001-S01-R1-F1 | P2 | open | Frozen quoted-newline acceptance: '
            'count.py counts lines: quoted.csv returns 2 instead of 1 logical row. |\n')
    if (sid, round) == ('M001-S01', 2):
        source = base + 'M001-S01_r1_review.md'
        sha = normalized(Path(source).read_bytes())
        updates = f'''
## Finding updates
| source | sha | id | disposition | related |
| --- | --- | --- | --- | --- |
| {source} | {sha} | M001-S01-R1-F1 | closed_by_review | - |
'''
    path = base + f'{sid}_r{round}_review.md'
    put(path, f'''# Review: {sid} round {round}
## Findings
| id | severity | disposition | summary |
| --- | --- | --- | --- |
{finding}{updates}
## Closure Decision
Objective status: {status}
Objective evidence: {evidence}
## Verdict
Verdict: {verdict} - next: {'correct F1' if finding else 'accept slice'}
''')
    native('ledger record ' + path)

report('M001-S01', 1)
issue('M001-S01')
# CODER correction
code = code.replace('for line in stream', 'for row in csv.reader(stream)')
tests = tests.replace(", 2)]", ", 2), (b'name\\r\\n\"Ada\\r\\nLovelace\"\\r\\n', 1)]")
put('07_app/count.py', code)
put('07_app/tests/test_count.py', tests)
# ARCHITECT
checkpoint('M001-S01')
report('M001-S01', 2)
native('ledger accept M001-S01')
# ARCHITECT
# Protected assertions execute even with PYTHONOPTIMIZE=1.
launcher += r'''import json
import subprocess
import tempfile
from pathlib import Path

check = unittest.TestCase()
for raw, rows in [(b'name\r\nAda\r\nBob\r\n', 2),
                  (b'name\r\n"Ada\r\nLovelace"\r\n', 1), (None, None)]:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'input.csv'
        if raw is not None:
            path.write_bytes(raw)
        r = subprocess.run([sys.executable, '-B', '07_app/count.py', str(path)],
            capture_output=True, text=True, timeout=10)
        check.assertEqual(r.returncode, 2 if raw is None else 0)
        check.assertEqual(r.stderr, 'input not found\n' if raw is None else '')
        if raw is None:
            check.assertEqual(r.stdout, '')
            check.assertFalse(path.exists())
        else:
            check.assertEqual(json.loads(r.stdout), {'rows': rows})
            check.assertEqual(path.read_bytes(), raw)
'''
put('scripts/verify_project.py', launcher.replace("{'test_count.CountTests.test_user_path'}",
    "{'test_count.CountTests.test_user_path', 'test_count.CountTests.test_missing'}"))
run('git', 'diff', '--stat')
run('git', 'add', '--all')
stored = subprocess.check_output(['git', 'show', ':07_app/input.csv'], timeout=120)
assert stored == raw.replace(b'\r\n', b'\n') != raw
print('staged raw', digest(stored), 'bytes', len(stored))
run('git', 'diff', '--cached', '--stat')
run('git', 'commit', '-m', 'S01 accepted; S02 IDs frozen')
issue('M001-S02')
# CODER S02
code = '''import csv
import json
import sys
try:
    with open(sys.argv[1], encoding='utf-8', newline='') as stream:
        rows = sum(1 for row in csv.reader(stream)) - 1
except FileNotFoundError:
    print('input not found', file=sys.stderr)
    sys.exit(2)
print(json.dumps({'rows': rows}))
'''
tests += '''
    def test_missing(self):
        r = self.invoke()
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, '')
        self.assertEqual(r.stderr, 'input not found\\n')
        self.assertFalse(self.path.exists())
'''
put('07_app/count.py', code)
put('07_app/tests/test_count.py', tests)
# ARCHITECT
checkpoint('M001-S02')
report('M001-S02', 1)
native('ledger accept M001-S02')
commit('S02 done')
native('ledger close M001')
# ARCHITECT
rm['milestones'][0]['status'] = 'done'
put('roadmap.yaml', yaml.safe_dump(rm, sort_keys=False))
native('roadmap check')
native('roadmap render')
commit('M001 done')
native('ledger status')
native('ledger check')
native('ledger close M001')
native('ledger close M001 --commit', expected=2)
```

S01 r1 is green despite missing quoted-newline coverage; simulated review catches it.
Receipts show tree_dirty_before:true/tree_clean_after:false: checks left uncommitted
work stable; later acceptance housekeeping grants no unattributed dirty baseline.

Open 05_governance/reviews/m001/M001-S01_r1_verification.json's manifest.path.
Its changed entry for 07_app/input.csv has sha equal to printed normalized evidence
and review prompt 002's normalized evidence SHA-256: staged raw (13 LF bytes),
not input raw (16 CRLF bytes). CLI preservation covers worktree bytes; Git converts.
Choose the [scoped -text/raw-manifest policy](project_checks.md#raw-evidence-and-current-output)
before an exact-byte baseline; do not apply it here or rewrite bound evidence.
M001-S01_r2_review.md binds the r1 report's source, normalized hash and
M001-S01-R1-F1 to closed_by_review; omission does not close findings.

Close writes nothing; explicit done edit/render changes roadmap.yaml/docs/roadmap.md.
M002-S01 stays unstarted: M002 is planned. next:none means no eligible task, not
no future work. This example has no run history; granted histories remain subject
to Q001's [Exact grant grammar](autonomy.md#exact-grant-grammar).
Keep [holistic closure](operating.md#steering-and-closure),
[blocker/resolve and commit recovery](upgrading.md#exceptional-commands) and
[pair qualification](upgrading.md#compatibility) separate. No frutlups/profile is
needed; qualified frutlups uses these artifacts without conversion.
