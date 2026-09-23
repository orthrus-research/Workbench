# Getting started

Workbench is a CleanroomMC-first development environment for Minecraft 1.12.2
mods and packs. Supersymmetry is its first pack profile, not a universal
default.

The Linux x64 MVP includes Core, Atlas and Axiom. Windows users can run the
Linux build inside WSL2; native Windows builds are outside this release. Use
the [Atlas usage guide](atlas-mvp.md) for shared scans or capture of your own
branch and the [Axiom guide](../../modules/axiom/README.md) for saved native
initialization checks.

## 1. Choose an installation

Use Python 3.12–3.14 and a reviewed wheelhouse matching the Python minor
version, operating system and architecture. Python is a prerequisite; it is
not bundled. For Workspace Home and the command catalog, choose an assembly
containing **Core and Workbench Shell**. Pack-specific workflows also need
their explicit profile. A Core-only assembly provides environment, storage,
runtime/world and package management.

The installer is included in the wheelhouse. You do not need a Workbench source
checkout or Pixi to install it. Install into a new destination; an existing
environment is never overwritten.

### Linux x64, including WSL2

Using a wheelhouse built for Python 3.14:

```bash
python3.14 /path/to/wheelhouse/install_workbench.py /path/to/wheelhouse --destination "$HOME/.local/share/workbench/env"
"$HOME/.local/share/workbench/env/bin/workbench" --version
```

Select the Python minor matching your wheelhouse. The completed wheelhouse
installs offline and does not need a package index. Installation does not
configure a game runtime or imply that every runtime workflow supports this OS.
Inside WSL2, use a path in the Linux filesystem for the checkout, environment
and managed state. Run these commands in the Linux shell, not PowerShell.

### Connect the installed launcher

Use the full executable path above, or add its directory to `PATH` yourself.
The examples below use `workbench` on `PATH`.

In VS Code, install the Workbench VSIX and run **Workbench: Configure Core
Executable**. In IntelliJ, install the Workbench plugin ZIP and choose
**Tools → Workbench → Configure Core Executable**. Select the installed launcher,
then open one local project. VS Code also requires workspace trust.

See the [native architecture guide](../architecture/NATIVE-PACKAGES.md) for
source builds, package management and the supported installation boundary.

### Assemble the Core, Atlas and Axiom workspace

From a source checkout with the build dependencies installed, build one Linux
assembly for Core, Atlas, Axiom, Workspace Home and the Supersymmetry profile:

```text
pixi run --locked --no-config -e release python tools/build_native_distribution.py --component workbench-core --component workbench-atlas --component workbench-axiom --component workbench-shell --component workbench-profile-supersymmetry --output .workbench/build/linux-mvp
```

The builder includes declared dependencies and records the exact composition in
`wheelhouse.json`. Install that wheelhouse with the instructions above, then
install either IDE client separately. The wheelhouse and Python minor version
must match. Axiom's independent engine ZIP is a separate release object; supply
the matching ZIP during its [first-time setup](../../modules/axiom/spec/material-program-preflight.md#retained-checks-in-the-developer-workflow).

Shell brings additional modules for its project workflows. Atlas remains
independently installable: `--component workbench-atlas` selects Atlas and its
own dependencies, including Core and its CLI. It does not provide Workspace Home.

## 2. Open your project

```bash
workbench modules list --json
workbench profiles list --json
workbench open /path/to/project
workbench capabilities
```

**Open Workspace Home** in either IDE runs the same read-only inspection as
`workbench open`. It exposes the current Workspace Home V2 projection and reports
recognized project context, blockers, and useful next actions. The capability
search command (`workbench capabilities QUERY`) searches the commands
registered by the optional Shell module. Core's `workbench --help` also lists
available module routes. Missing, disabled or incompatible profile dependencies
make the affected commands unavailable rather than selecting a fallback pack.

Use **Set Up or Repair Environment** or `workbench setup` when an action reports
missing configuration. Setup shows its plan before applying it. Java is needed
for selected build/native execution workflows, not for reading source or retained
recipe evidence.

For a full developer setup on Linux x64, Core can prepare Prism
Launcher and the Packwiz CLI in user state. Review the exact acquisition plan
before applying it:

```bash
workbench tooling --check --json
workbench tooling --plan --json
workbench tooling --apply PLAN_ID --json
```

Packwiz builds from source and dependencies shipped with Workbench. Prism and
the Go compiler use pinned official archives; `--seed-dir DIRECTORY` accepts
verified copies if downloads are unavailable. Pass the same seed option to
both plan and apply. `--go-executable PATH` can select an already installed
exact Go compiler. Core reports the resulting executable paths for downstream
adapters. A missing Prism account does not block tool preparation; its setup
or offline profile is handled when launching. See
[managed tooling provisioning](../architecture/TOOLING-PROVISIONING.md) for
source, integrity and recovery details. This stage does not replace project
setup or Java selection.

For Axiom's native saved-edit checks, select the profile's exact Java 25 runtime
through `workbench setup`, then use a matching Axiom engine ZIP from the same
release. The first `checks materials setup --prepare` acquires the profile's
original inputs through Core and assembles a retained runtime. Subsequent checks
reuse it and capture the current saved checkout. Follow the
[Axiom setup and run sequence](../../modules/axiom/spec/material-program-preflight.md#retained-checks-in-the-developer-workflow)
with an explicit private state root. The engine ZIP is separate from the Python
wheelhouse; a clone can build it with `python3 tools/build_axiom.py --provision`.

Automation and IDE clients can request structured output:

```bash
workbench setup --check --json
workbench open /path/to/project --json
workbench capabilities --json
workbench --version --json
```

## 3. Choose a workflow

- **Review saved changes:** save your edits, choose **Review Saved Local Changes**
  in the IDE, select a developer context and an explicit local Git baseline,
  then inspect diffs and source findings. See [local source review](../architecture/LOCAL-SOURCE-REVIEW.md).
- **Explore recipe evidence:** choose **Search Atlas Recipes** or find the Atlas
  commands in **Open Command Center**. Select the exact retained graph or source
  checkout. Source-only results do not claim observed producers or consumers.
  In a captured graph, search an item or fluid, follow its producer recipes or
  accepted-input links, then inspect quantities, duration, energy and evidence.
  Page through large neighborhoods and return to previous selections. Repeated
  nodes are marked as cycles/references; they do not prove a usable production
  route. Reopen the saved selection later, or clear it and select a new graph.
  **Import retained recipe capture** creates a graph through an explicit pack
  adapter; **Trace captured recipe prerequisites** follows an exact item/fluid
  selection. See [Atlas](../../modules/atlas/README.md).
- **Run a check:** use **Run Checks on Saved Changes** after selecting compatible
  profile/runtime inputs. Axiom initialization and saved game checks currently
  have Linux-specific execution requirements. See [saved checks](../architecture/SAVED-CANDIDATE-CHECKS.md)
  and [Axiom](../../modules/axiom/README.md).

The command catalog exposes installed workflows and their requirements. Reading
retained evidence does not itself run the game or establish current gameplay
behavior.

## Project profiles

### Choose Java for the selected action

Atlas browsing and saved source review do not need a game Java runtime. For a
build or native check, use the runtime required by its selected profile/context.
The Java used by IntelliJ itself, the project's build JDK, and a game's runtime
can be different installations.

List local installations without changing any selection:

```bash
workbench setup --list-java
workbench setup --list-java --profile-config /path/to/workbench.toml --json
```

The inventory shows the full runtime version, vendor, architecture and whether a
compiler is present. With an explicit profile configuration, it also reports
compatibility and the reason for a mismatch. Discovery does not download Java
or choose the highest version. Use **Customize developer setup** to select a
listed installation or enter a Java home. The CLI equivalent is
`workbench setup --plan --profile-config /path/to/workbench.toml --java-home /path/to/jdk`;
review the plan and apply its exact ID with the same arguments.

Java 21 and Java 25 may coexist. Requirements are profile-owned; an existing
home does not override them. The bundled Cleanroom setup currently selects
Temurin 25.0.4+7. Axiom additionally validates its exact context/runtime/JVM
bindings, so selecting another Java version does not qualify that version for
native initialization. Its saved setup is separate from general setup and must
be reviewed when changing the native JDK. Workbench leaves global `JAVA_HOME`
and `PATH` unchanged.

On Windows select the JDK home, such as `C:\Java\jdk-25`, for general setup.
An action asking for an executable needs that home's `bin\java.exe`; Linux uses
`bin/java`. Paths with spaces are supported. Keep previously used installations
available while retained contexts still refer to them.

An existing checkout can be associated with an explicit pack profile without
writing into the project:

```bash
workbench project qualify /path/to/project --profile supersymmetry
```

Use `workbench project qualify --help` before automating this flow. Planning,
application, and saved status are separate operations so an exact plan can be
reviewed before local state changes.

## Upgrade and recover

Install a new reviewed wheelhouse into a new environment, then select its launcher
in the IDE. Keep the previous environment until the new one is working. Stopping
active work and changing the launcher are explicit steps; the installer does not
migrate or remove projects, captures or saved evidence. A failed installation
retains its `workbench-install.json` receipt for diagnosis.

## Get help

Every command owns its current help. Start with:

```bash
workbench --help
workbench COMMAND --help
```

See [Support](../../SUPPORT.md) for support and troubleshooting routes and
[Security](../../SECURITY.md) for private vulnerability reporting.
