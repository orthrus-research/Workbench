# Workbench repository changelog

This file records repository-wide organization, policy, and compatibility
model changes. User-visible component changes belong in the independently
versioned component changelogs under
[`packaging/release/components/`](packaging/release/components/).

The component changelogs below record released component behavior. This
repository changelog records cross-component policy and organization changes.

## Unreleased

### Added

- Added one V2 release descriptor and compatibility authority for independently
  versioned Workbench Core, VS Code, and IntelliJ IDEA Community components.
- Added the `public-v1` component build lane, namespaced candidate workflow, and
  a fail-closed public-tree guard.
- Added contributor support, security, ownership, issue, pull-request, release,
  and dependency-update scaffolding for public hosting.

### Changed

- Promoted the real VS Code and IntelliJ implementations to canonical
  `clients/` component roots.
- Reduced source and package membership to current runtime, validation,
  profile, client, and public documentation paths.
- Made clean-checkout construction and explicit first-release bootstrap
  records requirements for public release preparation.

## Component changelogs

- [Workbench Core](packaging/release/components/workbench-core/CHANGELOG.md)
- [Workbench for VS Code](packaging/release/components/workbench-vscode/CHANGELOG.md)
- [Workbench for IntelliJ IDEA Community](packaging/release/components/workbench-intellij-community/CHANGELOG.md)
