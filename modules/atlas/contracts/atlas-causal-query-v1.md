# Atlas causal path query v1

Status: implemented

This package turns one accepted N01 normalization and one exact C01 request
into a request-bound causal-provenance result. It searches only normalized
relations, validates every reverse-discovered edge against the forward index,
preserves lifecycle and branch structure, applies global request bounds, and
then submits the complete projection to the independent C01 validator.

Artifacts:

- [reviewed closed-path fixture v1](../examples/atlas-causal-query-example-v1.json);
- [C01 causal provenance contract](atlas-causal-provenance-contract-v1.md);
- [N01 normalization handoff](atlas-runtime-provenance-normalization-v1.md);
- [`atlas_causal_query.py`](../src/workbench_atlas/atlas_causal_query.py); and
- [`test_atlas_causal_query.py`](../tests/test_atlas_causal_query.py).

The reviewed fixture is a production-shaped contract example, not accepted
production evidence. It is regenerated from the focused exact-execution
fixture and validates byte-for-byte.

## Admission boundary

`query(normalization, request)` first runs both independent validators. It then
requires exact agreement on:

- snapshot and source-lock identities;
- C01 policy ID and policy digest;
- primary profile and physical side;
- the final runtime node ID, kind, record digest, and capture-evidence ID; and
- every request-global path, node, relation, and evidence bound.

Supporting scopes are admitted only when the request names them. The exact
final node and its capture evidence must fit inside the request bounds. A
request that cannot contain its mandatory endpoint fails instead of emitting
an incomplete result.

## Traversal and closure

Traversal begins at the exact final runtime record and follows only relation
IDs in N01's reverse index. Each relation must also appear under its subject in
the forward index. Paths are simple, directed, and canonical. A cycle is never
silently flattened into a causal chain.

A path closes only when it:

- begins at an admitted `source-span` or `configuration-entry`;
- ends at the request's exact final node;
- has connected subject-to-object relations in order;
- contains at least one `transformation` or `causation` edge;
- contains no `possibility` edge;
- preserves the policy's lifecycle order; and
- ends with `observed_as_final`.

All other retained paths are partial and carry exact open-reason codes and
frontier node IDs. A lifecycle reversal retains only the valid prefix and
reports `lifecycle-order-unresolved`.

N01 operation candidates and frontiers are diagnostic records, not edges.
Static lexical operations whose exact promoted runtime node is absent cannot
enter a path. Therefore a final record disconnected from runtime transition
evidence is `unresolved`; textual target similarity, namespace, configured
stage membership, or coexisting final rows cannot change that result.

## Bounds and result states

The request bounds apply globally across traversal and projection. P03 does
not multiply them per branch. If a path, node, relation, or evidence limit
prevents complete emission, the result is `bounded`, identifies the stopping
bound, keeps an explicit open frontier, and reports at least one omitted
record.

The five C01 states retain their exact meanings:

- `closed`: at least one evidence-closed path and no open or bounded branch;
- `partial`: at least one retained open path and no truncation;
- `bounded`: traversal or projection stopped at a request-global limit;
- `unresolved`: the exact final record exists but no evidence-typed path can
  be retained; and
- `unavailable`: the final endpoint explicitly carries an
  `authority-unavailable` record required for `causal-provenance-query`.

When no closed path exists and evidence capacity permits, P03 emits the
policy-bound `no-closed-causal-path` statement backed by a content-addressed
`causal-validation` record. It never emits that negative statement when even
one closed branch is retained, including a bounded multi-branch result.

C01 v1 intentionally returns one bounded result with
`none-v1-single-bounded-result` pagination. P03 does not reuse M2 continuation
tokens or invent an incompatible provenance cursor.

## Exact projection

The result contains only:

- the mandatory final node;
- nodes and relations used by emitted paths;
- evidence referenced by those nodes, relations, and statements;
- canonical path and statement records; and
- exact counts, reasons, frontiers, and content identities.

Unused graph records and unused evidence are omitted. The C01 validator then
recomputes node/relation/evidence bindings, edge connectivity, lifecycle
order, closure, bounds, negative-statement authority, exact use, and the
result content ID. This keeps the query producer and result validator as
separate failure boundaries.

## Validation

The focused P03 suite covers:

- exact source-to-operation-to-runtime-to-final closure;
- disconnected static candidates remaining unresolved;
- runtime-only observation remaining partial;
- node and evidence truncation becoming explicit bounded results;
- a two-branch path bound retaining one closed branch without a false
  no-path statement;
- explicit authority loss becoming unavailable;
- exact request scope and endpoint admission;
- bidirectional-index and path-connectivity mutations;
- byte-for-byte determinism; and
- reviewed-fixture reproduction.

The reviewed fixture has result ID
`atlas-provenance-result:sha256:7de728af71443ffc6590642b3c33ab7aba99d709d57de9e467999b6159843b50`.
It is a synthetic exact-evidence proof. P03 has not run a game, captured a
world, mutated external evidence, or claimed a production path closed.

## Audience projection boundary

An audience projection may consume the validated C01 result as the sole mechanical source for
player and developer explanations. It may simplify terminology for players,
but it must preserve every meaning-changing mutation, open reason, bound,
scope, and authority limitation. It must not search N01 independently,
reinterpret static candidates, or add facts absent from the validated result.
