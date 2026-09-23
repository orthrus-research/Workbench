# Crucible revision-pinned query kernel V2

Status: executable, store-independent revision-pinned query kernel. Repository
closure validation, indexes, service integration, and authorization are
separate concerns.

## Purpose

This contract defines the product-generic seam that lets an authority query an
already verified immutable Crucible graph bundle without moving query meaning
into Crucible. It replaces test-local member unpacking with one bounded path:

```text
caller-validated immutable revision pins
  -> validation and isolation of exact rows, objects, IDs, and parameters
  -> one explicitly supplied owner query port
  -> canonical semantic answer bytes plus exact inspected-row IDs
```

Crucible owns validation of the supplied row and object closure, immutable
transport, content-address and context checks, bounds,
query/implementation/owner binding, parameter hashing, and result-byte
hashing. The caller owns graph-set, revision, and publication relationship
validation before invocation. The supplied port owns selection, traversal,
interpretation, wording, limitations, and all other query semantics.

## Kernel input

`PinnedGraphQueryInput` contains:

- one exact `ContextRef` ID;
- one immutable graph-set revision ID and one publication identity supplied by
  the caller;
- one to sixteen unique role-keyed graph revision IDs with canonical C01 graph
  rows; and
- zero or more exact object descriptors paired with their immutable bytes.

`PinnedQueryKernelConfig` binds one content-addressed query algorithm, one
content-addressed implementation, its exact owner authority/revision/adapter,
and record/input/result ceilings. Query parameters are one canonical JSON V2
object. No path, active workspace, current reference, environment variable,
clock, cache, or adjacent graph is an implicit input.

Before invoking the port, the kernel reopens every graph row, requires one
shared exact context, rejects duplicate record IDs and role-local logical keys,
checks every descriptor/object digest and length, enforces the declared bounds,
and rebuilds an isolated read-only view.

The publication identity is bound into the input closure and result. The
kernel does not dereference it or prove that it published the supplied graph
set; that relationship must already have been validated by the caller.

Parameter bytes, graph-record count and bytes, immutable-object count and bytes,
and aggregate closure bytes are checked at the first offending item before C01
parse, hash, copy, or owner callback. The hard object ceiling and configured
record/input ceilings cannot be raised by a caller into an unbounded in-memory
walk.

## Owner result

The port returns an exact `PinnedQueryOwnerResult` containing:

- one non-empty canonical JSON object within the result bound; and
- a unique set of inspected graph-record IDs drawn only from the pinned input.

The kernel returns a `PinnedQueryExecution` that retains context, graph-set,
publication proof, role-to-revision pins, algorithm, implementation, owner,
parameter SHA-256, sorted inspected rows, canonical result bytes, and result
SHA-256. It also hashes the complete member-row/object/parameter/query binding
as `input_closure_sha256`, so even an allowed extra immutable object cannot
become an invisible semantic input. This execution value is not itself a
published C01 record or an Atlas answer format.

Port exceptions are wrapped as kernel failures. A port cannot forge a kernel
diagnostic, report inspection outside the supplied closure, mutate the isolated
view, return noncanonical JSON, or exceed its bounds.
The inspected-row value must be an exact tuple no larger than both the hard
record ceiling and the supplied graph closure; this count is checked before
element scans, set construction, sorting, copying, or result parsing.

## Integration boundary

Callers invoke `execute_pinned_graph_query` with an exact
`PinnedGraphQueryInput`, bounded `PinnedQueryKernelConfig`, canonical parameter
bytes, and one explicit owner port. No application-store adapter or built-in
semantic owner port is part of this contract. Repository lookup, publication
validation, capability selection, and presentation remain outside the kernel.

## Exclusions

This contract does not provide:

- query discovery, registration, leases, cancellation, continuations, indexes,
  caching, a catalog, transport, or a persistent host;
- generic arbitrary graph-set reading or hostile-code sandboxing;
- semantic authority, graph compatibility, construction approval, or an action
  gate in Crucible;
- current Cleanroom, worldgen, mechanical-route, player-state, or general
  Supersymmetry evidence; or
- a catalog, continuation protocol, or full-graph build.

The focused conformance cases cover exact repeatability, immutable isolation,
context and canonical-parameter rejection, record/input/result bounds,
descriptor/object tamper, owner failure wrapping, and inspected-row closure.
The adversarial cases additionally prove that malformed oversized inputs and
an oversized duplicate inspected-row result reject before parser or
hash/equality dispatch.
