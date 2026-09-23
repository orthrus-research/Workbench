# Workbench IDE client surfaces V1

## Purpose

The IntelliJ and VS Code plugins are high-touch projections of the same
Workbench used from a terminal. They improve discovery, input collection,
language feedback, source navigation, and retained-result access. They never
approve an action or infer a runtime identity independently.

## Shared inputs

An IDE surface may consume only versioned Workbench seams:

1. `workbench-live-console-command-catalog-v2` for suites, actions, fields,
   availability, risk, preview strategy, authority, documentation, known
   limitations, and catalog/action digests;
2. `workbench-live-console-command-review-v2` for the exact rendered preview
   and execution argv, intents, risk, preview strategy, and review digest;
3. `workbench-groovy-language-session-descriptor-v1` for a ready managed
   language endpoint, exact program/runtime/profile identity, local/server URI
   mapping, canary evidence, capabilities, retained artifacts, and ownership;
4. `workbench-feature-studio-snapshot-v2` for a closed, bounded read-only
   projection of one fully validated Feature Studio result or material-flow V2
   receipt; and
5. existing command outputs, receipts, console event streams, and Manuals for
   result presentation.

Unknown versions, stale descriptor identities, non-loopback language
endpoints, mismatched readiness ports, unsupported consumers, malformed URI
maps, and unavailable catalog commands fail closed.

## Invocation and consent

Catalog fields are submitted to `workbench console run` as separate argv
tokens using the catalog's JSON assignment form. Neither plugin evaluates a
shell command. The plugins bind review to the catalog digest and selected
action digest, then pass that review digest through owner preview and execution.
Catalog, action, option, risk, preview, or rendered-argv drift therefore fails
closed instead of silently changing the consented action. The plugins display
authority and risk before execution. Mutating and destructive actions still
cross the Workbench Shell consent and downstream owner-validation boundaries.
Long-running execution remains visible and cancellable in an IDE terminal or
task surface.

The catalog remains the module-flow expansion seam. Adding a command to an
installed IDE does not require copying its fields or policy into native plugin
code.

## Retained Feature Studio presentation

Both clients request snapshot V2 through separate exact argv to
`workbench studio inspect --snapshot --json`. Workbench Shell validates the
complete result and owner chain before projecting it. A retained refresh names
only the prior closed snapshot; Shell reopens the original result or receipt
and rejects origin kind, URI, digest, size, or owner-link drift.

Clients treat the snapshot as read-only presentation. They must preserve the
exact result, semantic, plan, source, profile, runtime, export, and owner
identity aliases; reject unknown fields and versions; convert the declared
one-based/zero-for-empty source range exactly once; validate URIs before
navigation; and reread physical source or retained evidence under fixed byte
bounds. A plan diff must bind the current and reconstructed planned bytes to
the snapshot's SHA-256 values before opening a native diff. A retained-owner
document must match its declared digest and size before display.

Snapshot caching is client-local state under ignored `.workbench/` storage or
the IDE's local state mechanism. It is not evidence, does not alter snapshot or
result identity, and cannot authorize execution.

## Managed GroovyScript attachment

VS Code uses the Language Client library with a direct TCP `StreamInfo` over
the descriptor's public endpoint. IntelliJ's supported LSP API launches
`workbench groovy proxy --descriptor ...`, which validates the descriptor again
and copies raw LSP bytes between stdio and the same public endpoint. The proxy
does not parse language messages and cannot launch Minecraft.

Both clients map their actual project/workspace URI root to the descriptor's
`server_workspace_uri` for outbound messages and reverse that map for source
navigation and diagnostics. Mapping is root-boundary-aware; sibling prefixes
are not rewritten.

The upstream GroovyScript 1.4.3 server supports one active client and
sequential reaccept. IntelliJ, VS Code, and terminal clients therefore cannot
be attached concurrently. Detaching stops only the IDE language client.
Workbench remains the physical-client and session shutdown owner.

## Platform lifecycle

The VS Code extension is a workspace extension, stores local selection state
through `ExtensionContext`, disposes commands/channels/status through
`context.subscriptions`, requires Workspace Trust, uses one restrained global
status item, and exposes execution through `ProcessExecution` rather than a
shell.

The IntelliJ plugin is project-scoped, persists only local selection settings,
performs descriptor I/O off the event-dispatch thread, declares the LSP module
dependency, implements `LspIntegrationProvider` and
`ProjectWideLspClientDescriptor`, uses the platform Language Services status
surface, and declares background action-update threads.

## V1 horizon

VS Code and IntelliJ now both provide full-catalog native wizards over the
digest-bound V2 catalog/review seam. The dependency-safe retained-result
checkpoint consumes the shared Feature Studio snapshot without adding a
runtime or graph authority. Cross-IDE behavior is validated through the shared
snapshot contract and each client's own build.
Completion, hover, signature, definitions, and diagnostics are limited to
capabilities actually reported by the upstream session. Texture decoration,
observed effect ledgers, reload qualification, executable verification,
durable jobs, recipe graphs, and broad Blueprint-backed authoring remain
separate future increments.
