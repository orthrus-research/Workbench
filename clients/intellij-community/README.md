# Workbench for IntelliJ IDEA Community

Workbench for IntelliJ IDEA Community is the native IntelliJ client for an
independently installed Workbench Core. It reviews Minecraft modpack changes,
presents Core-owned plans and retained records, and runs local development
commands without copying Workbench policy into the IDE.

The plugin is the independently versioned `workbench-intellij-community`
component. Its version comes from [build.gradle.kts](build.gradle.kts); release
tooling derives artifact names from that native authority. Compatibility
requires testing the exact installed Core, module and client tuple.

## Requirements

- IntelliJ IDEA 2026.2 (`262.*`). The plugin uses the Community Platform API
  and has no Ultimate plugin dependency.
- One open local IntelliJ project.
- The Workbench Core launcher on `PATH`, or an explicit executable selected
  with **Tools → Workbench → Configure Core Executable**.

PR recipe review and project-profile recording currently use the explicit
Supersymmetry profile. Other catalog actions expose the availability and
profile limits reported by the installed Core. Runtime actions can additionally
require a supported launcher, Java, Packwiz inputs, and local seed artifacts;
Workbench Setup reports those requirements before execution.

## Install Workbench

Use a reviewed native wheelhouse for your Python version and host. Install it
into a new environment as described in [Getting started](../../docs/guides/getting-started.md),
then select its `workbench` launcher with **Configure Core Executable**.
`WORKBENCH_EXECUTABLE` is also accepted in managed developer environments.

The IDE's Workspace Home and command catalog require the optional Workbench
Shell module and its declared dependencies. Profile-specific actions additionally
require their explicitly selected profile; a Core-only installation intentionally
does not provide those domain workflows. The source bootstrap prepares a Pixi
development environment, not an installed standalone launcher.

## First use

Open one local project, then choose **Tools → Workbench → Open Workspace Home**
to inspect its context and available actions. **Open Command Center** lists the
installed workflows and their requirements. Use **Set Up or Repair Environment**
when the selected action reports missing configuration.

### Explore recipe evidence

For captured recipe evidence, choose **Tools → Workbench → Search Atlas Recipes**,
open a captured graph, and select an item, fluid or recipe. Follow paged
relationships and inspect captured quantities and evidence without copying
internal IDs. The action also reopens the last graph-bound selection after an
editor restart. Each page describes a neighborhood, not a complete route.
Observed item names, recipe duration/energy, amounts and reusable-input markers
appear alongside exact identifiers. Back and paging actions appear when available.
**Clear saved Atlas selection** removes an obsolete bookmark without changing
the graph. After a graph changes, open it again and search to establish a new selection.

### Review saved changes

For everyday development, edit normally and choose **Tools → Workbench → Review
Saved Local Changes**. Create or select a developer context inside the IDE and
choose an explicit local Git baseline. Inspect native read-only diffs, editor
highlights and source findings. Further edits invalidate the review; rerun
explicitly after saving. This source-only flow requires Shell and the selected
profiles, but not Blueprints or a game Java runtime.
See [local source review](../../docs/architecture/LOCAL-SOURCE-REVIEW.md) for
bounds and the distinction between interpretation, compilation and runtime evidence.

### Optional native checks

**Tools → Workbench → Run Checks on Saved Changes → Run Axiom material preflight**
checks an explicitly bounded, complete saved Groovy program without a game image
or Minecraft launch. On first use, select the optional Axiom module's profile
context and bind the existing engine, native runtime and selected JDK through
Core-owned setup. Saved expectation JSON is optional; leave it blank for native
diagnostics. Confirm the exact prepared request before execution.

Routine runs revalidate the selected context's setup and skip the input-path and
program-directory prompts. Save edits and rerun the existing action, or use:

```bash
workbench context run SESSION -- checks materials run \
  --context supersymmetry:material-authoring-pack
```

Use the selected Work Session ID and an installed material context. Choose
**Configure and run Axiom material preflight** to explicitly rebind inputs or
change optional expectations. The
[Shell setup commands](../../modules/workbench-shell/README.md#saved-material-initialization-checks)
also expose `setup-status`, reconfiguration and advanced all-or-none three-path
overrides. One-off CLI overrides do not replace stored setup. Missing or stale
bindings require review; `ready` means current input/profile/JVM bindings, not
completed initialization or installed-pack JVM qualification. The current
Workbench JVM selection remains provisional. Setup binds already provisioned
inputs rather than downloading them automatically.

Readable results separate native execution, expected forms and qualification.
Reopen a material check to inspect retained source or compare a new saved edit.
Dirty buffers and changed files never receive historical annotations. Axiom
does not repair source or launch a Minecraft client/server. See the
[material-program contract](../../modules/axiom/spec/material-program-preflight.md).

For explicit execution, use **Tools → Workbench → Run Checks on Saved Changes**.
The IDE can import an installed native runtime image, prepare the saved candidate
and request exact consent. Progress cancellation, retained logs and interrupted-run
recovery use Core ownership. See [saved-candidate checks](../../docs/architecture/SAVED-CANDIDATE-CHECKS.md)
for prerequisites and the current Linux client-only qualification limits.

Other workflows:

1. Use **Tools → Workbench → Open Command Center** to browse the installed catalog.
2. Review the selected action’s exact requirements and any owner preview.
3. If configuration is missing, choose **Tools → Workbench → Set Up or Repair
   Environment** and review its plan before accepting local changes.

For a Supersymmetry pull request, choose **Review Pull Request Recipes**, enter
the pull request number, inspect the provider-recorded base, head, and effects,
then choose **Fetch and Review** only when those identities are correct. The
planning phase changes no refs. Apply sends the exact plan ID back to Core and
displays its compact result.

## Current native surfaces

- **Workbench Home** presents `workbench open WORKSPACE --json` context,
  blockers, and ordered actions without reranking them.
- **Recipe Review** shows introduced and pre-existing attention separately and
  opens bounded recipe-property changes in IntelliJ's native diff viewer.
- **Qualify This Pack** can record that the project matches the explicit
  Supersymmetry profile. It is optional profile-family metadata, not code,
  runtime, support, or publication approval.
- **Command Center** renders the complete live catalog, typed fields, risks,
  limitations, owner preview, and digest-bound review. It has no plugin-side
  allowlist of Workbench features.
- **Workbench Records** reopens owner-validated transactions and retained
  runtime evidence. Exact before/after bytes open through IntelliJ's native
  diff APIs after size and SHA-256 checks.
- **Recipe Impact** displays bounded structural exposure from one explicit
  verified categorical graph; it does not claim gameplay reachability.
- Reviewed material/fluid runtime execution and durable Feature Studio job
  reopening remain thin clients over their Core commands.

Folder and exact local-ref recipe comparisons remain available through Core
and Command Center. They require an explicit folder or ref and never guess a
default branch.

## Safety and local state

The plugin starts the selected Core executable directly without a shell. It
bounds process output, validates versioned JSON envelopes, keeps write-capable
actions non-cancellable after final consent, and refuses them while the IDE has
unsaved documents. Mutation and recovery decisions remain in Core.

Workbench runs locally. The plugin does not upload project files, read launcher
account data, embed pack policy, or create a second approval path. Machine-local
settings select launcher inputs and optional external state roots. When Core
runs in WSL, explicit Windows paths must be absolute UNC paths for the same
configured distribution.

## Development and packaging

The source targets Java 21 and the IntelliJ `262` API. With the repository's
pinned JDK, platform, and Gradle tools provisioned, the direct task set is:

```bash
gradle --no-daemon clean unitTest buildPlugin verifyPluginStructure verifyPlugin
```

The preferred repository entry point provisions those toolchains, runs the
tests and plugin verifier, and emits the descriptor-named public-v1 ZIP:

```bash
python3 tools/build_release_clients.py \
  --component workbench-intellij-community \
  --lane public-v1 \
  --output-dir .workbench/build/public-components
```

Run the repository builder from the repository root. Build output, downloaded
IDE distributions, and Gradle state remain under ignored `.workbench/` storage
or ignored client build directories.

Component release notes are maintained in the
[IntelliJ component changelog](../../packaging/release/components/workbench-intellij-community/CHANGELOG.md).
General support and security routes are defined by the repository
[support policy](../../SUPPORT.md) and [security policy](../../SECURITY.md).

The client/Core responsibility boundary is defined by the
[IDE client architecture](../../docs/architecture/IDE-PLUGIN-ARCHITECTURE.md)
and [Workbench Shell](../../modules/workbench-shell/README.md).

Retained Axiom checks open a summary and first findings page. The displayed count
states how much has loaded. Use **Load next findings page** or **Load remaining
findings**, **Browse retained sections and values**, and a finding's **Read native
diagnostic evidence** for details. Compiler markers show the original useful
message; long labels are marked previews. Large values can be exported completely
to a new file. **Export complete original result** preserves the original bytes.
The previous completed check remains readable as historical during a fresh run;
its markers do not carry into changed or unsaved source.


Retained material history also offers **Storage and retention**. Inspect allocation,
protections, known-store coverage and cleanup status; edit finite history preferences
or choose keep-everything. Applying settings requires review of Core's exact scope,
grace and permanent-expiry disclosure. Preview is read only; maintenance uses only
an already-enabled policy. Core also exposes these operations through
`workbench storage --checks retention status`. Native execution/output limits are
unaffected. External exports, pinned evidence and active readers remain protected.
