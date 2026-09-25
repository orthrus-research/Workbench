# Repository topology

Workbench stays in one monorepo so protocol, schema, profile, core, and client
changes can be tested together. Components still version and release
independently.

This is the current layout. [Core and module separation](CORE-AND-MODULES.md)
defines ownership; [native packages](NATIVE-PACKAGES.md) defines the sole
installation path and the remaining release qualification gates.

## Public source tree

```text
api/                         module, provider and service contracts
core/                        environment management, dispatch and service hosting
clients/
  vscode/                    VS Code extension
  intellij-community/        IntelliJ Community plugin
modules/                     product-generic implementation
profiles/
  platforms/                 exact platform authority
  packs/                     exact pack authority
packaging/                   component packaging and release metadata
tests/conformance/           cross-component protocol checks
tools/                       contributor and packaging entry points
validation/                  repository validation
docs/                        public user and architecture documentation
assets/                      redistributable source assets
```

Local-only workflow metadata, downloaded dependencies, game artifacts, worlds,
captures, build output, credentials, and generated evidence are not part of the
source topology. They belong under ignored `.workbench/` storage or another
explicit external root.

## Dependency direction

Native Core and modules depend on the API. Core discovers installed modules
through entry points; it has no static product imports. Module-to-module
dependencies are explicit and acyclic, checked by `tools/module_packages.py`.
Profile providers are selected explicitly. Core owns native and source command
dispatch plus generic service transport/scheduling. The optional Shell still
composes domain workflows for the clients:

```text
VS Code / IntelliJ / CLI
           |
     Workbench Core          dispatch, host/service lifecycle
           |
     Workbench Shell         optional domain compositions
           |
  +--------+---------+----------+----------+
  |        |         |          |          |
Atlas  Blueprints  Relay     Crucible   other product modules
  |        |         |          |          |
  +--------+---------+----------+----------+
           |
   explicit platform and pack profiles
```

Surfaces own interaction and presentation. They do not copy authority,
construction policy, profile facts, or approval logic. Product modules may
compose another module through a declared contract, but a composition cannot
upgrade an observation into proof or an experimental pattern into a stable
standard.

## Modules

A module keeps only the directories it uses:

```text
src/          executable implementation
tests/        focused tests and controlled fixtures
schemas/      machine-verifiable record contracts
contracts/    stable semantic contracts
examples/     canonical or explicitly synthetic examples
```

A contract does not imply implementation, and implementation does not imply a
support claim. Availability comes from the core's live product capability
catalog.

## Profiles

Platform profiles bind exact Minecraft, Cleanroom, Java, bytecode, mappings,
build, transformation, and side assumptions. Pack profiles bind pack-specific
source authorities, conventions, fixtures, diagnostics, runtime policies, and
limitations.

Profiles reference external evidence by stable identity and digest. They do not
vendor mutable workstations, game installations, dependency binaries, or live
worlds. Supersymmetry is the first pack profile and is not inferred for an
unrelated checkout.

## Local state

Core command dispatch resolves platform user-state by default for source and
installed execution. Legacy source-only workflows still use ignored checkout
`.workbench/` storage until their owners migrate; it may include provisioned
targets, downloads, managed JDKs, content-addressed records, runtime sessions,
disposable worlds, staging areas, caches, and recovery state. Commands may use
another explicit state root, but that does not make the data source material.
See [user context](USER-CONTEXT.md) for the current location schema and
remaining legacy boundaries.

Do not package or publish `.workbench/`. Each native package declares its own
source and resources. Builders use fresh repository-input copies; artifact
audits and the public-tree validator reject local-only workflow paths.

## Release units

There are 23 independently versioned release units: 19 API, Core, module and
profile Python distributions, the optional Textual TUI wheel, two IDE clients,
and the Axiom JVM engine. Python versions come from each native `pyproject.toml`;
IDE and JVM versions come from their native manifests. The release view is derived, not another version
authority. The root workspace does not build a distribution.

Qualification records bind exact tested tuples without forcing lockstep
versions. A wheelhouse is an assembly of packages, not a versioned Suite
distribution. See
[component releases](../../packaging/release/README.md) and
[build paths](BUILD-PATHS.md).
