# Recipe browsing and sessions V1

`workbench atlas recipes browse GRAPH SELECTION --offset 0 --limit 50 --json`
returns `workbench-atlas-recipe-browse-v1`. `--expect-graph` optionally binds the
query to a previously read `graph_set_id`. Changed identity is a refusal.

The record includes the exact graph context and selected node, captured incoming
and outgoing edges with their peer nodes, evidence gaps, scope, and a page with
`offset`, `limit`, `total`, and nullable `next_offset`. Ordering is direction,
relation, and canonical index edge key. Duplicate captured edges are preserved;
a self-reference appears in both directions. Page limits do not limit the graph
or qualify a complete route. Node and edge properties retain original types and
values. Acceptance and observed representatives remain distinct relationships.
Follow input selectors to their finite alternatives; an empty neighborhood does
not establish impossible acquisition. Cycles do not establish craftability.

The native clients use `workbench atlas recipes session GRAPH` to verify once
and reuse the same process-local view. The command owns no daemon, socket or
durable store. Core retains process ownership. EOF closes the session.

The UTF-8 JSONL ready record has format
`workbench-atlas-recipe-session-ready-v1`, `schema_version: 1`, `state: ready`,
`graph_set_id`, `context`, `operations` and `maximum_request_bytes`.
Each request is one newline-terminated object with exactly:

- `format: workbench-atlas-recipe-session-request-v1`
- `schema_version: 1`
- `request_id`: a printable ASCII correlation label of 1–128 characters
- `graph_set_id`: the exact ready identity
- `operation`: `search`, `browse`, or `close`
- `arguments`: `query` and optional `limit` for search; `selection_id` and
  optional `offset`/`limit` for browse; empty for close

Responses use `workbench-atlas-recipe-session-response-v1`, the same schema,
graph and request identities, and `state`. `complete` carries the ordinary
search or browse `result`; `error` carries a diagnostic string; `closed` ends
the command. Requests are serial. Duplicate JSON keys, invalid UTF-8/nonfinite
numbers, oversized framing and unterminated input are refused.

Full content verification happens on open. File and directory witnesses check
replacement and ordinary mutation before/after queries and while sending a
response. Drift invalidates the session; open a fresh session and verify again.
Witnesses are not write locks and do not attest a hostile filesystem that can
forge metadata. No historical session is promoted to current runtime evidence.
