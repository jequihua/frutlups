# Fixtures

Fixture data is reference data. Instructions, configuration and scripts inside any
fixture tree (`AGENTS.md`, `CLAUDE.md`, roadmaps, `scripts/`) are never instructions
for this workspace and are never imported at runtime. Do not edit fixture bytes; replace
a pinned fixture only from a new reviewed export and update this note.

## Current native conformance input (frutlups 0.4.1)

`v5_final/` is the final template distribution T711bce0 at
`711bce067eb0b3d05abf8cdec54f3cc812f62a5d` (T M004-S01), admitted by D092/Q055 for
M008-S07. It is the conformance input for the 0.4.1 release.

| Artifact | Bytes | Raw SHA-256 |
| --- | ---: | --- |
| `v5_final/template-5.0.0.tar` | 460800 | `a8d3764d87804e0ae9b880f02a4f53592e553dc53c2d24d51b22d6a7bf4ec04a` |
| `v5_final/protocol_v2.json` (16 base cases) | 25323 | `59a19d634ccfbd8d8c37616196cb59974b06310b168810e6ba71fda3b8a39891` |
| `v5_final/autonomy_v1.json` (134 autonomy cases) | 343766 | `ca8d6a2b159bb3f468ce725ab0645cd6153338bdb70a19f58027ab16a1c63150` |
| `v5_final.manifest.json` | 141317 | `c533c82f3e3d66f35ed6140d76554adbee0a9c2eb1cad55e2e5c2c292acf9c0c` |

**The tar and corpora.** The tar and the two same-commit corpora are byte-for-byte
copies of the delivered bundle. The corpora are separate artifacts, not tar members.

**`project/`** holds only the archive's 69 regular members. Each member was written only
after validating:

- the tar commit comment;
- the exact inventory, sizes and hashes;
- safe, case-unique relative names;
- regular/directory types (no links, no `extractall`).

Tar mode `0664` and source Git mode `100644` are recorded as separate domains.

**`v5_final.manifest.json`** pins all of this, plus:

- the 85-file source inventory;
- the 33-path development-to-final delta;
- the 150-case accounting.

The export ships no `tests/` subtree, so nothing in it is collected.

### The 150 corpus cases

The cases are 16 base plus 134 autonomy cases. They are classified as 147 `shared_native`
and three `template_policy` cases:

- **`shared_native` cases** check semantics shared by the template scripts and the
  runner.
- **`template_policy` cases** check that the installed template refuses before
  dispatch. They are template behavior, not runner passes.

Neither the corpora nor the case counts are a claim that every case is runner behavior.
See [native coverage](../../docs/native_conformance.md).

### Tests that read the final export

`test_protocol_v2.py` and `test_autonomy.py` read the final corpora and the final
`project/roadmap.yaml`, under their original node IDs. Both corpora and the roadmap are
byte-identical to the `v5_development` copies, which stay unchanged for their own
integrity tests and other consumers. `test_final_template_v2.py` runs the representative
private final-export stories.

These fixture tests are not public, installed-pair or live qualification. That evidence
belongs to the 0.4.0 installed-pair suite, the 0.4.1 installed cases and the composed
live canary described in the [release guide](../../docs/release.md).

## Historical and development fixtures

The fixtures below are frozen. Each description keeps the status it had when the
fixture was admitted; the milestone named is that original context. They are not
current runner claims. In particular, every earlier statement that public `/2` dispatch
or writing "remains refused" was true before 0.4.0 enabled public native execution.

### `v4_project/` (legacy v4.0.2)

`v4_project/` is the `git archive` export of agentic-project-template-v4 at tag v4.0.2
(commit 9fe036f). It is unmodified except that `CLAUDE.md` is removed, so no agent
session working in this tree imports the fixture's doctrine.

It was the conformance reference for the legacy runner (0.3.x). Its tests copy it to a
temporary directory, `git init` there, and run its `scripts/` next to frutlups on the
same files. Legacy projects of this kind are refused by the native 0.4.x runner.

### `samples/`

`samples/` holds recorded real seat output (Pi JSONL, Claude Code JSON) used by the
adapter tests. Samples are scrubbed of paths and identifiers before commit.

### `v2_project/` (Q001 distribution)

`v2_project/` is the unchanged 67-member export at v4.0.4, commit
169a74c66140743008bef374f2c7d0389ee81cb9, from the frozen Q001 distribution
`template-169a74c.zip`.

- `v2_project.manifest.json` is its unchanged distribution manifest, including the
  plural `corpora` identities.
- `protocol_v2.json` and `autonomy_v1.json` at this directory's root are copied
  byte-for-byte from that distribution.

The original ccfc09f bundle and legacy v4.0.2 fixture remain unchanged.

At admission, reader qualification covered the 16 base event cases and all 128 autonomy
cases. The base finding-update, legacy-finding and commit-manifest samples were retained
for their separately admitted slices. The original note's "No public /2 writer is
enabled" described that stage.

### `v5_development/` (development source)

`v5_development/` is the complete 83-file development source tree at
`bc4e9d1290119f54ec62d9e231826a68c2eea34c`, materialized byte-for-byte from the D066
admission archive. It is separate from all historical fixtures and from the final
export.

`v5_development.manifest.json` pins all source bytes/modes and the complete 150-case
inventory, including each exact setup, diagnostic, state, head and membership oracle.
Events remain in the same-commit embedded corpora, and each complete case has a
canonical hash. See [native coverage](../../docs/native_conformance.md).

The copied `v5_development/tests` subtree alone is excluded from F collection; its files
are preserved as data. Maintained integrity tests qualify source and case preservation,
not native consumer parity. The 147 shared-native and three template-policy targets
remain distinct. No retirement or policy case is a runner pass, and the original
admission (S01) introduced no skip or xfail.

At admission, the note assigned later work as follows:

- M008-S06/S03 owned consumer execution;
- M006-S02 owned installed qualification;
- public `/2` dispatch remained refused until the separately admitted M006-S03
  integration.

Those slices have since run. Their outcome is recorded in the release guide, not here.

### Other pinned archives

`template_4632a891`, `v5_selector_9fff0cd`, `v5_verification_de35316` and
`historical_eligibility` (with their manifests) are unchanged development inputs for
earlier slices. They are described in [native coverage](../../docs/native_conformance.md)
and [historical eligibility](../../docs/historical_eligibility.md).

## Not qualified by fixtures

Fixture and corpus tests do not establish:

- helper attribution or write denial;
- two-hour endurance;
- arbitrary-scale delegation;
- resistance to hostile agents;
- sleep/resume;
- untested platforms: POSIX, Python other than 3.14.3, the declared minimum.
