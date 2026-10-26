# frutlups

frutlups runs coding, verification and review workflows for projects created from
the agentic project template. You choose the models and the work they may perform;
frutlups records progress and resumes from the project's durable history.

It is optional: the template's scripts also support a fully manual workflow.

## Get started

1. Use the qualified **template 5.0.0 export** and **frutlups 0.4.2**.
2. Download the wheel from [GitHub Releases](https://github.com/jequihua/frutlups/releases/tag/v0.4.2).
   Follow the [installation guide](docs/release.md#install-the-frozen-wheel) to
   verify its checksum and install it into a separate virtual environment.
3. Configure your project and record an owner grant using the template's scripts.
   See [project setup](docs/release.md#configure-a-project).
4. From the project root, use the installed `frutlups` command:

   ```text
   frutlups status
   frutlups preflight
   frutlups run --until slice
   frutlups status --usage
   ```

**Preflight can call models.** When work is admitted, it probes the selected seats;
each probe consumes a job from the owner grant and may consume provider quota or
incur charges. `status` is read-only and makes no provider calls.

## Requirements

The qualified setup is **Windows, 64-bit CPython 3.14.3, PyYAML 6.0.3 and Git for
Windows**, with Pi and Claude Code installed and authenticated separately.

The package declares Python 3.11 or newer, but only the setup above was tested.
Python 3.11 has a known incompatibility. Other platforms and Python versions are
not currently qualified. See [support and limitations](docs/release.md#support-and-limitations).

## Project configuration

- `frutlups.toml` is committed: model seats, effort levels, run limits and evidence paths.
- `frutlups.local.toml` is ignored: local executable paths and environment forwarding.
- `project.local.toml` optionally selects the project's verification interpreter.

Start from the template's examples and keep credentials out of these files.
[Setup instructions](docs/release.md#configure-a-project) explain the required
format markers and owner grant. [Models and delegation](docs/adapters.md) covers
Pi, Claude Code, effort settings and the configurable Pi record limit.

## Running and resuming

`run` works within the current owner grant and stops at its authorized boundary.
Run it again to continue eligible work. An unfinished attempt requires inspection;
frutlups will not silently repeat it.

Version 0.4.2 fixes continuation after a legitimate manual blocker: once an
authorized human or architect resolves it, work can resume without a runner
dispatch record for a round that was entirely manual. Existing runner history
still requires its matching policy. See the [0.4.2 evidence](docs/release_evidence_0_4_2.md)
for qualification and the disclosed operator handoff during holistic completion.

A `STOP` file at the project root prevents new admission and recovery effects.
It is not an emergency interrupt for a model call already in progress.
See [operating rules](docs/release.md#operating-rules) for recovery and stop handling.

## Documentation

- [Installation, setup and operation](docs/release.md)
- [Models, delegation and limits](docs/adapters.md)
- [Usage and cost reporting](docs/usage.md)
- [Documentation index](docs/README.md), including advanced reference and release evidence

The published wheel is the qualified artifact. Installing from source builds a
new artifact and is intended for development. Later documentation updates do not
replace the v0.4.2 wheel or release tag.
