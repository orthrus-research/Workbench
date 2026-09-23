# Atlas observation queries V1

Atlas provides family-neutral inspection over verified categorical graphs:

```text
workbench atlas observations context GRAPH --json
workbench atlas observations search GRAPH TEXT [--kind KIND] [--limit N] [--cursor CURSOR] --json
workbench atlas observations inspect GRAPH NODE_ID --json
workbench atlas observations relationships GRAPH NODE_ID [--direction incoming|outgoing] [--relation RELATION] [--limit N] [--cursor CURSOR] --json
workbench atlas observations evidence GRAPH NODE_ID [--limit N] [--cursor CURSOR] [--snapshot PATH --pack-profile PROFILE] --json
workbench atlas observations crafting-exposure GRAPH NODE_ID [--max-depth N] [--max-nodes N] --json
workbench atlas observations session GRAPH
workbench atlas observations index GRAPH [--max-source-bytes N] [--max-index-bytes N] --json
workbench atlas observations import-snapshot PATH --pack-profile PROFILE --output NEW_GRAPH [--side single|baseline|candidate] --json
```

## Authority and scope

The query reads exact retained nodes, edges, properties and evidence references.
It does not interpret a stored selector as an executed match, a registry member
as a successful initialization, an incoming edge as a dependency, or a recorded
relationship as source causation. GT impact retains its separate contract and
does not gain support for new families from these queries.

The context preserves graph identity, graph format and authority, scope, original
evidence binding and summary. `coverage.declaration` is the unchanged producer
declaration at `evidence_binding.coverage`, or null with `state: not-declared`.
The context also retains partition classification, evidence categories and
limitations. Declared coverage is not upgraded to semantic completeness. An
Axiom observation graph preserves its initialization scope and native outcome;
it is not relabeled as a Crucible post-start capture. Historical V2 graphs retain
their original authority and identities.

The Python API is `open_observations(path, check_cancelled=...)`, yielding an
`ObservationView` with `describe`, `search`, `inspect`, `relationships` and
`evidence` methods. The view holds one verified immutable index until closed.
`describe_observations(path, check_cancelled=...)` additionally reports index
readiness even when an explicit rebuild is needed. Queries do not repair storage
implicitly. Index rebuilding changes only disposable storage and its descriptor.

The [JSONL session](atlas-observation-session-v1.md) keeps one verified view open
for linked requests within one Core command. It preserves these query results
and avoids reopening the graph for every selection. The session ends on explicit
close, EOF, cancellation or evidence drift; it is not a detached service.

[Crafting reference exposure](atlas-crafting-reference-exposure-v1.md) follows
recorded native-value references to named crafting recipes within the same
registry. Its default is complete finite exploration; optional explicit bounds
preserve frontiers. This is stored-reference exposure, not inferred matching,
source registration causation or gameplay dependency.

### Reusing a verified view

Opening a view still validates the complete authoritative streams, dependency
closure and the derived index's exact semantics. Reuse does not accept a cached
verification receipt. The view records file identity, mode, size and filesystem
timestamp fields (`mtime_ns` and `ctime_ns`) for the manifest, streams and index before verification,
and checks them again afterward. Every path ancestor must be an ordinary
directory; symlink ancestors and parent traversal are refused.

Before and after queries, the view checks those witnesses and ancestor identities.
Directory timestamps are excluded so unrelated sibling activity does not
invalidate a view. Replacing a file, rebuilding an index, changing a stream or
losing access terminally closes the view with `ObservationChangedError`. Reopen
the graph to verify the new files. Returned context records and `view.manifest`
are detached copies. Explicit close is idempotent; closed views refuse queries.
The view and its SQLite connection belong to the thread that opened them.

These checks detect ordinary filesystem changes; they are not OS write locks or
a guarantee against hostile concurrent writes. Query a retained graph without
concurrently rewriting it. Session reuse creates no persistent cache or history
store and does not change custody of original producer evidence.

## Result contracts

All envelopes have integer `schema_version: 1` and these formats:

- `workbench-atlas-observation-context-v1`: context fields above; the standalone
  context command also includes `query_index` readiness.
- `workbench-atlas-observation-search-v1`: context, exact `query`, nullable `kind`,
  raw canonical node `results`, and `page`.
- `workbench-atlas-observation-inspection-v1`: context, complete canonical
  `selection` node, and incoming/outgoing relationship counts by relation name.
- `workbench-atlas-observation-relationships-v1`: context, complete selected node,
  `direction`, nullable relation filter, and results containing the exact `edge`
  and other endpoint `node`, plus `page`.
- `workbench-atlas-observation-evidence-v1`: context, `selection_id`, recorded
  references and `page`. State is `recorded` or `not-recorded`.
  `original_record_resolution: unavailable-reader-not-selected` means the query
  returns recorded references without reopening their original producer;
  `not-recorded` accompanies an observation without references. Reference
  presence alone is not proof that the originals remain accessible. Explicit
  paired `--snapshot` and `--pack-profile` options request owner validation and
  resolution of this page's references; success is `resolved` with the complete
  owner `resolution` record. Missing readers and misbound results fail explicitly.
- `workbench-atlas-observation-index-v1`: complete operation state, graph identity,
  new query-index descriptor and `authoritative_graph_evidence_mutated: false`.

Canonical node and edge records retain their complete graph schema, including
`id`, `kind`/`relation`, properties and original evidence arrays. No field is
silently dropped, converted to a GT recipe, or rounded for presentation. Family
meaning comes from the admitted projection, not this generic query layer.

## Pagination and cancellation

Search, relationships and evidence pages default to 50 records and accept page
sizes from 1 through 1000. The page size bounds one response, not total accessible
observations. `page` contains `limit`, `offset`, `returned`, `truncated` and nullable
`next_cursor`. Following the cursor exhausts the immutable query without dropping
rows. Search is literal Unicode-casefolded substring matching over semantic keys,
kinds and stored JSON properties, with exact/prefix semantic matches ranked first.

Cursors bind the exact graph identity, operation, selection/query, filters and
page size. They are canonical, digest-checked continuation records, not credentials
or source evidence. Different graphs or query parameters reject an old cursor.
Ordering is deterministic within the verified immutable graph. Missing selections
fail explicitly instead of becoming an empty relationship or evidence answer.

Core cancellation is checked around graph opening, during SQL execution and
before publishing a response. Cancellation produces no successful report. Index
operations reuse the existing progress/cancellation callback. The evidence
commands do not launch native workers or game processes.

## Optional snapshot import

Import resolves the explicit `workbench.observation_graphs` profile extension.
The extension declares integer `PROFILE_API_VERSION = 1` and integer
`OBSERVATION_GRAPH_API_VERSION = 1`, and provides:

```python
project_snapshot(path, output, *, side="single", check_cancelled=None) -> dict
```

The adapter verifies the original snapshot through its owner-supported reader,
preserves side, lifecycle, exact identities, coverage and limitations, and publishes
only to a new output. Single/baseline/candidate selection is explicit; the adapter
must reject a side absent from the retained request. It returns a receipt with
`state: complete`, resolved `root`, and `graph_set_id`. Atlas independently reopens
and validates the authoritative graph and checks the receipt against it.

Missing, disabled, duplicate or incompatible providers make import unavailable.
They do not disable queries of already admitted graphs. No Axiom or Shell
implementation is imported by the observation query package.

The same profile extension may provide
`resolve_evidence(path, references, *, check_cancelled=None) -> dict`. It reopens
the retained source through its owner, checks exact snapshot identity and pointers,
and returns `state: resolved`, `snapshot_id`, and `records` in requested order.
Each record contains the exact input `reference` and its original `value`; other
owner custody fields may be present. Atlas checks reference/count/snapshot linkage
before presenting the result. This is read-only access, never native execution.
