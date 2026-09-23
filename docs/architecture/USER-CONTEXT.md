# User context and local storage

Status: Physical Context V1 is implemented for Core command dispatch. Existing
Setup V1 records and profile APIs remain their respective authorities.

Workbench source and installed packages are versioned, read-only inputs to an
operation. A developer's selected project, configuration, runtime state,
downloads, captures, and evidence are separate local data. The platform user
configuration and state directories are the defaults on Linux and Windows;
`WORKBENCH_CONFIG_HOME` and `WORKBENCH_STATE_ROOT` are explicit location
overrides. Core command dispatch now resolves an external runtime-state root
for both source and installed execution. Legacy source-only owners may still
use checkout `.workbench/` unless setup selects a state root. Existing data is
retained in place; this change does not migrate or delete it.

## Physical Context V1

`workbench environment paths --json` reports the derived, read-only context.
The exact transport shape is the
[Physical Context V1 schema](../../core/src/workbench_core/schemas/workbench-physical-context-v1.schema.json):

```json
{
  "format": "workbench-physical-context-v1",
  "schema_version": 1,
  "configuration_home": "/absolute/user/configuration/directory",
  "workspace": "/absolute/selected/project",
  "state_root": "/absolute/user/runtime-state/directory",
  "profile_configuration_reference": null
}
```

The four location fields are physical paths, not profile definitions. An
explicit command workspace wins, then the saved Setup V1 workspace, then the
process workspace, then the current directory. For state, an explicit
`WORKBENCH_STATE_ROOT` wins over saved Setup V1 state, followed by the platform
default. The context is derived for each command and is not another persisted
configuration file. Core passes its workspace and state root to module handlers
through `ExecutionContext`. Core's storage manager keeps its established
`.workbench/` child beneath the selected external state root.

`profile_configuration_reference` is the path saved by Setup V1 or `null`. It
is a reference for review, not an activated pack or platform. An operation
requiring profile authority must select the intended profile and validate its
installed provider, document identity, and applicable digest through the
profile API. The repository's `workbench.toml` is a versioned suite manifest,
not a user's default pack selection.

## Boundaries still to complete

Setup V1 stores one default workspace rather than a registry of named projects.
The remaining program changes are owner-specific:

| Owner | Remaining boundary |
| --- | --- |
| Shell and older product lanes | Replace `default_suite_state_root` and direct checkout `.workbench/` calculations with the Core-supplied state location. Keep readers for retained evidence at its original path until an identity-preserving migration exists. |
| Core native checks and live console | Remove direct checkout-state construction after their workspace and session custody contracts are adapted. |
| Atlas and configuration consumers | Atlas's source and knowledge defaults now use Core's selected user state. An exact historical V3 lock copy is packaged with Atlas's fixed catalog, while the current Supersymmetry lock resolves through the selected profile resource. Checkout-local records remain in place and require explicit paths. Configuration V1 still confines profile documents to suite-relative `profiles/` paths. |
| Workspace selection | Add a separately reviewed, per-user registry of named project targets and per-target selections. Setup V1 remains one default, and explicit command targets continue to work. |

The remaining owners must migrate before source checkouts are free of mutable
user data. Atlas has not relocated retained files or rebound their original
identities; explicit old paths continue to read them.
