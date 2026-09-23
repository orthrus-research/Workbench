# Workbench documentation

The documentation describes the current product, its source boundaries, and
workflows that users or contributors can run. Generated runtime evidence and
local development state are not source documentation.

## Start here

- [Getting started](guides/getting-started.md)
- [Atlas recipe scans and routes](guides/atlas-mvp.md)
- [Axiom native saved-edit checks](../modules/axiom/README.md)
- [Product boundaries](product/README.md)
- [Architecture and module topology](architecture/TOPOLOGY.md)
- [Developing a module](guides/module-development.md)
- [Native installation and package lifecycle](architecture/NATIVE-PACKAGES.md)
- [Shared developer context and profile interfaces](architecture/DEVELOPER-CONTEXT.md)
- [Source navigation and exact editor locations](architecture/SOURCE-NAVIGATION.md)
- [Checks on developer-authored saved changes](architecture/SAVED-CANDIDATE-CHECKS.md)
- [Validation and testing](architecture/VALIDATION-AND-TESTING.md)
- [IDE clients](../clients/README.md)
- [Component releases](../packaging/release/README.md)

Architecture decisions in [`decisions/`](decisions/) record constraints that
still apply. Migration records in [`migration/`](migration/) describe the
provenance of retained imported source.

Repository policies live at the root:

- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Support](../SUPPORT.md)
- [Governance](../GOVERNANCE.md)
- [Releasing](../RELEASING.md)
