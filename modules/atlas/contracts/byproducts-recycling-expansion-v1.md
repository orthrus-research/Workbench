# Unified Process Atlas byproducts and recycling expansion v1

Status: implemented; exact predicate-filtered consumer pagination, the lossless
byproduct projection, exact root deduplication, and the fairly bounded
forward-consumption DAG are active and evidence-closed

This expansion completes the final active Unified Process Atlas v1 question:

- `PLAYER-BYPRODUCTS-RECYCLING-001`: What byproducts and recycling paths
  exist?

The answer has two mechanically related but semantically distinct sections:

- `byproducts` is a lossless view of non-selected output slots on every
  retained production route; and
- `recycling_paths` is a separately bounded forward-consumption DAG rooted at
  the exact alternatives in those byproduct slots.

Neither section assigns economic value, labels an output as waste, chooses a
preferred use, or claims a closed loop where only a downstream consumer was
observed.

## Fidelity boundary

In this contract, “byproduct” means a mechanical co-output: an output slot on
the selected producer operation other than the slot used to satisfy that
route's target. It does not mean unwanted, low-value, disposable, or safe to
void. Alternatives inside the selected production slot remain alternatives;
they are not reclassified as simultaneous co-outputs.

A forward use becomes a verified recycling closure only when exact runtime
identity and match-path evidence shows that an output returns to one of these
destinations:

1. the final selected target;
2. an exact subproblem target in the retained production-route DAG; or
3. an earlier target in the same forward path, forming a mechanically observed
   cycle.

A consumer that does not reach one of those destinations within the bounds is
an `open-downstream-use`, not a closed recycle loop. A terminal or truncated
path remains visible. Absence of a consumer in a truncated page is never
reported as global absence.

`requires` relationships are reusable conditions, not consumption, and never
start or advance a recycling path. Only exact `consumes` and `may_consume`
occurrences are eligible. Presentation rows, category membership, consultation,
and inferred material similarity are not mechanical forward edges.

Chance and quantity remain local to their exact slots. The answer preserves
every observed amount, formula, chance, and chance scale, but does not multiply
probabilities across stages, derive expected yield, or balance unlike item and
fluid units.

## Shared upstream truth

The existing bounded process-chain query already records, for every retained
route:

- the exact selected production slot and matched alternatives;
- every other `produces` or `may_produce` output slot;
- output grouping and alternatives;
- stage-local amount and chance metadata;
- the owning recipe or procedural rule and execution mechanics; and
- cycles, unresolved leaves, boundaries, and truncation.

The implemented `byproducts` section is projected from those route records
without re-querying or reclassifying them. This guarantees that the player
answer describes the same 50-route bounded choice space as the existing
production, ingredient, reusable-requirement, prerequisite, construction, and
infrastructure answers.

## Byproduct projection contract

`byproducts` contains:

- `status`, copied from the source route projection;
- `target`, copied from the source route projection;
- fixed semantics identifying mechanical co-outputs rather than economic
  waste;
- one item for every retained route, including routes with an empty byproduct
  list;
- the route ID, subproblem ID, scope, producer, selected production, and exact
  co-output slots for each item;
- deterministic summary counts;
- the unchanged source-route truncation object; and
- the exact runtime-mechanics evidence union of the projected records.

Including empty per-route items is important. It proves that the projection did
not silently omit routes with no additional observed output slot. It still does
not turn bounded route coverage into a global “this process has no byproducts”
claim.

Semantic validation must recompute the section from `routes.items` and require
exact equality, excluding only `evidence_ids` while that union is recomputed
separately. The summary contains:

- retained route count;
- routes with one or more co-output slots;
- total co-output slots;
- total returned alternatives;
- guaranteed slot count; and
- conditional slot count.

## Exact recycling roots

Every returned alternative in
`byproducts.items[*].slots[*].alternatives.items` is an exact scoped root.
Roots are deduplicated by profile, physical side, node kind, and runtime node
ID. Each root retains all origin records:

- source route and subproblem IDs;
- producer ID;
- byproduct relationship and slot IDs;
- alternative relationship ID; and
- local amount/chance semantics.

The traversal must use `NodeSelector.by_id` over those already resolved nodes.
It must not reconstruct selectors from display names, fluid names, material
names, or serialized stack text.

## Bounded forward-consumption DAG

The traversal mirrors the upstream route DAG but reverses its question:
starting with a concrete byproduct, which exact operations consume it, and
what exact outputs can those operations produce?

Each forward subproblem contains one exact scoped target, minimum observed
depth, all originating root IDs, and its consumer-operation IDs. Each operation
retains:

- the consumed target;
- owner, input relationship, input slot, all alternatives, and exact matched
  alternatives with match paths;
- consumption semantics, including `may_consume`;
- execution mechanics and procedural boundaries;
- other consumed input slots and reusable requirements without recursively
  treating them as recycling roots;
- every output slot, its grouping, alternatives, amount, and chance; and
- continuation edges to exact output-target subproblems.

The representation is a DAG, not a Cartesian list of path combinations.
Convergent targets share one subproblem and retain every incoming continuation.
A repeated target is a cycle only when the existing graph proves it is an
ancestor of the current operation; otherwise it is convergence.

Closures have one of these exact classifications:

- `returns-final-target`;
- `rejoins-retained-route`;
- `forward-cycle`;
- `open-terminal`;
- `open-procedural-boundary`; or
- `open-truncated`.

An exact identity match can cross material, item, fluid, or unification forms
only through the existing runtime match closure. The closure record retains
the match role and every edge in the match path.

## Fairness and global limits

Forward fanout is highly uneven, so lexicographically exhausting one root
before visiting the next would be deterministic but misleading. The query must:

1. collect and sort all exact roots;
2. obtain separate predicate-filtered counts for consuming
   (`consumes`/`may_consume`) and reusable-only (`requires`) occurrences;
3. page at most `max_consumers_per_target` mechanically consuming occurrences
   per target;
4. admit occurrence rank zero across all same-depth targets before rank one,
   and so on; and
5. share one global operation and visited-target budget across the whole
   traversal.

The initial defaults are:

| Limit | Default |
| --- | ---: |
| `max_depth` | 4 |
| `max_operations` | 250 |
| `max_consumers_per_target` | 25 |
| `max_output_alternatives_per_slot` | 25 |
| `max_visited_targets` | 10,000 |

Truncation reasons are
`max-depth`, `max-operations`, `max-consumers-per-target`,
`max-output-alternatives-per-slot`, and `max-visited-targets`. Per-target page
totals remain visible even when the global budget prevents hydration of every
candidate.

The domain-query prerequisite—exact predicate-filtered consumer pagination—is
implemented. `RuntimeGraphDomainQuery.consumers`,
`RuntimeGraphDomainQuery.consumer_occurrences`, `find_consumers`, and the
`runtime_graph.py consumers` command accept a canonical subset of `consumes`,
`may_consume`, and `requires`. Filtering is applied to occurrence counting,
offset/limit selection, and hydration. The omitted filter retains the original
compatibility payload byte-for-byte; an explicit all-predicate filter produces
the same payload. Invalid, empty, duplicate, non-string, and direction-invalid
sets fail closed.

This prevents reusable `requires` occurrences from occupying a page intended
for consuming operations. Callers can independently request
`CONSUMING_PREDICATES` or `REUSABLE_CONSUMER_PREDICATES`, receive exact totals,
and retain the same deterministic occurrence order.

## Production sizing

The 2026-07-28 `RUNTIME-GRAPH-9093` dedicated-server pilot for
`susy:diluted_oil_light`, using the same depth-eight and 50-route production
answer, establishes the initial scale:

- 50 retained routes, already truncated by `max-routes`;
- 52 co-output slots across 45 routes;
- 49 distinct exact byproduct alternative targets;
- 2 conditional co-output occurrences;
- 46 roots with at least one `consumes` or `may_consume` occurrence;
- 2 roots with only reusable `requires` occurrences;
- 1 root with no observed consumer occurrence;
- 4,452 immediate `consumes` occurrences, no observed `may_consume`
  occurrences, and 32 reusable `requires` occurrences; and
- one exact oxygen fluid variant with 3,622 immediate consuming occurrences.

These are candidate counts from private production evidence, not the final
answer. They demonstrate why fair paging and explicit truncation are part of
truth preservation rather than performance polish.

The implemented query primitive was also exercised directly against this
production index. The oxygen root returns an exact consuming total of 3,622,
25 retained rows at limit 25, `truncated=true`, and zero reusable occurrences.
A mixed-use fluid root independently returns 203 consuming and 19 reusable
occurrences; those totals compose exactly to its unfiltered total of 222.

The lossless byproduct projection was then run twice against the same production
route query. Both runs returned 50 route items, including the 5 routes with
empty co-output lists, and exactly 52 alternatives across 52 co-output slots:
50 guaranteed and 2 conditional. Each 495,356-byte canonical projection embeds
and cites the same 409 runtime-mechanics records with no missing evidence or
padding. The runs were byte-for-byte identical at SHA-256
`bea96e8c5e57ae1be9ce546724f527751316d4580123ba1113f506318abc9d1c`;
their unchanged route truncation reports `max-routes`.

The completed forward traversal was run twice from those 52 co-output
occurrences. Exact scoped identity deduplicated them to 49 roots while retaining
every origin. Across 233 visited targets, predicate-filtered pages reported
8,109 consuming candidates and 66 reusable-only candidates. Fair same-depth
admission retained the global limit of 250 operations; reusable `requires`
occurrences did not enter that operation set.

The bounded result contains 282 closures: 36 retained-route re-entries, 36
procedural boundaries, 12 open terminals, and 198 explicitly truncated open
uses. No final-target return or forward cycle was retained in this bounded
production answer; that is an observation of this result, not a global absence
claim. Truncation reports exactly `max-operations` and
`max-consumers-per-target`.

Each repeated recycling projection is 19,195,792 canonical bytes, embeds and
cites exactly 4,763 runtime-mechanics records, and hashes to
`aa845160fbfe5e7432c09cbb968cd2065489b57c039ca495ee9f2cfd8a5f0567`.
The complete validated acceptance answer is 29,469,264 canonical bytes, carries
5,318 evidence records, and repeats byte-for-byte at SHA-256
`9c90ab415dd81c832afa59c3fb0cb4e2661d35f291c58fb499d5ca9a0cf1d405`.

## Evidence and negative-claim policy

Both sections cite only accepted `runtime-mechanics` records. Every projected
node and edge must occur byte-for-byte in the answer evidence table, and every
evidence ID must be used by an assertion. Presentation reconciliation cannot
establish a co-output or consumer.

The validator must independently check:

- `byproducts` is an exact lossless projection of `routes`;
- recycling roots are exactly the deduplicated byproduct alternatives and
  retain every origin;
- every forward operation consumes its subproblem target through
  `consumes` or `may_consume`;
- reusable requirements are not continuation edges;
- every continuation is backed by an exact operation output alternative;
- every closure references a retained final, route, or ancestor target through
  an explicit match path;
- summaries, pages, cycles, terminal leaves, unresolved rows, and truncation
  recompute from the retained DAG; and
- both evidence unions have neither missing records nor padding.

An empty consumer page can support `no-consuming-use-observed-in-page` only
when its predicate-filtered page is complete. The answer never upgrades that
statement to “not recyclable anywhere,” “safe to discard,” or “has no use.”
