# IDE plugin architecture

Status: current native-client boundary

## Product boundary

Workbench has two canonical IDE clients:

- [VS Code](../../clients/vscode/README.md); and
- [IntelliJ IDEA Community](../../clients/intellij-community/README.md).

Both are thin clients of an independently installed Workbench Core. The CLI
remains a complete non-IDE surface, so installing an editor is never required
to inspect, plan, validate, or run an admitted Workbench operation.

```text
VS Code client       IntelliJ Community client       terminal / CI
       |                        |                           |
       +-------- versioned Core commands and records ------+
                                |
                         Workbench Shell
                                |
           Project Intelligence, Atlas, Blueprints, profiles,
                    runtime adapters, and retained evidence
```

Workbench Shell composes discovery, review, consent, and owner results. It
does not create another truth store. Project identity, Atlas facts, Blueprint
decisions, profile policy, runtime mutation, and evidence keep their existing
owners.

## Responsibility boundary

| Concern | Core | IDE client |
| --- | --- | --- |
| Workspace, profile, Java, runtime, and record identity | Resolve and validate | Display without reinterpretation |
| Capability and availability | Publish the live catalog and reasons | Render every returned state |
| Options and command construction | Define typed fields and exact argv | Collect values through native controls |
| Plans and mutation consent | Validate, bind, revalidate, and execute | Present the exact review and collect the decision |
| Filesystem and launcher operations | Perform through the owning module or adapter | Never implement directly |
| Results, diagnostics, and retained bytes | Produce versioned records and locators | Render, diff, and navigate after validation |
| Editor lifecycle | No editor API dependency | Own activation, views, commands, progress, and disposal |

The clients do not run Packwiz themselves, edit launcher instances, select a
profile or Java runtime, approve a mutation, or turn an unavailable capability
into a local approximation. Editor trust and permissions do not imply
Workbench consent.

Client processes pass argv arrays directly and never invoke a shell. Canonical
records contain URIs, paths, source ranges, and Workbench identities rather
than editor-private objects. `.vscode/`, `.idea/`, and plugin storage are local
presentation state, not authority.

## Installed Core and source checkouts

Each client resolves the Core executable in this order:

1. its project/workspace setting;
2. `WORKBENCH_EXECUTABLE`; and
3. `workbench` on `PATH`.

The selected command must return a supported `version --json` record before
the client accepts it. Setup readiness, command availability, and result
formats are then read from that same installation. A Windows client may launch
an explicitly selected Core in one WSL distribution through its UNC path; Core
paths and workspace paths must resolve to that same distribution.

The installed `workbench` launcher is the user-facing command. From a source
checkout, `python3 tools/workbench.py ...` is the direct development route and
`pixi run --locked --no-config workbench ...` uses the locked environment.
Client tests may exercise the source route explicitly, but a packaged client
does not infer a repository checkout, import repository modules, or treat
source-only state as an installed Core.

## Command catalog, review, and consent

The installed Core's V2 live catalog is the client command inventory. Each
entry carries its owner, availability and limitations, typed fields, risk,
preview strategy, exact argv template, and action digest. Unavailable entries
remain visible with their reason. Neither client maintains a feature allowlist
or a competing command count.

For a native command-center action, the client:

1. reads `workbench console catalog --json`;
2. submits typed values as separate `--set` tokens;
3. requests a review bound to the catalog and action digests;
4. presents the returned preview and execution argv, owner, risk, and limits;
5. runs the owner preview when the command defines one; and
6. passes the same catalog, action, and review digests when the developer
   explicitly chooses execution.

Catalog or argv drift fails before child launch. The review digest is content
binding, not authentication or proof of approval. The owning route still
checks its target, plan freshness, consent, and mutation policy. Full semantics
are defined by the
[command-binding contract](../../modules/workbench-shell/contracts/workbench-live-console-command-binding-v2.md)
and the shared
[IDE surface contract](../../clients/contracts/workbench-ide-surfaces-v1.md).

Direct native surfaces may also consume other versioned Core JSON records,
such as Workspace Home, recipe review, Atlas impact, or retained-record
projections. Unknown formats and broken identity, digest, size, or path
bindings fail closed; the client does not repair them by inference.

## Native surfaces

The VS Code package is a workspace extension for one trusted filesystem
workspace. It uses commands, tree views, progress and notifications, native
diffs, read-only virtual documents, and task/terminal execution. Virtual and
multi-root workspaces are outside its current accepted surface.

The IntelliJ package targets the `262` IntelliJ IDEA Community Platform and
Java 21 for one local project. It uses the action system, tool windows,
notifications, background work, and native diff/navigation APIs. Its manifest
depends only on `com.intellij.modules.platform`; Core behavior does not depend
on a commercial platform module.

Pixels and editor conventions may differ. Equivalent access means that both
clients preserve the same Core-owned identities, availability, review,
consent, outcome, and retained-record semantics.

## Source layout

The public client root is [clients](../../clients/README.md):

```text
clients/
  vscode/                  VS Code source, package manifest, and tests
  intellij-community/      IntelliJ Community source, Gradle build, and tests
  contracts/               Shared client invariants and versioned contracts
  testing/                 Small shared source-level fixtures
```

The package manifests under each client root own editor identity and packaging
metadata. Shared fixtures in [clients/testing](../../clients/testing/README.md)
are test inputs, not another client package. Workbench Shell remains under
`modules/workbench-shell/`, and Project Intelligence remains independently
read-only under `modules/project-intelligence/`.

Each client owns its native version. Artifact naming and the release view are
derived from those manifests; see [component releases](../../packaging/release/README.md).
Compatibility requires evidence for the exact installed component tuple.

## Validation

Run the integrated client checks and documentation/catalog policy from the
repository root:

```bash
python3 validation/validate.py --ide
python3 validation/validate.py --policy
git diff --check
```

The client READMEs contain the package-local npm and Gradle commands. The
repository builder exposes its current component and lane choices with:

```bash
python3 tools/build_release_clients.py --help
```

General validation depth and suite selection are documented in
[Validation and testing](VALIDATION-AND-TESTING.md).
