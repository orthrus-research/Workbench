# Workbench IDE clients

The public IDE clients live directly under this directory:

- [VS Code](vscode/README.md)
- [IntelliJ IDEA Community](intellij-community/README.md)

Each client is an independently buildable release component and a thin
consumer of the installed Workbench core. Client code may own native commands,
views, progress, diagnostics, review, and navigation. It may not copy Project
Intelligence, Atlas, Blueprints, Manuals, profile policy, launcher mutation, or
approval decisions into the editor.

The real package manifests are authoritative for client source and packaging:
[`package.json`](vscode/package.json) for VS Code and
[`build.gradle.kts`](intellij-community/build.gradle.kts) plus
[`plugin.xml`](intellij-community/src/main/resources/META-INF/plugin.xml) for
IntelliJ. The component registry records dependency direction without adding a
second client descriptor.

Shared client invariants live under [`contracts/`](contracts/), and the small
cross-client fixture set lives under [`testing/`](testing/). Generated
dependencies, IDE sandboxes, packages, and verification output are local,
ignored state.
