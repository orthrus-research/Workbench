# Native packages and installation

Workbench uses independently versioned native Python packages for API, Core,
modules, profiles and the optional Textual terminal client, plus native IDE-client packages. The repository root is
not a distribution. There is exactly one `workbench-core` package and one Core
dispatcher. The old portable Core and bundled-interpreter release paths are
removed; no forwarding imports or legacy installer adapters are provided.

## Responsibility boundaries

API defines registration, event/session records, profile extension contracts,
filesystem ports and the typed service-store interface. It does not import Core
or product modules. Generic log normalization remains in API; domain
classification is an explicit callback supplied by an admitted profile.

Core owns provisioning, setup/repair, host filesystem and process custody,
package admission/lifecycle, runtime/storage/world management, and service
transport, scheduling, authentication and cancellation coordination. A domain
service supplies its explicit store factory; Crucible retains durable context,
job and evidence semantics.

Product modules own domain algorithms. Shell is an optional composition/client
module, not a second Core. Profiles own target policy, resources, console
choices, experiments and examples. Disabled, missing or incompatible profiles
remove their affected capabilities without disabling unrelated Core functions.
Disabling a required module also makes dependent profiles unavailable.

The first shared developer workflow uses native construction, observation and
runtime-pair contributions. See [developer context](DEVELOPER-CONTEXT.md) for
selection, per-operation input identity and the consumer-owned contracts.

Source development explicitly exposes native manifests through distribution
discovery. Installed operation uses normal Python metadata. Both use the same
Core dispatcher; no source-only product router exists.

## Build and install

Use a dedicated Python 3.12–3.14 build environment containing pip and the
repository's declared build/validation dependencies. Build for the intended
Python minor version, operating system and architecture:

```bash
python tools/build_native_distribution.py --plan
python tools/build_native_distribution.py --output .workbench/build/core-wheelhouse
python tools/build_native_distribution.py --suite --output .workbench/build/suite-wheelhouse
python tools/build_native_distribution.py --component workbench-atlas --output .workbench/build/atlas-wheelhouse
python tools/build_native_distribution.py --component workbench-tui --output .workbench/build/tui-wheelhouse
python tools/build_native_distribution.py --component workbench-shell --component workbench-tui --output .workbench/build/shell-tui-wheelhouse
```

Output must be a new directory. Core is the default selection; `--suite`
selects API, Core, modules and profiles without creating another Python package.
`workbench-tui` is opt-in. Its wheel brings Textual and the setup path picker,
while the developer inspection tool `textual-dev` is not in the installed closure.
A TUI-only wheelhouse targets an existing, separately installed Core command;
the combined example above includes the Shell dependency closure and Core.
The builder constructs fresh native inputs, resolves dependencies and records
exact wheel versions, sizes and hashes. Building may use the network. A
wheelhouse is target-specific; pure-Python Workbench wheels do not make all
third-party wheels platform-independent.
Each selected component is staged under a short temporary name with its owned
relative layout intact. Unrelated repository directories are not copied into
the build tree; the source identity still covers the complete repository input
inventory.

From a reviewed wheelhouse, install into a new isolated environment:

```bash
python /path/to/wheelhouse/install_workbench.py /path/to/wheelhouse --destination /new/path/to/workbench-env
```

The installer checks the target and complete wheel closure, privately stages
verified inputs, uses offline hash-enforced installation and verifies the
result. It rejects existing destinations instead of overwriting an environment.
Failed installations remain available for inspection and are reported as failed.
It does not install Python, modify global packages or edit shell startup files.
When the selected closure contains the terminal client, the installer checks
its noninteractive `workbench-tui --help` path and records `tui_executable` in
`workbench-install.json`. That executable is a Python console script; no separate
Textual binary is downloaded. The installer does not write user preferences.
Reinstallation uses a new environment. The TUI appearance record stays in
`~/.workbench/tui.json` unless `WORKBENCH_CONFIG_HOME` selects another directory;
Core reports its own saved setup path through `workbench setup --check --json`.
Preserve those user records when moving hosts, and supply the same custom
`WORKBENCH_CONFIG_HOME` to the new launch if one was used. Moving an installed
virtual environment is not a supported substitute for reinstalling it.

On Windows, source staging and installation can exceed the host's path-length
limit when long-path support is disabled. Keep the checkout, task-specific
temporary directory and new installation path short. Workbench does not change
the machine-wide long-path setting. A failed or partial build/install remains a
failure; qualification must use the actual chosen paths and target host.
When long-path support is disabled, the installer checks the selected wheels and
their bytecode paths before creating the destination. An excessive destination
gets an actionable error requesting a shorter path or enabled long-path support.

Package conformance follows each workflow's declared host scope. Environment
preparation executes its process fixture on Linux; other hosts must demonstrate
its explicit refusal before any state is created. Windows installation does not
qualify Linux-only environment preparation or game execution.

Core's storage inventory and cleanup use host-specific shared/exclusive file
leases. Installed conformance exercises contention between real processes on
each host, including concurrent readers, collection exclusion and read-only
inventory without creating or modifying lock-file contents.
Shell requests writer leases through the API; Core supplies the host operation.
The API reports an unavailable host capability instead of importing Core or
falling back to an unprotected write. Crucible graph imports do not require Unix
resource measurement; its peak-RSS benchmark explicitly refuses hosts without
that measurement facility.
Local file URIs use the host's path converter so Windows drive letters, spaces,
Unicode and escaped percent signs round-trip correctly. Installed conformance
checks the workflow consumers and their rejection of nonlocal URI authorities.
Low-level byte reads and writes explicitly select binary mode on Windows.
Installed conformance checks line endings, control-Z and binary payloads so
source and evidence identities do not depend on text-mode translation.
Durable service storage uses portable directory keys, keeping logical context
and job IDs in their canonical records. Existing Linux stores remain readable
and writable in place; conflicting old/new storage for one identity is refused.

Run `bin/workbench` or Windows `Scripts/workbench.exe`. Add that directory to
PATH explicitly if desired. Core-only installs expose host and package commands;
`workbench open` and the product catalog require optional Shell.

Hashes bind bytes but do not authenticate an unknown publisher. Use a reviewed
assembly from a trusted source. Downloading arbitrary modules is code
installation, not a sandboxed data import.

## Module and profile lifecycle

```text
workbench modules list --json
workbench modules install /absolute/path/to/module.whl
workbench modules update /absolute/path/to/newer-module.whl
workbench modules disable module-id
workbench modules enable module-id
workbench modules remove module-id
workbench profiles list --json
workbench profiles disable profile-id
workbench profiles enable profile-id
```

Profiles also support install, update and remove. Package installation is
explicit and offline; dependencies must already be installed. Compatibility,
duplicate registrations, reverse dependencies and disabled consumers are
checked. Missing or broken optional owners are reported rather than selected
implicitly. Package removal preserves workspaces, evidence and user data.
Storage deletion remains a separate reviewed, recoverable operation.

Pip runs with isolated Python imports, scrubbed configuration variables and all
pip configuration files disabled. File/launcher collision checks reject another
distribution's owned paths and existing unowned files before installation.
These are environment-ownership safeguards, not a sandbox for untrusted Python.

Enable/disable choices are scoped to the canonical Python environment under
`state/environments/<environment-id>/`. Legacy unscoped enable-state files are
not imported: review and reapply explicit choices for the new installation.

Commands and services hold environment activity leases. Package changes are
refused while other Workbench work uses that environment. A service retains its
lease until direct requests and jobs actually finish, including asynchronous
shutdown. Callers cannot bypass the exclusion by selecting another workspace.

External pip does not cooperate with these leases. Metadata/reinstallation drift
is detected at supported command/service checkpoints and requires stopping and
restarting affected processes. This is not a promise of atomic external pip
updates, arbitrary code-tamper detection or automatic rollback.

The bundled installer-managed daemon and side-by-side automatic upgrade/rollback
workflow have been removed. Service hosting uses an explicit endpoint and
credential. Stop the old service, install into a new environment, select its
launcher, and restart with the intended retained-state root. A stale endpoint
after a crash fails closed; inspect ownership and process state before recovery.

## Versions, resources and public contents

Each installable component owns its native version metadata. Release tooling
derives its inventory, tags and artifact selections from those manifests.
Compatibility records describe tested combinations; matching numbers do not
imply compatibility, and assemblies have no independent package version.

Python implementation paths are stable. Versioned schema/protocol identifiers
remain where externally meaningful. Source-bound records are regenerated when
implementation inputs change; historical evidence and provenance retain their
identities.

Modules and profiles package only their owned read-only resources under
`workbench_resources`. Evidence-required source bytes may be present there as
identity inputs as well as executable packages; do not delete that duplication
without changing the corresponding identity contract. Installed resources are
never cleanup or workspace roots.
The historical light-oil capture experiment remains a source-checkout tool and
is excluded from installed resources; its repository-local output and Unix
memory measurements are not installed capabilities.

Installed service registries bind their actual native metadata dependency
closure and each owner's packaged identity inputs. They neither require the
source Pixi lock nor claim its qualification. Missing optional profile owners
remain an availability decision, not a reason to invent another source layout.

World graph presentation accepts an in-process binding minted by the loaded
producer from its current registry and genuine Core registration. It does not
pin source-checkout hashes in another module or accept caller-supplied JSON as
producer trust. The binding is an API custody boundary, not a Python sandbox.

Every Workbench wheel includes the repository's license and notice with native
license metadata. Artifact audits check member safety, the private boundary,
retired source names, notices and complete RECORD hashes.

## Qualification and publication

```bash
python tools/validate_native_packages.py
python tools/validate_native_packages.py --wheelhouse /path/to/current-suite-wheelhouse
python validation/validate.py --full
```

Native qualification includes API without Core, Core without product modules,
isolated module/profile dependencies, artifact audits, lifecycle checks and
installed construction, inspection, service cancellation/recovery and cleanup.
With an exact current Suite wheelhouse, it also relocates the assembly and runs
its included installer from outside the checkout. Installation, launcher, state,
project and source filenames include spaces and Unicode characters. The checks
cover relative installer arguments, refusal to overwrite an existing environment,
runtime use after moving the wheelhouse again, platform user-state discovery,
Workspace Home and Atlas source search/inspection. Installed environments remain
at their selected destination; this does not claim that Python virtual
environments can be moved after installation.
The full suite also exercises domain behavior, IDE clients and repository policy.
These checks do not claim that a game runtime has been launched or that every
OS/architecture has been qualified. Platform support requires an actual result
for that target; a CI configuration is not test evidence.

Public source preparation uses an exact reviewed commit and a separately bound
secret-scan receipt. The clean-root export excludes private history and local
coordination state. Export verification, confidential security reporting,
repository settings and approval of actual release artifacts remain mandatory
publication checks. Repository records must not claim hosting or release success
until those external actions have actually occurred.

See [module development](../guides/module-development.md),
[component releases](../../packaging/release/README.md), and
[public repository policy](../../packaging/release/public-repository-v1.json).
