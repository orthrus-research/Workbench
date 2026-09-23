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
