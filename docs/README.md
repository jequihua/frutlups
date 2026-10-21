# Documentation

## Start here

- [Install, configure and run](release.md): the published 0.4.1 release.
- [Models, delegation and limits](adapters.md): Pi and Claude Code settings.
- [Usage reporting](usage.md): known totals, missing metrics and cost limitations.

## Advanced reference

- [Execution mechanics](public_execution.md): entry points, admission order and
  recovery. Its acceptance maps and qualification statements record the original
  implementation stage; the release guide gives current qualification status.
- [Historical deadline assessments](historical_eligibility.md): an explicit,
  owner-adopted exception for particular historical evidence, not ordinary setup
  or a general migration mechanism.
- [Release evidence](release_evidence.md): exact artifact identities, installed and
  live results, and qualification limits.
- [Release history](changelog.md): prior releases, separated from current instructions.

Configuration format identifiers are independent of package versions. Keep the
literal values in configuration examples unchanged. Internal roadmap, receipt and
ledger format names belong in reference material, not the getting-started steps.

## Historical engineering records

These files preserve evidence and contracts from the stage named in each document.
Statements such as "public execution remains refused", "not accepted" or "pending"
are historical, not the current release state. Some records describe features later
changed or withdrawn. Consult the current guides above before following any command
from a record. The records are retained at their original paths to preserve links
and evidence identities.

| Record | Subject |
| --- | --- |
| [Adapter compatibility](adapter_compatibility.md) | Adapter implementation and offline handler checks |
| [Offline baseline](baseline.md) | Initial incident characterization |
| [Checker contract](checker_contract.md) | Checker identity and recovery admission |
| [Development checks](development_checks.md) | Verification timings and optimization experiments |
| [Idle contract](idle_contract.md) | Idle and completion cases |
| [Native conformance](native_conformance.md) | Format transition and source/test inventory |
| [Installed pair qualification](paired_qualification.md) | The frozen 0.4.0 offline matrix |
| [Earlier live qualification](qualification.md) | The 0.3.0 canary |
| [Release candidate](release_candidate.md) | Dated 0.4.0 preparation record |
| [Selection contract](selector_contract.md) | Task selection and runtime context |
| [Fixture notes](../tests/fixtures/README.md) | Current and historical reference inputs |

Old template fixtures remain test data, not alternate supported setup options.
Their presence does not enable legacy project execution in the current runner.
