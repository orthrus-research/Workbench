# User context and local storage

Core resolves a developer's physical workspace, configuration home, runtime
state, and location roles for each command. The current read-only interface is
`workbench environment resolve --json`, described by the
[Environment Resolution V1 schema](../../core/src/workbench_core/schemas/workbench-environment-resolution-v1.schema.json).
The plain command displays the same choices with their sources. An optional
workspace path selects a target for that resolution. The corresponding Python
API is `workbench_core.environment_resolution.resolve_environment()`.

Workbench source and installed packages are versioned inputs. A developer's
project, configuration, runtime state, downloads, captures, and evidence are
separate local data. Fresh user configuration defaults to `~/.workbench/` on
Linux and macOS and `%USERPROFILE%\.workbench\` on Windows.
`WORKBENCH_CONFIG_HOME` selects another absolute configuration home.
`WORKBENCH_STATE_ROOT` selects the operation state root. Core does not create
these directories merely to resolve their paths.

## Selection and authority

An explicit command workspace wins, followed by a named default in
`workspaces.json`, the saved Setup V1 workspace, `WORKBENCH_WORKSPACE`, and the
current directory. For state, `WORKBENCH_STATE_ROOT` wins over saved Setup V1
state, followed by the platform default. Core supplies the resulting workspace,
state root, and location roles to module handlers through `ExecutionContext`.
The [user preferences contract](USER-PREFERENCES.md) defines each role and its
fallback.

A saved `profile_configuration_reference` remains a path for review, not an
activated pack or platform. An operation requiring profile authority must
select the intended profile and validate its installed provider, document
identity, and applicable digest through the profile API. The repository's
`workbench.toml` is a versioned suite manifest, not a user's default pack
selection.

## Retained data and owner adapters

Core dispatch uses an external runtime state root for source and installed
execution. Older source-only owners may still use checkout `.workbench/` unless
setup selects a state root. Existing data remains at its original path; changing
preferences does not migrate or delete it. Core's storage manager keeps its
established `.workbench/` child beneath the selected external state root.

| Owner | Remaining boundary |
| --- | --- |
| Shell and older product lanes | Replace direct checkout `.workbench/` calculations with the Core-supplied state location. Keep readers for retained evidence at its original path until an identity-preserving migration exists. |
| Core native checks and live console | Remove direct checkout-state construction after their workspace and session custody contracts are adapted. |
| Atlas and configuration consumers | Atlas's source and knowledge defaults use Core's selected user state. An exact historical V3 lock copy is packaged with Atlas's fixed catalog, while the current Supersymmetry lock resolves through the selected profile resource. Checkout-local records remain in place and require explicit paths. Configuration V1 still confines profile documents to suite-relative `profiles/` paths. |
| Workspace selection | Named targets and a default are available through user settings. Per-target profile and tool selections need a separate reviewed contract; explicit command targets continue to win. |

These owners must migrate before source checkouts are free of mutable user data.
Atlas has not relocated retained files or rebound their original identities;
explicit old paths continue to read them.
