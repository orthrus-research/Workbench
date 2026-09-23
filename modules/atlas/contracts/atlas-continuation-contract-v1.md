# Atlas continuation and composition contract v1

Status: implemented experimental contract

This contract defines portable continuation identity for the Atlas upstream
route and forward recycling traversals. It extends, but does not change, the
accepted M1 query contract. Existing V1 answers remain unchanged unless a
caller explicitly requests a continuation surface.

## Meaning of continuation

A continuation advances serialized traversal state. It is not a new query with
a larger structural limit, an opaque database cursor, or a client-authored list
of unresolved nodes. Every segment binds the same query, algorithm, snapshot,
scopes, exact roots, structural limits, fairness policy, evidence digest, and
byte-exact immutable runtime query projection.

Structural limits remain immutable for the lineage. Segment allocation is a
separate work budget:

- route work admits or disposes one producer occurrence;
- recycling work admits or disposes one candidate at its canonical
  `(depth, rank, exact-target)` position;
- preparation and terminal finalization may consume no work item; and
- cumulative consumption is exactly the parent cumulative amount plus the
  child segment consumption.

This separation prevents a limit increase and fresh rerun from being
misrepresented as continuation.

## Canonical records

The schemas are:

- `atlas-continuation-manifest-v1.schema.json`; and
- `atlas-continuation-delta-v1.schema.json`.

A manifest contains:

- immutable request, algorithm, snapshot, scope, root, structural-policy,
  fairness, evidence, and content-addressed runtime-projection bindings;
- its exact parent digest and ordered ancestry;
- segment and cumulative work accounting;
- cumulative consumed source page ranges, with every child retaining every
  parent range and adding exactly its segment consumption;
- traversal-specific admission, frontier, ancestry, and pending-page state;
- the current canonical result;
- independent state and result digests; and
- explicit validity or ordered invalidation reasons.

The continuation identity hashes the complete canonical manifest other than
the identity field itself. The full manifest digest is separately bound by its
child. Thus changing a frontier, result, origin, evidence record, limit, scope,
or parent changes the identity and invalidates any existing child.

A delta binds one exact parent and one exact child. Its canonical object-tree
patch transforms the parent semantic manifest into the child semantic
manifest. The compositor applies the patch, recomputes all manifest and
binding identities, checks ancestry and cumulative budgets, and rejects replay
when the delta identity has already been admitted.

## Route checkpoint model

The route state records root and descendant queues, exact scoped targets,
expanded and active subproblems, current producer occurrence and offset,
admission and visited accounting, retained routes and dependencies, alternative
page ranges, cycles, and unresolved leaves. A pause occurs only at an atomic
producer-occurrence boundary. Resume begins at the saved occurrence offset and
does not reconsider an admitted occurrence.

Breadth-first root precedence and descendant queue order are part of
`ATLAS-ROUTE-BREADTH-FIRST-V1`. Exact origins and cycle relationships survive
serialization. Structural truncation and procedural or symbolic boundaries
remain open; continuation does not turn them into global negative claims.

## Recycling checkpoint model

The recycling state records exact co-output roots and every origin, final and
retained-route destinations, subproblems, prepared consumer pages and offsets,
same-depth identifier order, current candidate rank, global operation
admission, target reachability, continuation edges, convergence, cycle
ancestry, closures, terminal leaves, unresolved rows, and output bounds.

`ATLAS-RECYCLING-DEPTH-RANK-V1` orders candidates by depth, then candidate rank
across every exact target at that depth, then canonical target identity. A
pause may occur between any two candidates without allowing one root to spend
the global operation budget before peers at the same rank.

## Composition equivalence

For one immutable binding and cumulative work amount, composing the root
manifest and every consecutive delta must equal a fresh traversal by the
continuation primitive given that cumulative work amount:

- identical canonical result bytes;
- identical frontier and admission state;
- identical origins, reachability, convergence, cycles, closures, and
  unresolved rows;
- identical evidence use and deduplication; and
- identical truncation and completeness status.

This is continuation-primitive equivalence, not permission to answer resume by
rerunning the V1 query. Production implementations must advance decoded state.

## Invalidation and rejection

Validation fails closed for:

- changed query, algorithm version, snapshot, runtime-projection digest,
  scopes, roots, structural limits, fairness policy, or evidence digest;
- wrong or missing parent, nonconsecutive segment, fork presented as a child,
  or changed ancestry;
- malformed or overlapping page accounting;
- cumulative budget drift;
- state, result, frontier, manifest, child, or delta digest mismatch, including
  a result/frontier that cannot be derived again from its portable state;
- conflicting or replayed deltas; and
- a complete claim with a nonempty resumable frontier.

Snapshot replacement creates a new root lineage. It never resumes an old one.
Unavailable evidence invalidates the lineage with a named reason; it does not
silently downgrade authority.
