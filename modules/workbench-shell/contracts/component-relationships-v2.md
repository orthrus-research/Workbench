# Workbench component relationships V2

Status: experimental executable bootstrap contract

## Purpose

The component registry records dependency direction and lifecycle state
without becoming a second authority map. It exists so source layout, clients,
and future protocol capability discovery agree on how Workbench is composed.
Each non-client component is treated as an independent project even while the
initial implementation shares one repository. An integration dependency means
Workbench calls a public project boundary; it does not transfer that project's
domain logic or authority into the Shell.

V2 is a new Workbench-native format. Imported V1 module identities and records
remain unchanged.

## Component kinds

- `authority`: Atlas, Blueprints, or Manuals.
- `foundation`: Workbench Shell or Project Intelligence.
- `client`: a native IDE adapter.

Exact platform and pack profiles are resources consumed through Project
Intelligence. They are not modules and are not copied into this registry.

## Required direction

```text
VS Code client ----+
                   +--> Workbench Shell --> Project Intelligence --> profiles
IntelliJ client ---+          |
                              +--> Atlas
                              +--> Blueprints
                              +--> Manuals
```

The enforced rules are:

- each client has exactly one module dependency: Workbench Shell;
- Workbench Shell depends directly on Project Intelligence and every declared
  authority through their public integration boundaries;
- Project Intelligence and each authority have no executable module
  dependencies in this bootstrap;
- every dependency target and component path exists;
- component paths are unique and remain inside the repository;
- the graph is acyclic; and
- each canonical client package manifest exists under the registered client
  root and exposes the expected Workbench entry point.

Blueprints may consume digest-bound Atlas evidence without importing Atlas as
an executable module. Manuals may consume accepted records without importing
their engines. Those authority relationships therefore remain data flow, not
registry dependency edges.

## Lifecycle meaning

The registry uses the shared Workbench lifecycle states. A component's state
describes the component project, not every capability inside it.

The VS Code and IntelliJ Community roots are `review`: their executable plugin
sources, package manifests, and relationships exist, while public release
qualification remains separate.

The JSON contracts are:

- [component-registry-v2.schema.json](../schemas/component-registry-v2.schema.json)
