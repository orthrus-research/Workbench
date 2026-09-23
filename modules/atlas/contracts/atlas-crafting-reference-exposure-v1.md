# Crafting stored-reference exposure V1

This workflow answers: **which named crafting definitions retain a reference path
to this exact stored native object in the selected catalog?** It preserves aliases
and cycles in the recorded object graph. It does not evaluate ingredient matching,
infer source registration causation or recipe dependencies, predict an edit's
effect, or establish gameplay availability. Object cycles are not recipe cycles.

```text
workbench atlas observations crafting-exposure GRAPH NATIVE_VALUE_ID [--max-depth N] [--max-nodes N] --json
```

The Python API is:

```python
from workbench_atlas_observations.exposure import derive_crafting_reference_exposure

derive_crafting_reference_exposure(view, selection_id, max_depth=None, max_nodes=None)
```

`view` is an open, verified `ObservationView`. The operation also participates in
the [observation session](atlas-observation-session-v1.md). It reads retained
observations and does not launch a game or invoke native recipe callbacks.

## Admission and exact reference validation

The graph must use the V3 retained-observation authority and the
`workbench-atlas-initialization-projection-v1` contract. The selection must be an
`initialization-crafting-native-value` with exactly one crafting catalog owner.
The catalog must retain `axiom.native-stored-crafting-recipes.v1`. Snapshot,
selected side, report section and original JSON pointers must agree with the
graph binding. Local native value IDs have meaning only within this catalog.

Before deriving an answer, Atlas verifies the entire selected catalog's reference
projection, including observations outside the selected object's reverse closure:

- Every stored value and recipe entry has one correct catalog membership, an
  exact escaped original member pointer, and the corresponding original key.
- Retained mapping types and collection counts match the projected membership.
- Every literal `{"nativeValueRef": "local-id"}` in retained node values resolves
  to that catalog's exact native value and has exactly one projected reference
  at its original pointer.
- Every projected reference touching the catalog corresponds to such a literal
  reference, with the same source, target, pointer, snapshot and side. Missing,
  additional, malformed and cross-catalog references refuse the operation.

This check compares raw retained node values and relationships; it does not treat
a sealed graph edge as proof of its own domain meaning. The original producer's
snapshot is not reopened by this query. Original evidence resolution retains its
separate owner-reader operation described by the
[observation query contract](atlas-observation-query-v1.md).

Successful catalog verification may be reused only within the same unchanged,
open view. This in-memory cache retains verification status and evidence gaps,
not the full catalog. Cancellation or failure cannot populate it. View custody
checks run before and after derivation; closed or changed views refuse reuse.

## Exploration and completeness

The algorithm follows incoming `references-native-value` edges from the selected
value, using iterative breadth-first traversal. Values are expanded once; named
crafting entries are endpoints. It returns one deterministic shortest witness per
recipe and retains every examined reference occurrence, including aliases and
cycles. It does not enumerate all possible paths.

Defaults exhaust this finite reference graph. Optional limits must be positive
integers. `max_depth` counts reference edges from the selected value;
`max_nodes` counts distinct admitted values and recipe entries, including the
selection. SQL batches and generic query page sizes do not cut this analysis.
Catalog validation remains complete even when explicit traversal limits apply.

`traversal.state: complete` means the recorded reverse reference graph was
exhausted. `truncated` retains each unadmitted reference occurrence in `frontier`.
Revisiting a known node, including a cycle, does not consume another node or
create a frontier. Frontier entries are reference occurrences, so several entries
may name one unadmitted object.

Evidence completeness is separate. The original catalog must declare observed
status, `storedValuesComplete: true` and an empty `affectingGaps` list. Any stored
incomplete observation or recipe gap anywhere in the catalog keeps reference
evidence incomplete, even when the traversal finishes. A native failure or a
historical observation is preserved as such; reference completion does not
promote it to successful initialization or a current running world.

An empty, complete traversal with complete reference evidence means
`none-observed` within this exact retained catalog. An empty traversal with cuts
or incomplete evidence is `undetermined`. Positive results are
`referrers-observed`, with their independent traversal and evidence states intact.

## Result schema

The report has `format: workbench-atlas-crafting-reference-exposure-v1` and integer
`schema_version: 1`:

| Field | Meaning |
| --- | --- |
| `context` | Unchanged observation context, authority, scope, original outcome and coverage. |
| `selection`, `catalog` | Complete canonical selected value and owning catalog nodes. |
| `claim_boundary` | The explicit stored-reference-only claim. |
| `limits` | Nullable `max_depth` and `max_nodes`. |
| `traversal` | `state`, `visited_nodes`, `visited_values`, `visited_recipes`, `examined_reference_edges`, `maximum_distance`. Values include the selection; maximum distance covers admitted nodes. |
| `evidence` | `crafting_reference_inventory` (`complete` or `incomplete`), original `native_outcome`, original `capture_coverage`, and false `matching_evaluated` and `edit_effect_evaluated`. |
| `summary` | `recorded_recipe_count` and `status`: `referrers-observed`, `none-observed` or `undetermined`. |
| `results` | Rows sorted by recipe ID: complete canonical `recipe`, shortest `distance`, and `witness`. |
| `reference_edges` | All examined canonical reference edges, sorted by edge ID. Includes references leading to the frontier. |
| `frontier` | Rows containing `reason` (`max-depth` or `max-nodes`), canonical unadmitted `observation`, `distance`, `reference_edge_id` and `toward_selection_id`; sorted by distance, observation ID and edge ID. |
| `evidence_gaps` | Original catalog or stored-observation incompleteness, without converting it to missing references. |

A witness contains `node_ids` from recipe to selected value and complete
canonical `edges` between consecutive nodes. Its edge count equals the shortest
distance. Equal-length alternatives are resolved deterministically by source ID,
reference pointer and canonical index order; every examined alias remains in
`reference_edges`.

Gap code `crafting-stored-reference-evidence-incomplete` retains catalog
`observation_id`, `status`, `stored_values_complete` and `original_gaps`.
`incomplete-stored-observation` retains the member `observation_id`,
`original_gaps`, and `unknown_values` containing local `pointer`, `reason` and
`type`. Null or malformed original completeness declarations are not promoted
to complete evidence.

Cancellation is checked during catalog validation, reference traversal,
incompleteness scanning and witness construction. Cancellation publishes no
successful report. Input graphs and original snapshots remain unchanged.

## Source meaning

Axiom's [`NativeRecipeValues`](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRecipeValues.java)
uses an identity-based local catalog to preserve shared native objects. Its
[`NativeVanillaRecipeObservations`](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeVanillaRecipeObservations.java)
records stored fields, completeness gaps and original registry order without
invoking matching callbacks. Atlas follows those retained references under the
[initialization node-family contract](atlas-initialization-node-families-v1.md).
