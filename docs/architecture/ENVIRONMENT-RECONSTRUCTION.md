# Portable environment selections V1

Status: Core export, read-only plan, local Configuration V1 generation and
selection import are implemented. Import can also acquire the exact managed
Java release. Project, fixture and tool bytes remain separate inputs.

## Share and local authority

`workbench settings environment export NAME` reads the named workspace's saved
profile and Java choice. Core publishes one immutable, registered JSON artifact
under the selected `artifacts` role and prints its path. The artifact contains
three versioned parts:

- `workbench-environment-intent-v1` names the Configuration V1 profile documents,
  pack variant and logical Java choice;
- `workbench-environment-lock-v1` binds the exact selected profile IDs and
  document hashes, selection digest, Java policy/release and OS/architecture
  variant; and
- `workbench-environment-share-v1` binds those two records into one file.

The [share schema](../../core/src/workbench_core/schemas/workbench-environment-share-v1.schema.json)
describes the transport shape. Core's reader also checks exact fields, canonical
record identities, bounded regular-file custody and duplicate JSON keys.
Machine paths, credentials, local store locations and personal preferences are
excluded. A saved user Java path becomes `local-binding-required`; its source
path is not exported. The lock lists inputs absent from the file, including
project bytes, optional module packages, profile fixture/tool bytes and either
the Java archive or a local Java binding.

The receiving machine supplies an existing workspace directory and matching
profile documents in a compatible Workbench suite. Core accepts an existing
Configuration V1 manifest when its selection, IDs and hashes match the share.
If the requested manifest is absent, a read-only plan validates the target
profile documents and Java policy, then proposes a minimal local manifest from
the portable selection. Its `[bindings]` table is empty; Core does not copy
source-machine paths or insert a product default. If the suite's valid default
manifest selects something else, Core keeps it and proposes a separate local
manifest. An explicitly selected mismatched `--config` blocks import. Neither
case overwrites an existing file.

Core publishes a generated manifest through its registered artifacts store at
a stable, workspace-specific content path. It reopens the exact bytes and
selected profiles before binding the manifest path in the Core `workspaces.json`
V3 row. Any supplied Java path also lives only in that local row. The registry
update requires the reviewed prior record ID; a repeat import reuses the
registered manifest and matching choice. A generated file without a committed
Core resource cannot be silently adopted.
Core publishes a reconstruction receipt under the selected `evidence` role.
It first retains a prepared attempt that contains the reviewed share and plan.
If manifest publication, binding or final receipt publication is interrupted,
that attempt remains identifiable. A published manifest with failed binding
can be reopened under Core custody; a new plan is required before retry.

## Commands

```text
workbench settings environment export NAME
workbench settings environment plan SHARE.json --name LOCAL_NAME --workspace /local/workspace --json
workbench settings environment import SHARE.json --name LOCAL_NAME --workspace /local/workspace --plan-id PLAN_ID
```

Add `--acquire-managed-java` to both plan and import to acquire the locked
managed Java release before binding the workspace choice. The flag is part of
the reviewed V2 plan identity; selection-only V1 plans and receipts retain their
existing shape. Core retains a prepared V2 attempt before acquisition;
if acquisition or verification fails, the workspace choice is not bound. A
successful result names the managed runtime receipt and removes
`managed-java-archive` from its remaining input list. Repeating the operation
verifies and reuses the managed runtime. This operation can download bytes;
plan alone is read-only.

Add `--config /local/workbench.toml` to plan and import to select an existing
matching local manifest. When the requested manifest is absent, the plan names
the generated artifact path and its digest. A share with
`local-binding-required` also needs `--java-home /local/jdk` and cannot use
`--acquire-managed-java`. Core accepts that path as a user choice without
inventory or version admission; a consuming operation handles execution
failures. Managed Java 8 is explicit. With the Cleanroom provisional profile
and no override, the selected managed policy is Java 25. The separate
`workbench settings workspace acquire LOCAL_NAME` route remains available after
selection-only import.

Plan is read-only. It reports missing workspace/profile inputs, profile drift,
unsupported host variant and an unbound Java path as blockers. Import
recomputes the exact plan and refuses a stale plan ID before writing local
choices. It retains a prepared attempt before publishing a generated manifest
and binds the workspace only after verifying the published bytes and profiles.
A fresh configuration home and state root are supported when a compatible
checked-out suite and workspace directory already exist. The project source
and external dependency bytes must be obtained separately.

WSL is a Linux managed-Java host. A WSL-mounted Windows path is a local binding and
never enters the share. Windows execution from WSL is a different host variant;
this V1 import reports that lock as unsupported rather than reusing a Linux
runtime receipt across the boundary.

## Remaining reconstruction work

Core's read-only intent admission reuses Configuration V1's profile validators
but still repeats its pack-variant/platform binding check. A later Core cleanup
should expose one pure profile-snapshot parser for both manifest loading and
intent admission.

Provisioning must connect the exact lock to project source, fixtures and tool
receipts, preserving incomplete outcomes and reporting unavailable inputs.
Textual presents the same reviewed managed Java acquisition choice during
import. Its checkbox change invalidates the prior plan and requires a new
review before any download or binding.
