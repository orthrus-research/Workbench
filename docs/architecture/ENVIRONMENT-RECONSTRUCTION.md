# Portable environment selections V1–V3

Status: Core export, read-only plan, local Configuration V1 generation and
selection import are implemented. Import can also acquire the exact managed
Java release. An explicit V2 export can bind a selected project source-lock
file; V3 also binds Core's exact managed-tool policy for the selected host.
Project, fixture and tool bytes remain separate inputs to selection import.

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
workbench settings environment feasibility SHARE.json --name LOCAL_NAME --workspace /local/workspace --json
workbench settings environment import SHARE.json --name LOCAL_NAME --workspace /local/workspace --plan-id PLAN_ID
```

Add `--bind-project-source-lock` to export to create a
[V2 share](../../core/src/workbench_core/schemas/workbench-environment-share-v2.schema.json).
The selected pack variant must explicitly reference a safe, canonical V3
source-lock file. Core reads its bounded bytes and binds the file's SHA-256,
suite-relative path, HTTPS repository, full Git commit and tree into the new
portable lock. Import verifies the same file and selected variant on the
receiving suite, including after managed Java acquisition. It uses V3 plan,
attempt and result records; V1 shares and V1/V2 import records retain their
existing identities. The current Cleanroom provisional variant has no source
lock, so this explicit export choice reports the missing prerequisite. V2 does
not download project source or claim the workspace's project bytes match that
Git tree.

Add both `--bind-project-source-lock` and `--bind-managed-tools` to export a
[V3 share](../../core/src/workbench_core/schemas/workbench-environment-share-v3.schema.json).
Its lock identifies the exact Core Prism and Go archive policy and Packwiz
executable/source policy for a supported Linux or Windows host. Core checks
policy parity on import and records it in V4 plan, attempt and result records.
This is an opt-in identity lock, not an installed-tool receipt; selection import
does not acquire tool bytes. Textual exposes the same opt-in export choice and
shows the policy lock in its import review.

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

Core also offers a separate reviewed project-byte import API for V2/V3 shares.
It verifies the exact Git commit and tree in a managed checkout and retains an
acquisition receipt; ordinary selection import still leaves project bytes
unresolved.

For V3 shares, `plan_tool_import` and `apply_tool_import` provide a separate
reviewed Core API for the locked Prism and Packwiz bytes. The plan binds the
share, local workspace and state root, host, exact Core policy, retained tool
state and any explicit seed or Go selection. Apply requires that plan ID,
retains a prepared attempt before Core's fixed provisioner runs, verifies both
managed tools on reopening and publishes a result. A failed acquisition leaves
the prepared attempt for recovery. Repeat import verifies and reuses the tools
without rebuilding. This operation does not bind a workspace selection,
acquire Java, obtain project or package bytes, or clear the combined profile
fixture and tool input from the share's unresolved list. Linux and WSL use the
Linux tool policy; a selected state root that cannot enforce owner-private
custody is refused before provisioning.

For a V3 share whose selection, project and managed tools were already imported,
`plan_environment_composition` reopens their three Core result resource IDs
under the same workspace. It checks the current Configuration V1 selection,
exact managed Git checkout and acquisition receipt, and retained Prism and
Packwiz state. `apply_environment_composition` requires that plan ID, repeats
the checks and publishes one Core evidence result referencing all three prior
results. It downloads nothing and does not claim a complete environment:
optional module packages and profile fixtures remain unresolved, and Java
acquisition still follows the selection import's separate reviewed choice.

Core can also build a read-only `workbench-environment-input-candidate-v1` for
an exact V3 share through `build_input_candidate(share, wheels=...,
profile_owner_id=...)`. The caller selects one to 64 local optional package
wheels, each within Core's 256 MiB wheel bound, and
the admitted platform profile owner. Core snapshots each wheel under its
existing package admission parser and records distribution, version, declared
module/profile IDs, size and SHA-256. The selected platform document identity
in the share differs from its installed owner ID; Core compares the admitted
owner's platform document bytes to the share lock before asking that owner's
fixture extension to validate its complete source tree. The candidate records
the fixture tree digest, owner code identity, and hashes of its lock, schema
and preflight tool. Local paths do not enter the candidate. `recheck_input_candidate`
reopens all those local inputs when the platform owner is installed and admitted.

`plan_wheel_import` reviews explicit local wheel paths against the candidate
and classifies a prior Core tree as absent, reusable or recoverable. The
separate `apply_wheel_import` snapshots those exact wheels into a private
Core-managed input tree, records a prepared attempt and publishes a result
after exact readback. `reopen_wheel_import` needs only the V3 share, candidate,
workspace and result resource ID; the original wheel paths may be gone. A
validated publication intent interrupted before its tree rename can be
reconciled. An incomplete stage without that intent remains review-only.
Linux exact-tree support is checked before acquisition; WSL state on Windows
mounts still needs native qualification. This operation never invokes pip,
installs packages or clears the optional-package unresolved marker.

`plan_fixture_import` asks the admitted platform owner to validate its live
source tree, owner lock, schema and preflight tool against the V3 candidate.
`apply_fixture_import` copies only the lock's declared files and those three
witnesses into a separate private Core-managed exact tree. It retains a
prepared attempt and a result bound to the candidate and selected workspace.
`reopen_fixture_import` verifies the cataloged tree, source lock and result
without the original profile source. A validated publication intent can be
reconciled after interruption; an incomplete stage without an intent requires
review. The source profile must be installed and admitted for first acquisition.

These input receipts do not execute the fixture, install optional packages,
prove dependency closure, or change V1–V3 share and selection-import receipts.
They keep the full unresolved input list. Toolchain and runtime fixture bytes
still need their own owner locks and reviewed acquisition before a complete
rebuild can be claimed. Linux exact-tree support is required; WSL storage on
Windows mounts still needs native qualification.

`plan_package_closure` can review a local `workbench-native-wheelhouse-v1`
assembly after Core has retained the candidate's optional wheels. The caller
supplies its exact V3 share, input candidate, workspace, wheel import result ID
and an absolute wheelhouse path. Core reopens the retained wheel tree; checks
the assembly's complete manifest, hash lock and wheel bytes; and requires the
native selection to name Core, the fixture owner and any selected optional
package whose distribution is Workbench named. It compares each retained
optional wheel's exact bytes with the assembly. It matches the fixture owner's
packaged Python code and extension entry point to the candidate's admitted
code identity. It then checks all
wheel tags, Python requirements, archive members and CRCs, and evaluates the
complete `Requires-Dist` closure for the executing Python and host. Direct URL
dependencies, missing or extra wheels, and target mismatches are refused.
WSL uses a Linux Python/wheelhouse target; Windows wheels cannot satisfy it.

This is a read-only, identity-bound review record. Its `state` is `reviewed`
and its coverage is `reviewed-offline-dependency-closure-only`. The native
manifest's hashes detect byte changes but do not authenticate its publisher.
No wheels are copied into new Core custody, no environment is created, and no
package is installed. The `optional-module-packages` marker remains unresolved.
`plan_package_import` reviews that closure plan for an exact private Core tree.
`apply_package_import` repeats the source review, records a prepared attempt,
copies only the manifest, hash lock and named wheels through pinned source
directories, and publishes the tree with no-replace semantics and exact Linux
inventory. `reopen_package_import` verifies the retained tree and result
without the original wheelhouse path. A committed tree can be reused; an
interrupted publication with a complete intent can be reconciled. A failed
stage or a foreign target is preserved for review. The 32 GiB exact-tree bound
is enforced before copy. On WSL the destination must support owner-private
Linux tree custody; Windows-mounted storage remains unqualified.

The retained wheelhouse still has no installed-package authority.
`plan_package_install_preflight` takes the exact V3 share, input candidate,
reviewed closure, workspace and retained package result ID. It reopens the
managed wheelhouse, binds the executing and base Python binaries by resolved
path, size and SHA-256, and checks the exact Python, host and virtual
environment layout. Core derives one stable destination directly beneath its
selected owner-private evidence root from the closure and base interpreter identities. It
requires that destination to be absent; an existing file, directory or redirect
is blocked for explicit recovery review. The installer creates the
environment at that final path after a prepared attempt. Renaming a populated
virtual environment from a temporary path would leave generated scripts bound
to the old path.

The preflight maps every retained wheel member and console/GUI launcher to its
prospective destination and refuses duplicate files, file/directory overlaps,
reserved launchers, bytecode and unsupported `.data` layouts. It checks the
Linux mount under the selected evidence root and blocks Windows-mounted WSL filesystems
such as 9p/DrvFS; WSL native execution still needs separate qualification.
The result is a sealed, read-only review with `state` `reviewed` or `blocked`
and coverage `read-only-isolated-install-preflight-only`. It creates no
directory or environment, invokes neither venv nor pip, and preserves every
unresolved input marker, including `optional-module-packages`. Install execution,
restart reconciliation, dependency checks, module/profile and fixture-owner
admission have their own APIs or evidence gates below.

`apply_package_install` requires that exact reviewed preflight ID. It publishes
a prepared Core evidence resource before asking Core's working-allocation
service to reserve and activate the stable destination. The allocation's own
catalog and marker establish custody of the mutable directory. Core then adds
an immutable attempt binding and creates the virtual environment at its final
path. Pip bootstraps from the retained, hash-locked wheelhouse with no index,
bytecode generation or installation compilation, and `pip check` runs inside
that environment. Both child commands use Core's
process supervisor with process-group closure, timeouts and bounded private
stdout/stderr captures. Core verifies the retained input again after execution,
then records selected completion evidence and a result. A failed or interrupted
destination stays protected by the working-allocation catalog; a later attempt
does not overwrite it.

`reconcile_package_install` checks the Core allocation and prepared binding
after restart. If pip completion and its captures were retained but allocation
terminal or Core result publication was interrupted, it can finish that exact
evidence and return a result. If the attempt marker or completion is missing,
it reports `incomplete-review-required` and preserves the directory for an
explicit recovery decision. `reopen_package_install` verifies the retained
result and selected current files. This slice records isolated pip completion,
not installed module/profile or fixture-owner admission; every unresolved
input marker, including `optional-module-packages`, remains.

`admit_package_install` is a separate Core result after pip completion. It
reopens the retained wheelhouse, the prepared install result and the Core
working allocation, then inventories the isolated site and launcher trees
through exact POSIX no-follow reads. Both process captures must bind the exact
install and check command identities; earlier captures without that binding
remain readable but cannot gain package admission. Preflight and admission hold
each retained wheel's parent and file descriptors through ZIP inspection and
recheck its exact bytes and visible inode afterward. Every installed source
wheel member except pip-regenerated `RECORD` must match its retained bytes. Each distribution's
installed `RECORD` must name exactly its wheel files, pip installer metadata
and declared launchers, with valid hashes and sizes. Extra files, links,
missing distributions and changed
bytes refuse admission. Pip's generated interpreter-minor launcher is reserved
in preflight. The result records exact installed-tree digests and resolves only
`optional-module-packages` in its new `remaining_unresolved_inputs` list;
historical V3 share, install and composition receipts are unchanged.
`reopen_package_admission` repeats the checks against current bytes. Native
Linux/WSL private-filesystem support remains required, and profile fixture
execution, managed tool materialization and clean-root reconstruction retain
their separate evidence gates.

`plan_environment_input_composition` is a V2 linked review of five separately
completed Core results: workspace selection, exact project checkout, managed
tools, retained optional wheel bytes and retained profile fixture sources. It
binds their resource IDs and current readbacks to the same V3 share, input
candidate and workspace. `apply_environment_input_composition` publishes a
linked evidence result; `reopen_environment_input_composition` repeats the live
checks without requiring the original wheel or fixture source paths. When the
selection acquired managed Java, Core also reopens and probes that runtime
against its policy and receipt. A user-supplied Java path remains an unchecked
local binding. The linked result inherits the project-byte closure and any
managed-Java closure from their original receipts. Optional package
installation, dependency closure and fixture toolchain/runtime execution remain
unresolved. It does not orchestrate
acquisition of all five inputs after one approval; that needs a separate step
journal and recovery contract.

`plan_environment_package_composition` is a read-only next review. It reopens
that five-input result and a separate installed-package admission against the
same V3 share, candidate, workspace, closure plan and retained optional-wheel
resource. It binds both current Core result identities and removes only
`optional-module-packages` from the review's remaining-input list. The
historical results retain their original markers. Profile fixture execution,
managed tool runtime materialization and a supported clean-root rebuild remain
unresolved, so this plan is not an environment reconstruction result.

`plan_environment_artifact_composition` can extend that read-only review with
one separately admitted, owner-validated fixture JAR. It recomputes the exact
package composition, reopens the artifact admission and its execution/resource
chain, and requires the same V3 share, candidate, workspace and retained fixture
result. It publishes no new resource and leaves every remaining input marker,
including the combined fixture/tool marker, unchanged. A copied or absent
profile-owned pack source lock, tool runtime materialization, escaped process
descendants and a clean-root rebuild remain outside this evidence join.

Cleanroom fixture artifact admission is a separate Core operation after a
zero-exit supervised fixture attempt. The installed profile derives the sole
remapped JAR path from retained locked inputs and checks the exact ZIP content.
Core pins the output read, records a prepared attempt, retains an immutable JAR
resource, and links a result to the exact execution evidence. Reopen validates
the retained bytes and resource chain even if the mutable build output changes.
A prepared attempt without its final result remains unknown and cannot retry
automatically. This admits an observed artifact snapshot, not an independent
build provenance or a complete environment reconstruction.

`environment feasibility` is read-only and uses the same local options as
`plan`. Its [versioned report schema](../../core/src/workbench_core/schemas/workbench-environment-feasibility-v1.schema.json)
names the exact import plan, local blockers and missing acquisition inputs. The
Cleanroom provisional pack variant has no
project source lock. A variant may reference a local source-lock file with Git
repository, full commit and tree identities, but the V1 portable share hashes
only the profile document, not that referenced file. The report marks such a
file as a local, unbound candidate and shows its current SHA-256. V2 reports
`portable-lock-bound` only when the target file matches the selected portable
lock, while project bytes remain unresolved. Optional module packages and
profile fixture/tool artifacts likewise need exact identities and hashes. The
report does not inspect or reject a user-supplied Java path; managed Java
acquisition remains the reviewed import operation.

The [V2 feasibility report](../../core/src/workbench_core/schemas/workbench-environment-feasibility-v2.schema.json)
for a V3 share additionally reports whether the locked managed-tool policy
matches Core and whether its bytes are present. Package and profile fixture
locks remain missing.

WSL is a Linux managed-Java and managed-tool host. A WSL-mounted Windows path is
a local binding and never enters the share. Windows execution from WSL is a
different host variant; share import reports that lock as unsupported rather
than reusing Linux runtime or tool custody across the boundary.

## Remaining reconstruction work

Core's read-only intent admission reuses Configuration V1's profile validators
but still repeats its pack-variant/platform binding check. A later Core cleanup
should expose one pure profile-snapshot parser for both manifest loading and
intent admission.

Provisioning must connect the retained wheel bytes to dependency-complete
optional package installation and profile fixture acquisition, preserving
incomplete outcomes and reporting unavailable inputs.
The Core composition receipt links already imported selection, project and
tool results but does not perform a combined acquire. Interrupted project and
tool acquisition still need restart reconciliation; missing package and fixture
locks prevent a claim that one small manifest rebuilds a complete environment.
Textual presents the same reviewed managed Java acquisition choice during
import. Its checkbox change invalidates the prior plan and requires a new
review before any download or binding.
