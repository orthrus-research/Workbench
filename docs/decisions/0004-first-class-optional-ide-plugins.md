# Decision 0004: IDE plugins are first-class and optional

Status: accepted
Date: 2026-07-30

## Decision

Workbench will ship native plugins for VS Code and IntelliJ as first-class
developer experiences. Neither IDE is required to install, operate, automate,
or validate Workbench.

Product-generic behavior remains in an independently runnable local Workbench
core. A headless CLI and the two IDE plugins use the same versioned request and
result contracts. The plugins translate those contracts into native editor
commands, views, diagnostics, progress, review, and navigation; they do not
implement alternate Atlas, Blueprints, profile, Packwiz, launcher, or runtime
logic.

The first inter-process transport will be JSON-RPC 2.0 over standard input and
output. A plugin starts or attaches to a compatible Workbench core on the same
side as the workspace. Standard Language Server Protocol support may later
carry editor-language features, but LSP is not the Workbench domain protocol
and is not required for authoritative capability.

## Reasons

- Workbench must remain useful in terminals, CI, Gradle, and other automation.
- A single core prevents VS Code, IntelliJ, and headless behavior from
  becoming three different products.
- A process boundary lets TypeScript, Kotlin, and the core evolve without
  sharing an implementation language.
- Standard I/O avoids an exposed local port and works naturally in remote
  workspaces when the core runs beside the workspace.
- JetBrains' built-in LSP integration is not available in open-source IntelliJ
  builds, so making it mandatory would violate the Community-compatible
  product commitment.

## Consequences

- VS Code and IntelliJ receive dedicated native clients and release checks.
- The CLI remains a supported product surface, not merely a debugging tool.
- IDE-specific source lives outside authority modules and may contain
  presentation and host-integration code only.
- Every operation exposes the same capability state, exact profile identity,
  consent boundary, result, and failure across surfaces.
- Packwiz, Prism Launcher, MultiMC, Java, and Cleanroom integration live behind
  the core boundary. An IDE plugin never edits a launcher instance directly.
- Browser-only or otherwise process-restricted editor environments report the
  unavailable capability explicitly instead of substituting weaker behavior.

The detailed boundary and first vertical slice are defined in
[IDE plugin architecture](../architecture/IDE-PLUGIN-ARCHITECTURE.md).
