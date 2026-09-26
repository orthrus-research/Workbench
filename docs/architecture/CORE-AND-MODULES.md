# Core and module separation

Status: implemented source ownership and native package architecture. See
[native packages](NATIVE-PACKAGES.md) for installation, lifecycle limitations,
and the qualification required before a public release.

[Native-tool capture](NATIVE-TOOL-CAPTURE.md) describes Core-owned protocol files,
verified reads and their integration with retained-check storage.

Workbench Core owns the environment in which modules run: provisioning,
installation, configuration, process and service coordination, resource
inventory, recovery, and cleanup. Modules supply domain capabilities through
explicit interfaces. A working Core installation must not require Atlas,
Blueprints, Crucible, a particular game profile, or either IDE client.

## Implemented separation

Core owns the command router, service host, scheduling, package lifecycle,
runtime provisioning and storage management. API-only consumers do not import
Core. Product modules and profiles are independently installable distributions;
the root project is a non-distributable workspace marker. No portable Core
distribution or central release-version authority remains.

Shell is an optional composition module. Domain event classification belongs
to Cleanroom; Supersymmetry owns its experiment catalog, examples and console
policy. Profile extensions load only from admitted installed distributions.
Disabling a required profile or module removes the dependent capabilities from
dispatch without disabling Core's setup and cleanup commands.

## Ownership

| Owner | Responsibilities |
| --- | --- |
| Core | Host discovery and repair; toolchain and environment provisioning; package installation and removal; workspace configuration; module discovery; process/service lifecycle; cancellation and recovery; resource inventory and cleanup. |
| Core API | Small, stable contracts for capability registration, execution context, events, cancellation, declared resources, and operation receipts. No domain algorithms or profile defaults. |
| Product modules | Knowledge queries, source analysis, construction, experiments, comparisons, and domain-specific result semantics. |
| Platform and pack profiles | Exact dependencies, policies, adapters, observations, templates, and default values for an explicitly selected target. |
| Clients | Terminal or IDE interaction over registered capabilities; presentation and user consent. |

Core owns the generic machinery to launch and clean up a process or resource.
Crucible owns experiment execution semantics, observation records, and evidence
retention requirements. A module declares resource ownership and retention
constraints; Core checks those constraints before cleanup. Moving the current
runtime manager must preserve its protection of active resources, external
worlds, unknown paths, referenced evidence, and recoverable trash.

Generic acquisition belongs in Core. Cleanroom or pack-specific selection and
materialization policy belongs in the selected profile adapter. Core must not
silently choose Supersymmetry when no profile is selected.

Packwiz V2 materialization supplies the exact Packwiz and Installer commands,
timeouts and historical log paths through the Core process API. Core supervises
each child, bounds and publishes its merged log in private storage, preserves a
previous log under its historical history name, and refuses an unpublished
pending log. The module validates the refreshed pack, payload and V2 receipt;
Core performs the Linux no-replace move of its prepared result directory.
Packwiz source scratch and its disposal remain a separate migration boundary.

Recipe capture reads and registers per-user runtime/JDK locations through the
Core-bound fixture-selection API. The selected profile still validates their
bytes before planning. An exact command override applies to that operation
without replacing the user's saved selection.

## First durable resource slice

`ExecutionContext.publish_bytes` delegates immutable file publication to a
Core-bound resource service. The host binds its workspace, resolved location
roots and policy identity to the admitted module before the handler runs.
Process Studio's optional comparison report uses this port; its input and
comparison rules remain with Process Studio. Core creates the output without
replacing an existing file, retains an intent before publication, commits a
resource reference and verifies bytes when reopening it. Interrupted
publication can be reconciled when the prepared and published file still
share the recorded identity.

Core keeps the custody catalog in the stable user configuration home.
`workbench storage resources list`, `inspect` and `reconcile` expose registered
files across current and prior selected roots. The existing workspace storage
inventory keeps its V1 format; its cleanup plans protect any workspace item
containing a registered resource. Registered resources default to protected
until a later, reviewed retention policy supplies collection rules. This slice
supports immutable file output in the `evidence` and `artifacts` roles. The
publisher currently requires POSIX descriptor-relative file operations and
refuses unsupported hosts. The older `output_path` allocator remains for
owners awaiting migration.

The catalog root has a sealed identity marker and rejects unknown or unsafe
file records during inventory. Its present marker cannot prove that an earlier
configuration home or registered output was never lost. Workspace cleanup
therefore remains protected while historical catalog coverage is unproven;
reconciling an interrupted root publication does not establish that coverage.

For a newly created catalog, a Core host can opt in before first use to a V2
root epoch and write-ahead file issuance. An opt-in requested after V1 catalog
birth is refused without publishing a file. Older Core binaries that only read
V1 root markers cannot open an opt-in V2 root in the same configuration home;
the default CLI and module dispatch do not elect V2. Core records each selected file's
workspace, owner, target and reservation in matching, ordered records under
the configuration home and workspace before publishing it. The read-only
`ResourceCatalog.post_birth_coverage` verifier can report
`post-birth-covered` for one exact committed file while its original inode and
bytes survive. Default and historical V1 roots, files without an issue record,
and files from another configuration home remain unproven. A detectable gap
or interrupted issuance blocks further opt-in issuance pending review.
This candidate proof does not change the catalog's `ready-unproven` state or
workspace cleanup protection. Opt-in witness storage requires an owner-private
workspace location; WSL Windows-mounted workspaces have not been qualified.

Before the first opt-in issue row, Core also seals an epoch anchor in the
workspace that names the configuration home and catalog root. A shared
workspace lease prevents this V2 issuer from starting another epoch beside a
retained anchor or issue ledger.
`ResourceCatalog.inspect_workspace_issuance_epoch` can report that surviving
evidence after the configuration home is lost; a foreign epoch
or incomplete paired ledger blocks further opt-in issuance. Earlier V2 issue
ledgers without an anchor gain one only after their exact rows and reservations
reopen under the selected root. Older V2 writers that do not check the anchor
remain outside this protection.

`ResourceCatalog.inspect_current_file_issuance` compares a selected
workspace's visible file resource rows with its retained V2 issue rows. It
reports publication state and resources with no matching retained issue.
Ordinary file writers do not share the issue lease, so this diagnostic does not
certify a complete current snapshot or any missing history. Its result grants
no cleanup authority; the catalog root remains `ready-unproven`.

Core's managed-tree API can locate one retained tree by its exact target path,
workspace, owner and role, returning the catalog tree ID and publication state.
An optional domain identity must match a prepared intent. Missing, duplicate,
foreign or changed records fail closed; the path is a lookup key, not authority
to adopt an uncataloged tree. Recovery still uses Core's ID-based reconcile.
Feature Change V1 context publication uses this lookup during direct Shell and
Core-dispatched Work Session actions. Other direct Shell tree writers remain
separate migration candidates.

## Private records and workspace choices

Core's filesystem host also exposes bounded reads, immutable publication,
revisioned replacement and a private advisory lease through the Core API.
Work Session V2 uses these operations for its header, event journal and
rebuildable summary. Feature Change uses them for selected-context history and
its current pointer. Their domain formats, event sequencing, recovery rules
and historical paths remain with Shell. Core owns the file staging, privacy
check, no-clobber publication, compare-and-swap and directory flush. Generic
bounded reads remain available for user-supplied inputs that are not
Core-owned private records.

Active-instance selections use the same Core filesystem port for bounded reads
and locked preference updates. Core registers the selected namespace during
module dispatch while Shell still validates the pack/runtime identity and
retains the existing per-workspace selection URI. A prior ordinary-mode
selection is upgraded to owner-private custody on its next successful update;
hardlinked legacy files are refused.

Project qualification retains its reviewed plan and external binding path.
Its private binding reader and revisioned replacement use the Core filesystem
port while Shell keeps the profile and workspace validation. Core registers
the historical binding namespace against the explicitly reviewed qualification
target, which can differ from the dispatch workspace. Workspace Home adoption
uses the same target-aware registration. Direct invocations without a bound
record-store scope remain a compatibility route.

`workbench settings workspace list --json` reads the named workspace registry.
`workbench settings workspace select NAME --profile-config PATH --java-home PATH`
saves local profile and Java candidates for that workspace; `--clear-profile`
and `--clear-java` clear each choice. The operation accepts
`--expected-record-id` for a stale-writer check. Saving a choice upgrades the
registry from V1 to V2, gives each entry a stable workspace ID, and preserves
unselected entries. The Textual Workspace choices screen uses these Core
operations and Core's Java inventory. These paths are local bindings, not a
portable reconstruction manifest. Runtime owners must still check a candidate
against the selected profile's Java policy before execution.

Core resolves profile and Java candidates only from the selected named
workspace or a matching Setup workspace. A different workspace does not
inherit Setup's Java. Explicit process Java remains a candidate with its own
provenance. Earlier V1 records still open without changing their IDs or bytes.

Core stores retained client state-root choices per exact workspace in the
stable user configuration home. `workbench settings state-root resolve WORKSPACE
product-spine --json` returns the effective root and a policy ID without
creating the destination. `state-root select WORKSPACE product-spine PATH
--expected-policy-id ID` saves a reviewed choice; `clear` restores the default.
The same API has a separate `feature` role. A changed workspace identity,
redirected selected path, or stale policy ID is refused. An explicit
`WORKBENCH_STATE_ROOT` remains an invocation override. Owner commands still
accept their documented direct path overrides. Core-dispatched `feature plan`
resolves the workspace's saved `feature` choice and holds that policy through
plan retention; direct `--state-root` remains an explicit one-command override.
Project qualification can apply with
`--expected-state-root-policy-id ID`; Core rechecks that ID and the destination
under its selection lock through the owner write. An explicit `--state-root`
without a policy ID retains the existing one-command override behavior. If a
workspace is replaced and its saved root is bound to the old identity,
`settings show --json` exposes the selections
record ID; `state-root clear-stale WORKSPACE ROLE --expected-record-id ID`
removes only that stale role after review.

VS Code reads the `feature` policy for retained Feature records and runs. Its
earlier editor setting is a migration hint. A Feature run rechecks the policy
after user consent, then passes Core's selected path and policy ID to the owner.
For `feature run material-fluid-recipe`, Core holds the selection lock through
plan retention and runtime-attempt allocation. The attempt then keeps its
selected path while the long-running observation proceeds; a later selection
applies to future attempts. An invocation without a policy ID keeps the direct
`--state-root` override. Feature plan retention has the same owner guard;
compare, apply, rollback and recover still lack it, and run receipts do not
yet retain the Core policy ID. Catalog, presentation
and transaction inspection use the selected Feature path.

IntelliJ Community reads the same Core `feature` choice for Retained Records
and the reviewed material/fluid recipe run. The Records panel saves a reviewed
change through Core; earlier project properties remain migration hints. The
run submits the policy ID to the owner at attempt allocation. This client
path has source and focused Java test coverage; native IDE execution remains
a separate qualification step.

On Linux, private record directories must enforce owner-only access. On WSL,
the Linux filesystem meets that condition in the tested configuration. The
tested Windows-mounted 9p location did not preserve Unix mode bits and is
rejected for private record writes; WSL mounts configured with Unix metadata
may differ. Native Windows behavior requires its own execution validation.
During Core dispatch, Work Session and Feature Change selection stores ask the
Core API for a mutable namespace before publication. Core chooses their
historical physical layout and registers a stable store ID in the resource
catalog. `workbench storage resources list` and `inspect` expose these stores;
the workspace cleanup planner protects registered namespaces. Their retention
stays protected until a reviewed policy exists. Direct historical adapters
retain their original readers and paths for records created outside Core
dispatch.

Feature Change Work Session setup also asks Core for a fixed session-owner
child within the suite's registered context store. Core retains its allocation
identity, an owner-local marker, a lease, and the exact identity of the mutable
state root and immutable start result. A completed context keeps its V1 URIs
and bytes. Unknown existing owner paths, changed files, and interrupted setup
are retained for review; setup does not delete or adopt them on retry. V1
contexts without Core owner or tree markers remain readable after full
historical validation. If all independent Core witnesses are externally lost,
the current catalog cannot prove whether such a context was historical, so
automatic cleanup remains unavailable.

`ExecutionContext.selection` carries the selected workspace ID, profile
configuration, Java choice and source labels as an immutable operation
snapshot. The `runtime-java` command passes that selection to Core's managed
Java service. With no Java choice, Core uses the profile's pinned managed
release; the provisional Cleanroom profile currently pins Java 25. A user may
explicitly select Core's exact managed Java 8 release, then acquire it into the
resolved operation state root through `settings workspace acquire NAME` or
`runtime-java`. This is an acquisition choice, not a claim that a Cleanroom
operation supports Java 8. A supplied Java home is returned as an unverified
path without inventory, execution or profile compatibility checks. The
runtime materialization and launch operations probe only that selected path
when execution needs Java, then report failures there. Core dispatch passes
the same Java choice and resolved state root into those Shell operations.
Direct legacy Shell entry points retain their existing configuration and
state defaults until their consumers migrate.

## Protected source changes

Blueprints admits a consented direct-apply plan and validates its source and
resulting manifest. During Core dispatch, its physical source transaction uses
the Core API to stage exact file or symlink bytes, recheck each baseline before
replacement or deletion, and flush the changed directory. On failure, Core
restores only paths whose applied bytes still match; a later edit is preserved
for review. Core removes only its own staged files and empty directories it
created. Blueprints keeps its V1 history markers and domain receipts. The
external source tree remains outside Core's retained-resource cleanup scope.

Blueprints M2 and Cleanroom Fresh Project use the same Core source port for
file replacement, rollback and token-owned stage cleanup. Blueprints retains
the V2 attempted-operation journal and decides whether an interrupted plan
can finish or must restore exact applied bytes. Core can reopen those stages
after a process exit; it preserves a later source edit instead of replacing
it during recovery. Core holds the M2 transaction marker at its historical
path with V1 token bytes, and removes only the exact marker Blueprints admits
as stale during recovery. Core also reads the V2 attempt journal and prepared
receipt and removes their reviewed bytes once Blueprints decides the attempt
has finished. Existing Cleanroom plans remain bound to their
original sealed construction owner, while new plans use the current owner
record.
Core registers the selected Fresh Project V2 state root and handles physical
journal and receipt publication, reads, and guarded removal at the existing
paths. The original and retired Core construction owners reopen only for
recovery of their already sealed plans. Profile-local direct apply still needs
a Core-composed entry route for state custody.

## Source layout

Keep one monorepo for coordinated contract changes and conformance testing:

```text
core/
  pyproject.toml
  src/workbench_core/
  tests/
api/
  pyproject.toml
  src/workbench_api/
  schemas/
  tests/
modules/<module>/
  pyproject.toml
  src/<stable_package_name>/
  tests/
  contracts/                 when needed
  schemas/                   when needed
clients/
  vscode/
  intellij-community/
profiles/
  platforms/
  packs/
packaging/                   release policy and source-environment metadata
tests/conformance/           cross-package integration contracts
tools/                       contributor and release entry points
validation/
docs/
```

The API is a separately installable, lightweight package so a module can be
developed and tested without importing the Core host implementation. Avoid a
general shared-utilities package: domain utilities stay with their owner.
Create directories only when they contain an implementation or contract.

Dependencies should flow from Core and modules to the API. Core loads explicitly
installed module entry points and does not statically import domain modules.
Module-to-module dependencies must be declared and acyclic. Clients use the
host protocol. Profiles register target-specific adapters through the same
declared boundary, without introducing a default pack into Core.

## Package and version metadata

Each installable package has its own ID, semantic version, dependency constraints,
entry points and tests. Use its native package metadata as the
canonical source: Python project metadata for Core, API, and Python modules;
native client metadata for the IDE clients. Release assembly consumes or
validates those versions instead of requiring independent manual edits to
several authorities. The release view is derived directly from those manifests;
the root project has no distributable package version.

Capability metadata declares its ID, owner, supported API range, command/service
entry point, required capabilities, and resource requirements. Start with local,
explicit installation and standard package entry points; a marketplace, remote
registry, or custom dependency resolver is not required for the first baseline.

Core-only installation must be distinct from an optional distribution containing
the recommended modules and profiles. Each package owns its included resources;
do not maintain one hand-edited, suite-wide file list as module authority.
Compatibility records can continue to identify exact tested combinations
without requiring lockstep versions.

Internal source filenames and package names should be stable. Module versions
belong in package metadata; data and protocol evolution belongs in explicit
format/schema metadata. Retain a versioned schema identity only where it is
externally meaningful. Do not rewrite identity-bearing evidence as part of a
filename migration. Artifact filenames and release tags remain versioned.

## Acceptance gates

1. Check module declarations, dependency direction, native metadata and owned
   resources. Keep source names stable; change schema identities only when the
   external contract changes. Do not rewrite historical evidence.
2. Exercise a fresh installation with no modules or pack profile, then install,
   discover, run, update, disable, and remove a sample module. Verify that Core
   setup, repair, service shutdown, and cleanup still work when that module is
   absent, incompatible, or fails during loading. Verify cancellation and crash
   recovery preserve existing cleanup and evidence protections.
3. Exercise installed profile resources, construction, inspection, durable
   service cancellation/recovery and reversible cleanup outside the checkout.
   Reject package changes while the environment has active commands or services.
4. Build from a clean checkout with only declared dependencies; test modules in
   isolation and run full cross-package/IDE validation against a frozen revision.
   Recheck licenses, imported-source provenance, documentation, artifact contents,
   and the exact exported-tree secret scan before the public root is created.

Repository hygiene, package independence and actual target-host qualification
are separate gates. A successful build is not a supported-platform claim.

The final destination and clean-root export policy remain defined by the
[public repository record](../../packaging/release/public-repository-v1.json).
Local development history and private coordination state stay outside that
export. See [migration provenance](../migration/README.md) and
[current topology](TOPOLOGY.md) for the retained source baseline.
