# Workbench for VS Code

Workbench for VS Code is the native VS Code client for an independently
installed Workbench Core. It reviews Minecraft modpack changes, presents
Core-owned plans and retained records, and runs local development commands
without copying Workbench policy into the editor.

The extension is the independently versioned `workbench-vscode` component.
Its version comes from [package.json](package.json); release tooling derives
artifact names from that native authority. Compatibility requires testing the
exact installed Core, module and client tuple.

## Requirements

- VS Code `1.125.0` or a compatible later `1.x` release accepted by
  [`package.json`](package.json).
- One trusted local filesystem workspace. Virtual and multi-root workspaces
  are not supported.
- The Workbench Core launcher on `PATH`, or an explicit executable selected
  with **Workbench: Configure Core Executable**.

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

Open and trust one local project, then choose **Workbench: Open Workspace Home**
to inspect its context and available actions. **Open Command Center** lists the
installed workflows and their requirements. Use **Set Up or Repair Environment**
when the selected action reports missing configuration.

### Explore recipe evidence

For captured recipe evidence, choose **Workbench: Search Atlas Recipes**, open
a captured graph, and select an item, fluid or recipe. Follow paged relationships
and inspect captured quantities and evidence without copying internal IDs.
The same action can reopen the last graph-bound selection after an editor restart.
Source checkout search remains explicitly separate from captured relationships.
Loading and queries can be cancelled without replacing the last saved selection.
Observed item names, recipe duration/energy, amounts and reusable-input markers
appear in the picker; exact identifiers and full values remain available.
**Clear saved Atlas selection** removes an obsolete bookmark without changing
the graph. After a graph changes, open it again and search to establish a new selection.

### Review saved changes

For everyday development, edit normally and choose **Workbench: Review Saved
Local Changes**. Create or select a developer context inside the IDE and choose
an explicit local Git baseline. Inspect native read-only diffs and Problems,
then follow verified source findings and declared related sources. Further
edits invalidate the review; rerun explicitly after saving. This source-only
flow requires Shell and the selected profiles, but not Blueprints or Java.
See [local source review](../../docs/architecture/LOCAL-SOURCE-REVIEW.md) for
bounds and the distinction between interpretation, compilation and runtime evidence.

### Optional native checks

**Workbench: Run Checks on Saved Changes → Run Axiom material preflight** checks
an explicitly bounded, complete saved Groovy program without a game image or
Minecraft launch. It requires the optional Axiom module, selected profiles, local
native runtime and selected JDK. On first use, choose a material context and bind
the existing engine/runtime/JDK inputs through Core-owned setup. Saved expectation
JSON is optional; leave it blank for native diagnostics. Confirm the exact
prepared request before execution.

Routine runs revalidate that context's saved setup and do not request the three
input paths again. Save your edits and rerun the existing action, or use:

```bash
workbench context run SESSION -- checks materials run \
  --context supersymmetry:material-authoring-pack
```

Use the selected Work Session ID and an installed material context. The
[Shell setup commands](../../modules/workbench-shell/README.md#saved-material-initialization-checks)
also expose `setup-status` and explicit reconfiguration. From a retained check,
**Review Axiom setup or optional expectations** lets you change the saved inputs.
Missing or stale bindings require review; `ready` means current input/profile/JVM
bindings, not completed initialization or installed-pack JVM qualification.
The current Workbench JVM selection remains provisional. Setup binds already
provisioned inputs rather than downloading them automatically. Advanced CLI
path overrides must supply all three paths and do not replace stored setup.

Native execution, expected forms and qualification remain separate. Reopen
retained checks to read outcomes, compare a new saved edit, or open exact retained
source. Unsaved buffers and changed source never receive historical diagnostics.
Axiom does not repair source or launch a Minecraft client/server. See the
[material-program contract](../../modules/axiom/spec/material-program-preflight.md).

For explicit execution, use **Workbench: Run Checks on Saved Changes**. The IDE
can import an installed native runtime image, prepare the saved candidate and
request exact consent. Progress cancellation, retained logs and interrupted-run
recovery use Core ownership. See [saved-candidate checks](../../docs/architecture/SAVED-CANDIDATE-CHECKS.md)
for prerequisites and the current Linux client-only qualification limits.

Other workflows:

1. Use **Workbench: Open Command Center** to browse the installed command catalog.
2. Review the selected action’s exact requirements and any owner preview.
3. If configuration is missing, run **Workbench: Set Up or Repair Environment**
   and review its plan before accepting local changes.

For a Supersymmetry pull request, run **Workbench: Review Pull Request
Recipes**, enter the pull request number, inspect the provider-recorded base,
head, and effects, then choose **Prepare and Review** only when those identities
are correct. The planning phase changes no refs. Apply sends the exact plan ID
back to Core and displays its compact result.

## Current native surfaces

- **Workbench Home** presents `workbench open WORKSPACE --json` context,
  blockers, and ordered actions without reranking them.
- **PR Recipe Review** shows introduced and pre-existing attention separately
  and opens bounded recipe-property changes in VS Code's native diff editor.
- **Qualify This Pack** can record that the project matches the explicit
  Supersymmetry profile. It is optional profile-family metadata, not code,
  runtime, support, or publication approval.
- **Command Center** renders the complete live catalog, typed fields, risks,
  limitations, owner preview, and digest-bound review. It has no extension-side
  allowlist of Workbench features.
- **Workbench Retained Records** reopens owner-validated transactions and
  retained runtime evidence. Exact text bytes can open in read-only virtual
  documents after size and SHA-256 checks.
- **Atlas Recipe Impact Candidates** displays bounded structural exposure from
  one explicit verified categorical graph; it does not claim gameplay
  reachability.
- Blueprint examples, reviewed material/fluid runtime execution, and durable
  Feature Studio job reopening remain thin clients over their Core commands.

Folder or exact local-Git-baseline recipe comparison remains available through
**Review Recipe Changes Against Another Baseline**. It requires an explicit
folder or ref and never guesses a default branch.

## Safety and local state

The extension starts the selected Core executable directly without a shell.
It rejects untrusted workspaces, bounds process output, scrubs interpreter
injection variables, validates versioned JSON envelopes, and keeps mutation
and recovery decisions in Core. Cancellation is offered only where the owning
operation can stop safely.

Workbench runs locally. The extension does not upload project files, read
launcher account data, embed pack policy, or create a second approval path.
Machine-local settings select launcher inputs and optional external state
roots. When Core runs in WSL, explicit Windows paths must be UNC paths for the
same configured distribution.

## Development and packaging

Run the extension-local checks from this directory with the locked npm input:

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run test:package
```

There is no second extension-local packaging command. The repository builder
is the sole distributable VSIX path: it stages the canonical repository
license and notice, provisions its pinned Node/npm tools, runs the package and
extension-host checks, normalizes the archive, and emits the descriptor-named
public-v1 artifact:

```bash
python3 tools/build_release_clients.py \
  --component workbench-vscode \
  --lane public-v1 \
  --output-dir .workbench/build/public-components
```

Run that command from the repository root. Build output and downloaded tools
remain under ignored `.workbench/` storage or ignored client dependency
directories.

The component release notes are maintained in the
[VS Code component changelog](../../packaging/release/components/workbench-vscode/CHANGELOG.md).
The package-local [change log](CHANGELOG.md) and [support guide](SUPPORT.md) are
included in the VSIX. General project support and security routes are defined
at the repository root.

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
