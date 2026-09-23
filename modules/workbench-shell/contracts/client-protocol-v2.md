# Workbench client protocol V2

Status: experimental executable bootstrap contract

## Purpose

The Workbench client protocol gives CLI and native IDE clients one
surface-neutral process boundary. It carries Workbench domain records and
capability state; it does not carry editor view models or create a new
authority.

V2 is a Workbench-native format. JSON-RPC itself remains version `2.0`;
Workbench negotiates a separate protocol major and minor version.
The current server version is `2.2`. Minor `1` added the optional
`runtime/diagnose` capability. Minor `2` advances that method to the V2
diagnosis result with exact checkpoint and artifact-provenance semantics. A
`2.0` client remains compatible and discovers the method from the returned
capability list; clients consuming its result must inspect the result format.

## Transport

The bootstrap host reads and writes UTF-8 JSON-RPC messages framed with an
ASCII `Content-Length` header:

```text
Content-Length: <UTF-8 byte count>\r\n
\r\n
<JSON payload>
```

Standard output contains framed protocol messages only. Human-readable
lifecycle and failure logs go to standard error. The host opens no socket.

The core distribution and the initialized project are separate roots. The
component registry and selected profiles belong to the Workbench distribution;
source, Packwiz files, scripts, and resources belong to the developer's
project. A project is never required to copy Workbench metadata into its
checkout merely to initialize a session.

The bootstrap limits a message to 4 MiB. Invalid or incomplete framing is a
fatal transport failure because the next message boundary is no longer
trustworthy. A framed but malformed JSON payload receives JSON-RPC parse error
`-32700`, after which the host can continue.

## Session lifecycle

```text
new --initialize--> initialized --shutdown--> stopped
```

- `initialize` must be the first request and may occur once.
- The V2 bootstrap accepts exactly one `file:` workspace root.
- A protocol major other than `2` is incompatible.
- A minor difference is accepted only through the returned method and feature
  capabilities.
- `workspace/inspect` is available only after initialization.
- `runtime/plan` and `runtime/diagnose` are available only after
  initialization and remain read-only.
- `shutdown` returns JSON `null` and stops the host after its response.
- Notifications do not change bootstrap state. Cancellation and progress are
  advertised as unavailable in this slice.

The client may name `required_methods` during initialization. Initialization
fails when the server cannot supply every required method.

## Methods

### `initialize`

The request supplies:

- client ID, kind, and version;
- Workbench protocol major and minor;
- one workspace root URI;
- the execution-host kind and optional authority; and
- optional required methods.

The result supplies:

- negotiated protocol version;
- core name, version, exact source-tree digest, and verification state;
- echoed client and workspace binding;
- supported method operation classes;
- unavailable methods with reasons; and
- progress and cancellation feature state.

The current source-tree distribution is digest-bound but not signed, so its
`verified` field is `false`.

### `workspace/inspect`

The request takes an empty object. The result is the existing
`workbench-foundation-bootstrap-v2` record, including exact Git, native
Packwiz project, source loader, target platform, pack, component, and client
context.

Inspection is read-only and accepts no alternate path. The initialized
workspace is the only target.

### `shutdown`

The request takes an empty object. The result is JSON `null`. Shutdown changes
only the process session and performs no repository mutation.

### `runtime/plan`

The request supplies a physical side and compatible launcher:

- `client` with `prism` or `multimc`; or
- `server` with `dedicated-server`.

The result is a
[runtime plan V1](runtime-plan-v1.md). Planning is read-only and may return
`blocked` with exact reasons. It does not create the named fixture.

### `runtime/diagnose`

The request supplies one local launch-receipt URI and may supply local
artifact-root URIs. The initialized workspace supplies the project and pack
profile context. The result is a
[runtime diagnosis V2](runtime-diagnosis-v2.md).

Diagnosis verifies retained evidence and artifact provenance, inspects
implicated JAR/class metadata, preserves the receipt's exact checkpoint and
limitations, and may match exact pack-profile guidance. A `blocked` result is
a successful diagnosis of a broken runtime, not a protocol error. The method
does not patch an archive or change source, materialized, or launcher state.

## Errors

The host uses standard JSON-RPC error codes where applicable:

| Code | Meaning |
| --- | --- |
| `-32700` | Framed payload is not valid JSON |
| `-32600` | JSON-RPC request envelope is invalid |
| `-32601` | Method is unknown or unavailable |
| `-32602` | Method parameters are invalid |
| `-32603` | Unexpected internal failure |
| `-32001` | Workbench protocol major is incompatible |
| `-32002` | Session is not initialized |
| `-32003` | Session is already initialized |
| `-32004` | Required capability is unavailable |
| `-32010` | Workspace inspection or component validation failed |
| `-32020` | Runtime planning could not produce a result |
| `-32021` | Runtime diagnosis could not produce a trusted result |

Server-specific errors include a machine-readable `kind` and Workbench
availability state without exposing a traceback or private payload.

## Contract artifacts

- [client-protocol-v2.schema.json](../schemas/client-protocol-v2.schema.json)
- [workspace context V2](../../project-intelligence/contracts/workspace-context-v2.md)
- [client protocol conformance](../../../tests/conformance/client-protocol/README.md)
