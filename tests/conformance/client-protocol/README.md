# Client protocol conformance

Lifecycle: `active`;
read-only V2 protocol scope

This root holds request/result fixtures shared by CLI, VS Code, and IntelliJ.
The current scope covers component dependency direction plus the executable
read-only protocol lifecycle.

[relationships-v2.json](relationships-v2.json) records the required edges and
client entry point accepted by:

- [Decision 0004](../../../docs/decisions/0004-first-class-optional-ide-plugins.md);
- [IDE plugin architecture](../../../docs/architecture/IDE-PLUGIN-ARCHITECTURE.md);
- [repository topology](../../../docs/architecture/TOPOLOGY.md); and
- [component relationships V2](../../../modules/workbench-shell/contracts/component-relationships-v2.md).

The V2 fixtures cover:

- a compatible `initialize` request and capability response;
- an empty `workspace/inspect` request;
- a concrete client `runtime/plan` request;
- a retained-evidence `runtime/diagnose` request;
- clean `shutdown`; and
- the relationship graph used by the returned foundation record.

The executable stdio host implements `initialize`, `workspace/inspect`,
`runtime/plan`, `runtime/diagnose`, and `shutdown`. Runtime planning is
read-only and may return a complete blocked plan when required runtime
identities are unresolved. Runtime diagnosis is read-only and may return a
blocked runtime as a successful evidence-backed result. Protocol V2.2 returns
runtime-diagnosis V2, whose success state names the exact reached checkpoint
and whose confirmed artifact findings are bound to the launched payload.
Progress and cancellation remain explicitly unavailable.

Canonical behavior is defined by the
[client protocol V2 contract](../../../modules/workbench-shell/contracts/client-protocol-v2.md)
and [schema](../../../modules/workbench-shell/schemas/client-protocol-v2.schema.json).
