# Atlas observation session V1

```text
workbench atlas observations session GRAPH
```

This command verifies one observation graph, then answers serial requests using
the same `ObservationView` and SQLite connection. Core owns the command's process
and package lifetime. The session starts no detached daemon, endpoint or durable
store and requires no Shell, Axiom or pack profile for graph queries. Separate
command processes have separate views; there is no cross-process cache.

Input and output are UTF-8 JSON Lines. Standard input must be a pipe or file.
Standard output contains only protocol records, including when `--json` is
omitted. Standard error announces cold graph verification and carries terminal
errors. Opening can take substantially longer than subsequent queries; readiness
is emitted only after authoritative streams and the query index are verified.

## Ready record

```json
{
  "format": "workbench-atlas-observation-session-ready-v1",
  "schema_version": 1,
  "state": "ready",
  "graph_set_id": "GRAPH_ID",
  "context": {},
  "operations": ["context", "search", "inspect", "relationships", "evidence", "crafting-exposure", "close"],
  "maximum_request_bytes": 1048576
}
```

`context` is the complete observation context V1, not the empty placeholder above.
It preserves exact graph identity, authority, evidence binding, coverage and
limitations. It does not upgrade incomplete evidence or native-failed outcomes.

## Requests

Each request has exactly these fields:

```json
{
  "format": "workbench-atlas-observation-session-request-v1",
  "schema_version": 1,
  "request_id": "search-1",
  "graph_set_id": "GRAPH_ID_FROM_READY",
  "operation": "search",
  "arguments": {"query": "copper", "limit": 50}
}
```

`request_id` is 1–128 printable ASCII characters. It correlates a response to
this request; it confers no authority, uniqueness or idempotency guarantee.
Every request must name the exact graph identity from readiness. Unknown fields,
operations, mismatched identities and invalid argument shapes are rejected.

| Operation | Required arguments | Optional arguments |
| --- | --- | --- |
| `context` | None | None |
| `search` | `query` | `kind`, `limit`, `cursor` |
| `inspect` | `selection_id` | None |
| `relationships` | `selection_id` | `direction`, `relation`, `limit`, `cursor` |
| `evidence` | `selection_id` | `limit`, `cursor`, paired `snapshot` and `pack_profile` |
| `crafting-exposure` | `selection_id` | `max_depth`, `max_nodes` |
| `close` | None | None |

Arguments and results retain the corresponding [observation query
contract](atlas-observation-query-v1.md). `context` returns `view.describe()`;
it does not repeat the standalone context command's index readiness inspection.
Evidence resolution uses the same optional profile reader and exact reference
validation as the standalone command. It may reopen original snapshot custody;
the session caches neither snapshot readers nor profile evidence results.

Crafting exposure uses [the stored-reference
contract](atlas-crafting-reference-exposure-v1.md). Omitted or null traversal
bounds request the complete finite closure. Explicit positive bounds expose
their truncation/frontier; stored references do not establish recipe matching,
registration provenance or gameplay impact.

## Responses and termination

Every response has `format: workbench-atlas-observation-session-response-v1`,
integer `schema_version: 1`, the session's `graph_set_id`, and `request_id`.

- `state: complete` includes the complete, unchanged owner record in `result`.
- `state: error` includes `error: {code, message}`. `code` is `invalid-request`
  for envelope/argument-shape rejection or `operation-failed` for an unavailable
  selection, invalid query argument, or refused evidence reader. No success
  record is emitted for that request. A following valid request can proceed.
  An invalid correlation identifier is represented as null in the error response.
- `state: closed` acknowledges an explicit valid close request. No further
  requests are read. All records follow request order; no requests run concurrently.

EOF between requests exits successfully and closes the view without an extra
record. Each input frame must end with LF and contain at most 1 MiB excluding LF.
Malformed UTF-8/JSON, duplicate object members, nonfinite numbers, unpaired Unicode
surrogates, oversized frames and EOF inside a frame terminate the session with
an error instead of skipping input. Output retains exact Python JSON numbers;
clients must preserve precision or refuse unsupported integers before presentation.

Cancellation, graph-file drift and fatal I/O/framing errors end the session and
release the view. They are not recoverable request errors. Pipe/file reads poll
Core's cancellation callback while idle, including after a partial request;
pipe output also remains cancellable if its reader stops draining it. Previous
descriptor blocking modes are restored on exit. SIGINT also unwinds the command
through Core. No background input/output worker is created. A short write is
completed before processing another request; failure or cancellation during a
frame ends the session. Clients must discard an unterminated final output frame.

The graph is checked for drift before requests and after successful operations,
including after serialization and immediately before publishing ready/success
records. A changed graph requires a new session and full verification; the
session never silently rebinds a cursor or selection to different evidence.
The input frame bound protects transport parsing. It is not a limit on total
graph size, session query count or complete crafting exploration.
