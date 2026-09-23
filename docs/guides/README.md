# Workbench guides

These guides cover the current public command surface. Run
`workbench COMMAND --help` for the complete options supported by the installed
core.

- [Getting started](getting-started.md) installs the core, configures a local
  workspace, and shows how to discover available capabilities.
- [Atlas MVP usage guide](atlas-mvp.md) walks through installation, shared
  scans, dead-end review, producer/consumer queries and capturing a saved branch.
- [IDE clients](../../clients/README.md) explains the optional VS Code and
  IntelliJ Community clients.
- [Validation and testing](../architecture/VALIDATION-AND-TESTING.md) lists
  checks for contributors and maintainers.

Workbench stores generated state, downloads, run records, and local
configuration under ignored `.workbench/` storage or the configured external
state root. Those files are not source and should not be committed.
