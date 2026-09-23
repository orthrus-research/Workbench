# Atlas runtime recipe comparison V1

`workbench-atlas-runtime-recipe-comparison-v1` compares two explicit,
independently verified categorical graph V2 bundles as ordered `before` and
`after` observations. Atlas owns the report and its uncertainty. The caller
does not gain a second approval or truth path.

The public Python entry point is:

```python
compare_runtime_recipe_graphs(
    before_view,
    after_view,
    *,
    max_recipes=100_000,
    max_recipe_deltas=500,
    max_resources=500,
    max_depth=4,
    max_nodes=2_000,
)
```

Both arguments must be open `GraphRecipeHealthView` instances. Each view has
already verified the authoritative JSONL bundle and the derived SQLite query
index through the categorical graph V2 owner.

## Compatibility gate

V1 computes no semantic delta unless all of the following are true:

- the complete graph `scope` objects are equal;
- each evidence binding resolves to exactly one `adapter_profile_sha256`;
- those adapter profile hashes are equal;
- each evidence binding resolves to exactly one canonical `gt-recipes`
  category result;
- each result is a digest-bearing complete `transformation-recipe` capture at
  `post-start-end-tick`, its record count equals the graph recipe count, and
  the two category/checkpoint identities agree; and
- both graphs contain finite `gt-recipe` nodes.

Failure produces a sealed-shape report with `compatibility.state` equal to
`incomparable`, `recipe_signatures.status` equal to `not-compared`, no resource
or progression delta, and an explicit unknown such as
`adapter-protocol-mismatch`. V1 does not compare hashes emitted by capture
protocols whose equality is unproven.

Different `input_manifest_sha256` values are retained as context, not treated
as incompatibility: a before/after experiment normally changes an input. The
graph pair alone does not prove which input caused an observed difference.

## Exact signatures and correspondence

One exact finite recipe occurrence is identified by:

```text
recipe_map | semantic_sha256 | duplicate_ordinal
```

V1 reports exact multiset membership as unchanged, removed, or added. The
semantic hash is itself immutable, so a signature cannot be both the same
signature and a changed signature.

The current capture protocol does not publish a stable recipe-occurrence key
across launches. Consequently:

- `change_correspondence.changed_recipe_pairs` is always empty;
- a report containing both removals and additions uses
  `unavailable-no-cross-capture-occurrence-identity`; and
- Atlas does not pair records by list ordinal, source line, recipe-map
  proximity, I/O similarity, or matching counts.

`same_signature_observation_deltas` are limited to non-identity node metadata
that differs while the exact signature remains equal. They are projection or
membership observations, not changed-signature pairs. A verified
`lookup_active` transition is included in resource portfolio analysis because
it changes whether the same exact signature is an available finite producer;
missing or non-boolean activity state prevents availability classification.

## Resource and structural exposure

V1 inspects resources directly used or produced by the bounded emitted recipe
deltas. A resource flow row retains before and after presence, lookup-active
finite producer IDs, lookup-active finite consumer IDs, all related recipe
IDs, unknown lookup-activity IDs, and exact membership additions/removals.

`newly_without_observed_finite_producers` is true only when both portfolios are
complete inside the bound, the before lookup-active producer set is nonempty,
and the after set is empty. Missing or non-boolean `lookup_active` evidence
prevents that classification.

Starting with those exact resources, the after graph is traversed using the
same conservative finite-recipe rules as recipe impact:

- a recipe is an at-risk candidate when at least one exact selector accepts
  only resources already classified at risk; and
- an output resource is an at-risk candidate when all of its observed
  lookup-active finite producers are at-risk recipe candidates.

The result is a structural exposure candidate, never a gameplay dead-path
conclusion.

## Cycles and progression signals

Cycle scanning follows observed exact input acceptance and output relations,
bounded by depth and node limits. `introduced_with_added_recipes` contains
cycles that include an added exact occurrence in the after graph;
`removed_with_removed_recipes` contains cycles that included a removed exact
occurrence in the before graph. The activated/deactivated same-signature arrays
cover verified `lookup_active` transitions without calling them signature
changes. These are dependency-cycle signals, not proof that a cycle is usable,
self-sustaining, or progression-relevant.

`cycle_signals.remaining_producer_dependency_cycle_signals` examines active
producers remaining after an affected resource loses an active producer. Both
resource portfolios must have complete lookup-activity evidence within the
bound. Each signal retains the remaining `recipe_id`, the observed cycle
`path`, its `affected_resource_ids`, and `viability_effect: "unknown"`. These
cycles may already have existed before the removal. They are separate from
introduced cycles, and neither remove a producer from the observed portfolio
nor prove independent supply, unavailability, or a usable alternative route.
The array is empty for incomparable reports, with status `not-compared`;
`summary.remaining_producer_dependency_cycle_signal_count` is then null.

For affected resources, V1 compares exact BetterQuesting requirement links.
When an after resource newly has no observed finite producer, its exact quest
requirements and bounded structural prerequisite dependents are reported.
Prerequisite cycles are definition structure only. Atlas does not execute task
matching, completion, rewards, or player progression.

## Bounds, frontiers, and gaps

V1 has independent bounds for recipes scanned per graph, emitted recipe delta
details, affected resources, traversal depth, and visited graph nodes. A scan
bound prevents partial exact-membership claims. Detail and traversal bounds
retain valid emitted rows and add explicit `frontiers`; `summary.truncated`
then becomes true.
Reaching the depth bound at a terminal resource does not create a frontier;
a depth frontier requires an eligible consumer expansion beyond the bound.

Every report retains these evidence gaps:

- graph-pair differences are not source-change attribution;
- non-recipe acquisition domains are not alternative producers;
- dynamic recipes are not executed;
- player reachability is not assessed; and
- BetterQuesting task execution is not invoked.

Retained graph projection limitations are also forwarded as
`graph-projection-limitations` gaps with `side: "before"` or `side: "after"`.
An inspected selector marked `acceptance_complete: false` cannot establish a
complete alternative set; its omitted acceptance produces a frontier and is
excluded from structural exposure classification.

## Improvements exposed by V1

V1 identifies four system improvements needed for stronger future reports:

1. Publish a category-level semantic encoder/protocol identity. The current
   graph exposes a whole adapter-profile hash, so adding an unrelated adapter
   can make otherwise comparable recipe observations fail closed.
2. Publish a stable cross-launch recipe occurrence/correspondence key bound to
   a source declaration or retained transaction operation. That would permit a
   future format to classify an observed before/after pair as one changed
   recipe without heuristic matching.
3. Carry an experiment-pair binding into each graph: baseline and candidate
   locks plus the owner-validated plan, application receipt, and capture-run
   identities. An opaque input-manifest digest is not enough to attribute a
   runtime delta to one recipe edit.
4. Add an owner-verified, graph-ID-bound validation receipt or comparison
   session cache. Opening two current full graphs repeats expensive immutable
   bundle verification, so IDE presentation needs visible progress and
   cancellation until safe verification reuse exists.

Neither improvement changes V1 semantics. Adding authoritative
correspondence is a new format decision rather than a reinterpretation of V1.
