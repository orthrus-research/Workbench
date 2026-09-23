# Crucible bounded computed-graph materializer V2

Status: active execution contract.

## Purpose and boundary

`workbench_crucible_materializer` executes one bounded graph recipe over one
exact admitted evidence snapshot. It validates the recipe, evidence revision,
admission, payload, execution binding, and implementation registry; runs the
selected built-in implementation in fresh worker processes; seals the returned
semantic values as graph records; and delegates deterministic shard assembly to
the Crucible graph-shard kernel.

The materializer owns no store, mutable reference, publication transaction,
query service, profile discovery, or semantic authority. It does not admit
evidence or decide what a graph means. Callers supply already-validated records
and owner decisions, and receive immutable candidate records, blobs, and shard
assembly.

## Accepted input

`MaterializationInput` carries:

- one materialization recipe;
- one evidence-set revision with exactly one effective evidence record and one
  effective admission record;
- the exact evidence and admission partition descriptors and bytes;
- the effective evidence and admission records; and
- one canonical-JSON payload descriptor and its exact bytes.

The admission must name the evidence, have outcome `admitted`, be eligible for
materialization, and share the evidence and revision context. Every supplied
record and blob is reloaded or verified before execution. Noncanonical,
oversized, mismatched, rejected, quarantined, or incomplete input fails closed.

The selected recipe step and output contract must agree with the registered
implementation and evidence kind. Subject-identity and graph-record schemas
must be distinct. The installed bounds cap aggregate input, payload, worker
output, execution time, result records, source-row references, and records per
shard.

## Execution binding and registry

Each implementation is admitted through a content-addressed
`RecipeExecutionBinding`. The binding covers the recipe and implementation,
selected execution artifact, dependency lock, runtime environment, resource
limits, handler and capability, recipe-owner authority, derivation step, output
contract, schemas, component identities, worker protocol, and numeric bounds.

`snapshot_recipe_implementation_registry` revalidates at most 32 registrations,
sorts them deterministically, rejects duplicates, and returns an immutable
registry root. `execute_materialization` accepts either that snapshot or a
mapping that can be converted to it. There is no implementation discovery or
fallback.

The retained public capability set consists of:

- one synthetic before-relation trial; and
- five closed worldgen branches: capture health, occurrence, stability,
  generative, and realized.

There is no generic profile graph capability or arbitrary declarative bundle.

## Fresh-worker isolation

Qualification runs the same request in two fresh Python workers under two
closed locale and time-zone profiles. Execution runs a third fresh worker. The
parent and child compare the content-addressed execution binding and installed
handler, and the parent rejects disagreement between either qualification run
or the final execution.

Workers receive canonical value-only input through standard input. They do not
receive a store, reference resolver, authority callback, lease, or application
object. Their environment, working directory, input, output, and timeout are
bounded. A timeout, nonzero exit, malformed response, unknown capability, or
semantic mismatch fails closed.

The handler ID is derived from the exact bytes of `_fresh_worker.py`. Dormant
dispatch bytes in that file remain unreachable from the parent capability set
but are intentionally retained as a record-identity anchor. Changing them
requires a new handler identity and coordinated migration of derived records.
This isolation seam is not a hostile-code sandbox or a general determinism
proof.

## Record and shard construction

The worker returns semantic values, never pre-sealed graph records. The parent
validates those values and seals subjects, nodes, edges, properties,
provenance, evidence links, and object descriptors as required by the selected
closed capability.

The graph-shard kernel performs canonical ordering, partitioning, and shard
assembly. Optional prior shards are untrusted cache hints: the full target row
set is always recomputed, each hint is reopened and validated, and bytes are
reused only when the coordinate and all identity-bearing values match.

`MaterializationResult` exposes the exact input and execution identities,
authority bindings, graph-family metadata, sealed graph records and blobs, and
the deterministic partition assembly. Its `publication_fields()` method is a
mechanical projection for a caller; it does not publish or authorize anything.

## Retained conformance

The 18 tests in
[`test_crucible_materializer_v2.py`](../tests/test_crucible_materializer_v2.py)
cover input and binding closure, immutable registry identity, fresh-worker
qualification, bounded failure behavior, synthetic record construction,
worldgen branch selection, deterministic identities, and prior-shard hint
handling.

The seven tests in
[`test_crucible_worldgen_v2.py`](../tests/test_crucible_worldgen_v2.py)
exercise the five owner-separated worldgen branches, capture audit, repeatable
graph sets, fresh-control comparison, and replay rejection.

These are focused local conformance tests. They are not an independent audit,
publication proof, persistent-service qualification, or general recipe-hosting
claim.

## Explicit exclusions

This contract provides no source discovery, evidence admission, arbitrary code
execution, application service, physical object store, graph publication,
mutable reference management, query engine, scheduler, persistent host,
transport, profile adapter registry, full-build orchestration, incremental
publication, or workflow cutover. Those capabilities must be implemented and
validated by a separate owner if they are added later.
