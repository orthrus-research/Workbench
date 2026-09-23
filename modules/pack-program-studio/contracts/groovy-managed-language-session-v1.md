# GroovyScript managed language session V1

Status: working experimental local lifecycle contract

This contract turns the existing exact-diagnostics broker into a managed
developer touch point. `workbench groovy session` starts GroovyScript 1.4.3's
real embedded language server in one exact disposable physical client, proves
that the upstream compiler is ready, then hands the same endpoint to a terminal,
IntelliJ, or VS Code client. Workbench does not implement a second Groovy
compiler or silently select a personal launcher instance.

The versioned records are the
[`managed session profile`](../schemas/workbench-groovyscript-managed-session-profile-v1.schema.json),
[`ready descriptor`](../schemas/workbench-groovy-language-session-descriptor-v1.schema.json),
and [`final receipt`](../schemas/workbench-groovy-managed-language-session-v1.schema.json).
The Cleanroom GroovyScript 1.4.3 Prism policy lives under the platform profile,
not in a Supersymmetry branch inside the lifecycle engine.

## Admission and exact custody

V1 accepts a completed `workbench-runtime-launch-receipt-v3` for a
Workbench-created disposable Prism projection. It rejects a receipt unless:

- the prior physical client has an observed exit;
- the launcher family, command, executable bytes, application root, instance
  ID, projection, host OS, and optional Java executable remain available;
- the instance ID uses the receipt-bound `workbench-` disposable namespace;
- the supplied runtime is exactly that projection's `.minecraft` directory;
- the source `runConfig`, GroovyScript JAR, complete bounded mod graph, class
  cache, optional Java, and launch receipt pass the existing exact inventory;
  and
- no session lock or pre-existing matching Windows Java process is present.

The launch receipt is an explicit custody input, not authentication data that
the upstream TCP protocol can present. V1 therefore reports
`managed-launch-custody; no-upstream-identity-challenge`, never cryptographic
endpoint identity.

## Checked temporary overlay transaction

Workbench reserves a free public loopback port before changing the projection.
For a direct launch, that is also the GroovyScript port. When Workbench runs in
WSL and the receipt launches Windows Prism, it additionally selects and checks a
distinct free Windows upstream port. This avoids the `wslrelay.exe` mirror
occupying the JVM's listener while retaining one public `127.0.0.1:port` for
terminal and IDE consumers. Workbench then reads two regular, non-symlink files
with bounded size and exact byte preconditions:

1. Prism `instance.cfg`, where it adds or replaces only
   `-Dgroovyscript.run_ls=true` and enables the instance's explicit JVM-argument
   override; and
2. GroovyScript's Forge config, where it replaces exactly one integer
   `languageServerPort` property with the direct or Windows upstream port.

Original bytes are retained privately in the fresh session directory. Writes
are same-directory atomic replacements preserving file mode. If either
precondition changes, application stops. On shutdown, an unchanged file is
restored byte-for-byte. If Prism or Forge rewrote unrelated fields, Workbench
performs a checked three-way field merge: every owned field must still be
semantically equal to either the applied or original value, only those fields
are restored, and unrelated live bytes are preserved. This is recorded as
`restored-merged`. Any third value in an owned field is a `conflict`; Workbench
preserves the backup and refuses to overwrite it. A `complete` receipt requires
both overlays to be `restored-exact` or `restored-merged` with no owned-field
conflict.

## Readiness, handoff, and the single-client boundary

A listening socket is insufficient. Workbench connects as a bounded LSP client,
requires a valid `initialize` result with completion, hover, signature, and
GroovyScript texture-decoration capabilities, publishes a deliberate syntax
error, observes the matching severity-one diagnostic, replaces it with a clean
in-memory document, observes an explicit empty diagnostic set, and performs a
clean LSP shutdown.

GroovyScript 1.4.3 accepts one client, waits for that connection to close, then
reopens the same port for the next client. The readiness client therefore
disconnects before Workbench publishes
`workbench-groovy-language-session-descriptor-v1`. The descriptor binds the
source/program, runtime/receipt, profiles, local and server workspace URIs,
public route and upstream endpoint, capabilities, canary transcript, retained
paths, connection model, and supported consumers.
`--json-events` places the complete descriptor in the `ready` JSONL event so an
IDE plugin can consume the contract without scraping terminal prose.

The descriptor exposes one local LSP route. On direct hosts it reaches the
upstream socket. Across WSL/Windows, a bounded same-session bridge accepts the
public connection and starts a Windows stdio helper that connects the distinct
upstream loopback socket. Native Windows clients reach the same public port
through WSL's loopback relay; WSL clients reach the bridge directly. The
descriptor's `workspace_uri`/`server_workspace_uri` pair makes path translation
explicit rather than sending `/mnt/...` URIs to a Windows JVM. Consumers connect
only to the top-level endpoint, never the implementation-level `upstream`.

Only one terminal or IDE client may be active at once. The bridge routes one
connection; it does not multiplex or weaken GroovyScript's sequential-client
boundary. A future multiplexing proxy would require a new transport contract.

## Cancellation and shutdown

The Workbench process remains the session owner. SIGINT/SIGTERM, the configured
session deadline, or observed client exit starts cleanup. POSIX launches use a
new process session and bounded TERM/KILL escalation. Windows Prism launches
inventory Java processes by the exact disposable instance ID before launch and
bind newly observed JVMs by that ID or ownership of the configured upstream
port.
Every action revalidates PID, process creation time, and executable path before
requesting `CloseMainWindow` or force-stopping after the grace period. The Prism
launcher process is also reaped.

Every transition is fsync-retained in a JSONL journal. The final content-addressed
receipt binds the command, process identities, endpoint allocation and route,
local/server URI mapping, readiness, descriptor, overlays and restore results,
shutdown path, orphan inventory, and event-journal hash. `complete` requires
exact process ownership, at least one owned client identity, zero orphans, and
both owned-field overlay restorations. Launch failure, failed compiler
readiness, external overlay conflict, changed lock, or surviving owned process
is `blocked`; none is explained away as partial success.

## Authority and horizon

The compiler canary is GroovyScript canonicalization evidence. It does not run
pack scripts, observe effective registries, authorize source construction, prove
reload idempotence, or decide save compatibility. Atlas remains observed and
derived runtime authority; Blueprints remains construction authority.

The descriptor is the stable seam for the IntelliJ and VS Code stretch goal.
Those plugins should start or attach to this session, consume its capabilities
and receipts, and present Workbench actions. They must not spawn an untracked
Minecraft client, replace the compiler, or invent a separate acceptance path.
The first native clients use the shared
[IDE surfaces contract](../../../clients/contracts/workbench-ide-surfaces-v1.md).
IntelliJ reaches this socket through `workbench groovy proxy`, a descriptor-
validating byte bridge required by its supported stdio LSP process model; the
bridge neither parses language messages nor takes session custody.
