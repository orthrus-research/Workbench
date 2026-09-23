# Workbench product boundaries

Workbench is a CleanroomMC-first development environment for Minecraft 1.12.2
mods and packs. It joins project inspection, construction, runtime capture,
diagnostics, and native IDE surfaces while keeping the owner of each claim
explicit.

Supersymmetry is the first pack profile and the primary developer workflow we
are working to make dependable. Its developers need to understand an existing
pack, make a focused change, test it safely and explain the result. Supporting
those outcomes is the purpose of the integration, not merely exercising the
framework. Its rules are never implicit rules for other projects.

## Authority model

Workbench is an orchestrator, not a second source of truth.

- Atlas owns observed and derived game knowledge and states what remains
  uncertain.
- Blueprints owns convention-aware construction from declared patterns.
- Manuals owns practical teaching and does not authorize code.
- Crucible owns controlled runtime execution, custody, and retained records.
- Platform and pack profiles own facts that apply only to their exact target.
- Workbench Shell and the IDE clients compose and present those results without
  changing their meaning.

Historical Forge evidence remains valid for its recorded environment. Active
construction targets Cleanroom through an explicitly selected profile.

## Public surfaces

The independently runnable core provides the `workbench` command. The native
VS Code and IntelliJ Community clients call that same core and do not duplicate
profile rules or product authority.

Start with:

```bash
workbench setup
workbench open /path/to/project
workbench capabilities
```

`workbench capabilities` is generated from the installed command registry and
is the current availability source. A contract, schema, or design document by
itself does not mean a capability is implemented or supported.

See the [getting-started guide](../guides/getting-started.md) and
[client documentation](../../clients/README.md).

## Source boundaries

Product-generic implementation belongs in `modules/`. Platform and pack
authority belongs in `profiles/`. Native clients live in `clients/`. Generated
state, provisioned runtimes, worlds, downloads, sessions, credentials, and
evidence are stored outside the public source tree under ignored `.workbench/`
storage or another explicit external root.

The repository is a monorepo because the core, profiles, clients, schemas, and
cross-client conformance tests share protocol changes. API, Core, every product
module, every platform/pack profile and both IDE clients have independent
native versions and publication decisions. None requires a suite-wide release
merely to advance its own implementation.

The tested suite is a composition of component versions, not another version
authority. See [component releases](../../packaging/release/README.md).

## More detail

- [Axiom–Atlas integration vision](AXIOM-ATLAS-VISION.md) — proposed direction;
  Atlas MVP and integration qualification remain separate.
- [Cleanroom platform position](CLEANROOM-PLATFORM.md)
- [Supersymmetry profile](SUPERSYMMETRY-PROFILE.md)
- [Trust and authority](TRUST-AND-AUTHORITY.md)
- [Glossary](GLOSSARY.md)
- [Repository topology](../architecture/TOPOLOGY.md)
- [Validation and testing](../architecture/VALIDATION-AND-TESTING.md)
