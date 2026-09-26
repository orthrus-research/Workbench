# Portable environment selections V1

Status: Core export, read-only plan and local selection import are implemented.
This is the first reconstruction slice. It binds a compatible Workbench suite
and an existing local workspace; it does not acquire project, fixture, tool or
Java bytes.

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

The receiving machine supplies an existing workspace directory and a local
Configuration V1 manifest in a compatible Workbench suite. Core reloads that
manifest and the selected profile documents. Their exact selection, IDs and
hashes must match the share. The local manifest path and any supplied Java path
live only in the Core `workspaces.json` V3 row. The import updates that row under
the existing lock with a reviewed prior record ID; a repeat import reuses it.
Core publishes a reconstruction receipt under the selected `evidence` role.
It first retains a prepared attempt that contains the reviewed share and plan.
If binding or final receipt publication is interrupted, that attempt remains
identifiable and a repeated import can recheck or reuse the local binding.

## Commands

```text
workbench settings environment export NAME
workbench settings environment plan SHARE.json --name LOCAL_NAME --workspace /local/workspace --json
workbench settings environment import SHARE.json --name LOCAL_NAME --workspace /local/workspace --plan-id PLAN_ID
```

Add `--config /local/workbench.toml` to plan and import when the target suite's
default manifest is not the matching local manifest. A share with
`local-binding-required` also needs `--java-home /local/jdk`. Core accepts that
path as a user choice without inventory or version admission; a consuming
operation handles execution failures. Managed Java 8 is explicit. With the
Cleanroom provisional profile and no override, the selected managed policy is
Java 25. Use `workbench settings workspace acquire LOCAL_NAME` after import to
acquire the saved managed choice.

Plan is read-only. It reports missing workspace/configuration inputs, profile
drift, unsupported host variant and an unbound Java path as blockers. Import
recomputes the exact plan and refuses a stale plan ID before writing local
choices. A fresh configuration home and state root are supported when a
compatible checked-out suite and workspace directory already exist. The
project source and external dependency bytes must be obtained separately.

WSL is a Linux managed-Java host. A `/mnt/c/...` path is a local binding and
never enters the share. Windows execution from WSL is a different host variant;
this V1 import reports that lock as unsupported rather than reusing a Linux
runtime receipt across the boundary.

## Remaining reconstruction work

The next slice can generate a local Configuration V1 manifest from a validated
intent when the target suite lacks a matching one. It must then keep that
manifest and the workspace registry reference together under Core custody.
Provisioning must connect the exact lock to managed Java, project source,
fixtures and tool receipts, preserving incomplete outcomes and reporting
unavailable inputs. Textual will present this Core plan and import operation;
it will not implement a separate environment store.
