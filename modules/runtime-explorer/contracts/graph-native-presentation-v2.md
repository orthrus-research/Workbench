# Workbench Runtime Explorer graph-native presentation V2

Status: implemented experimental embedded presentation

## Purpose

V2 presents one exact registered M4 V01 `graph/query` owner result without
turning it into Exact Runtime Explorer V1 records. The owner result remains the
machine-readable answer. Runtime Explorer adds only a deterministic,
control-safe terminal view over those same validated bytes.

This is a graph-native successor, not a provider adapter. It does not recreate
the retired Observatory-bundle receipt projection, assign authority from
category names, translate V01 rows into the V1 evidence-state model, or claim
legacy result equivalence.

Implementation:

- `workbench_runtime_explorer.graph_query`;
- `EmbeddedGraphQueryPresenterV2` and `EmbeddedGraphQueryResultV2`;
- `execute_embedded_graph_query_v2`;
- `graph_query_presenter_manifest_v2`; and
- `render_embedded_graph_query_result_v2`.

The supported symbols are also exported from the Runtime Explorer package
root. No CLI, Shell-catalog, local-service, stdio, or remote route is exposed.

## Producer-bound route

The current producer binding determines the route identity. There is no global
source-checkout hash or compatibility fallback. Its manifest format is
`workbench-runtime-explorer-graph-query-presenter-v2`, and its closed material
binds:

- the exact current registry, distribution and source-tree identities;
- the current capability at semantic version `1.0.0`, handler and implementation;
- embedded invocation of method `graph/query`;
- accepted operation `query`, forbidden comparison requests, and fallback
  policy `none`;
- result format `workbench-world-studio-proving-view-result-v1`, kind
  `world-studio-proving-view-result`, and schema
  `workbench://schemas/crucible/crucible-world-studio-proving-view-v1.schema.json#/$defs/result`;
- the exact request, result, row, depth, and node resource bounds stated below;
- JSON output `exact-owner-result-bytes-with-lf` through sink policy
  `single-write-binary`; and
- terminal output `deterministic-control-safe-view`, maximum `16777216` bytes,
  with sink policy `single-write-after-complete-validation`.

A change to the producer source, native dependency closure or presenter route
material derives a new identity. Installed packages bind their actual native
metadata; an explicit source checkout binds its declared source environment.
Neither is substituted for the other, and historical evidence is not rewritten.

## Trusted embedded execution

The trusted host calls
`workbench_shell.world_studio_registry.build_world_studio_presenter_binding(handler)`.
This factory reconstructs the registry from its own loaded resource root,
creates the genuine owner registration and mints a Crucible-owned
`WorldStudioPresenterBindingV3`. It accepts neither a supplied bundle nor an
alternate source root. The runtime must install `binding.registration`; the
presenter accepts `EmbeddedGraphQueryPresenterV2(runtime, binding)` and the
manifest is available through `graph_query_presenter_manifest_v2(binding)`.
Explorer imports the owner contract, not Shell.

The binding is a non-serializable in-process custody capability, not a Python
security sandbox. An arbitrary self-hashed bundle cannot mint it through the
public API. Before dispatch the presenter checks its minted fingerprint,
exact runtime registration identity, and the sealed registry,
distribution, source tree, dependency lock, health receipt, provider,
capability, registration, exact handler type, implementation, mutation
boundary, binding modes, known schemas/method and owner request/result
validators. Replacing the registration or any resolver, or altering the bound
composition even with recomputed hashes, fails closed.

The request is detached through canonical JSON and must be an exact query
operation with no comparison request. Its context, input binding, graph-set
revision, proof index, action-gate receipt, and query are bound back to the
returned owner result. A bare result mapping, saved RPC response,
caller-selected endpoint, or validator-valid result from a different request
is not accepted.

## Bounds and failure behavior

The route manifest distinguishes the enforcement phases:

- before service dispatch, the detached request is limited to 64 KiB, 8,192
  JSON nodes, and depth 64; and
- after owner-service validation, presenter admission independently limits the
  canonical owner result to 3 MiB, 256 query rows, 64 KiB per canonical row,
  8,192 JSON nodes, and depth 64.

The bound source-derived owner implementation also preflights its result
before canonical owner validation: 4 MiB for the owner result, 256 query rows,
64 KiB per query row, 8,192 JSON nodes, and depth 64. Both layers reject
non-JSON values and non-string object keys before unbounded canonicalization.
Every row category must appear in the validated owner result's own
`authority_owners` table, and repeated row identities fail closed.

Composition or registration drift, malformed data, request/result replay,
owner validation failure, and an owner non-success raise before any output
result is minted.

No legacy fallback occurs.

## Exact JSON and terminal presentation

`EmbeddedGraphQueryResultV2` is constructed only from the canonical bytes of a
validated result returned by the trusted embedded dispatch. `write_json`
accepts only a binary sink, constructs the exact canonical owner bytes plus LF,
and submits that complete payload through exactly one sink call. A text sink or
short binary write fails explicitly. This is presenter-boundary single-call
atomicity, not a claim about filesystem durability. It adds no Explorer
envelope, record, evidence state, grouping, ranking, or authority table.

The terminal renderer preserves sealed owner row order, sorts owner categories
by exact UTF-8 identity, and prints the route, owner-result and graph-set
identities, exact query, support states, the owner result's
category-to-authority declarations, and bounded row navigation. Every
rendered value passes the shared Shell terminal sanitizer. The complete view
must fit 16 MiB before it is submitted through one text-sink call. Rendering
is a non-authoritative view; the canonical owner result remains the answer.

## Legacy disposition and custody

Under [Decision 0002](../../../docs/decisions/0002-v1-compatibility.md), the
Runtime Explorer dispatch for exact format
`workbench-crucible-worldgen-observatory-bundle-v1` is
`retired-unselected`. Passing that format to the generic receipt provider
fails explicitly and directs the caller to the embedded V2 graph/query
presenter. Generic receipt routing and every unrelated receipt family remain
in scope on their existing paths.

The underlying Worldgen Observatory V1 bundle format, schema, capture contract,
validator, and owning Crucible tests remain `retained-evidence`. Their bytes,
identities, validation, and historical claims are unchanged. Retiring one
Runtime Explorer projection does not retire or reinterpret that evidence.

The presenter is read-only. Crucible retains graph custody. Runtime Explorer
acquires no writer lease, moves no publication reference, writes no graph or
index, opens no raw Observatory archive, and creates no alternate truth or
approval path.

## Nonclaims

This contract does not claim equivalence with the removed V1 projection,
restore legacy event or coordinate search, promote an Explorer V1 result,
admit Atlas semantics, change profile support, authorize construction or world
mutation, expose a public service endpoint, sign an offline export, or make
this experimental surface a supported product claim. Executable tests are the
current implementation evidence.
