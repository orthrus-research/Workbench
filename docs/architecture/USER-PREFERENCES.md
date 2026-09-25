# User preferences and resolved locations

Status: User Settings V1, named workspaces, Core resolution and command dispatch
are implemented. Core now captures text from dispatched module commands beneath
the resolved `logs` root. The Cleanroom generic dev-fixture adapter uses the resolved
`fixture_instances` root for new plans, and Shell's Blueprint staging uses
`blueprint_sessions` through Core command dispatch. Other module-specific
placement adapters are adopted separately; an operation must not claim to honor
a location role until its adapter consumes it.

## Stable configuration home

Fresh Workbench configuration uses `~/.workbench/` on Linux and macOS and
`%USERPROFILE%\.workbench\` on Windows. `WORKBENCH_CONFIG_HOME` selects a
different absolute configuration home. The directory and the filenames
`settings.json` and `workspaces.json` do not contain a Workbench version. Their
records carry a schema version and an identity derived from their choices.

`settings.json` contains only user-selected location overrides. An absent file
or absent role uses the current documented fallback; a preference is never
filled with a copied default merely because Workbench was upgraded. Supported
expressions are absolute paths, `~/path` relative to the current user home, and
`./path` relative to the configuration home. Core resolves and checks them
without creating their destinations. A newer unsupported schema fails clearly
before an older Workbench can overwrite it.

`workspaces.json` holds named project paths and an optional default. An explicit
command target wins; the named default follows; the retained Setup V1 workspace
then remains the fallback. The workspace registry contains no pack or platform
declarations. User settings are physical choices, not profile authority.

The configuration folder is distinct from a historical checkout-local
`<project>/.workbench/` state directory. Core does not migrate retained runtime
evidence when settings change.

## Location roles

| Role | Fallback when unset | Intended owner |
| --- | --- | --- |
| `workspace_parent` | `~/Workspaces` | Parent for future workspace creation. |
| `artifacts` | `<state_root>/artifacts` | Verified retained artifacts. |
| `logs` | User state `workbench/logs` | Module-grouped operational logs. |
| `blueprint_library` | `<config_home>/library/blueprints` | User-owned reusable definitions. |
| `blueprint_sessions` | `<state_root>/blueprints` | Materialized sessions and review work. |
| `fixture_library` | `<config_home>/library/fixtures` | User-owned immutable fixture definitions. |
| `fixture_instances` | `<state_root>/fixture-instances` | Prepared working copies. |
| `cache` | `<state_root>/cache` | Disposable tool caches. |
| `evidence` | `<state_root>/evidence` | Retained operation evidence. |

These roles name destinations, not permissions to use unverified inputs. A
platform or pack profile retains exact fixture identities, locks, compatibility
rules and runtime policy. Core resolves placement and owns materialization and
cleanup; a domain adapter translates role paths into its tool arguments.

## API and commands

`workbench environment resolve` displays the current effective workspace,
state root, every location role and the source of each choice. It is read-only
and works without `--json`; the optional flag supplies a versioned machine
record. An optional workspace path, including `.`, selects a target for this
resolution. Core's `resolve_environment()` is the corresponding Python API. Normal
module dispatch passes resolved roles through `ExecutionContext.locations`;
adapters opt into roles with `context.location(role)`.

## Module output routing

Core appends dispatched module events to one UTC-dated JSON Lines file per
active module:

```text
<logs>/modules/<module-id>/<YYYY-MM-DD>.jsonl
```

Capability and run IDs are fields in each event, never directory levels. The
`workbench-module-log-event-v1` schema has a small common envelope: timestamp,
module ID, capability ID, run ID, and event kind. A `started` event names the
workspace; `python_text` events name stdout or stderr and contain text; a
`finished` event records outcome, exit code, error, omitted byte counts, and
any requested outputs. Concurrent Workbench writers serialize appends. Text
continues to reach the terminal. Python text captured in the current execution
context is limited to 16 MiB per stream and omitted bytes are counted. This is
an operational log, not an immutable evidence receipt or a complete process
capture.

The default log root is independent of `WORKBENCH_STATE_ROOT`. Setting an
operation state root does not create that root merely to retain command text.
`workbench settings set logs PATH` changes the shared log root for future
invocations. The fallback is `$XDG_STATE_HOME/workbench/logs` on Linux,
`%LOCALAPPDATA%/Workbench/logs` on Windows, and
`~/Library/Application Support/Workbench/logs` on macOS.

A module asks `ExecutionContext.output_path(role, filename)` for a fresh file
destination. Core places it at
`<role-root>/outputs/<role>/<module-id>/<run-id>-<filename>`. The role segment
keeps destinations distinct when a user selects the same physical root for
multiple roles. This supports `logs`, `artifacts`, `evidence`, `cache`,
`blueprint_sessions`, and `fixture_instances`; a module declares the role and
filename rather than a physical root. Reusable libraries and durable sessions
use their resolved roots and owner-specific layouts instead of this file
allocation convention. Existing owner plans and receipts remain readable at
their original locations.

This routing captures current-context Python command text. Binary writes,
other threads without the execution context, subprocess file logs,
Minecraft runtime logs, standalone validation logs, and other owner-managed
outputs still need their owners to request or receive a routed destination.
Adapters must bind any execution-relevant destination into their plan identity
and preserve the original source path in retained evidence.

`workbench settings` shows durable choices. `workbench settings set ROLE PATH`
and `workbench settings unset ROLE` change only one location. The command
`workbench settings workspace add NAME PATH --default` registers a named default
project. The settings writer uses a lock, checks a prior identity when supplied,
and publishes a complete record atomically.

## Earlier setup records

Existing Setup V1 and fixture or launcher records are read from their former OS
configuration directory until imported. Fresh setup uses the stable home.
`workbench settings migrate --dry-run` previews known files. The command
`workbench settings migrate` copies validated bytes into the stable home without
deleting the old files or replacing a conflicting destination. Retained
operation receipts keep their original location and meaning. Future schema
migrations must read all supported prior versions, preserve a recoverable original, and write the stable
filename transactionally; an older binary may not discard newer fields.

## Next owner adapters

Cleanroom's current V2 construction owner still reads its exact fixture from the
platform profile. A new construction contract should let Core publish a verified
fixture definition under `fixture_library`, prepare a separate working copy under
`fixture_instances`, and pass both paths with the profile lock identity to the
owner. The owner must recheck the bytes before planning and applying. The V2
fixture lock and retained plans remain valid at their original paths.

Artifact, log, cache, and evidence producers should adopt their resolved role
paths one owner at a time. Each adapter must keep historical receipt paths
readable and bind the selected location into new plan or result identities where
that location affects execution.
