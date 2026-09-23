# Captured recipe prerequisite routes V1

Atlas projects one exact resource selection in a verified categorical V2 graph
into its prerequisite route model. The backend reads captured recipe occurrences;
it does not create a synthetic `COMMON_FINAL_STATE` runtime graph or manufacture
execution-proven machine edges to satisfy the older normalized SQLite reader.
The older runtime graph reader and its contracts remain unchanged.

## Owner API

```python
from workbench_atlas_recipe_health import (
    RecipeRouteOptions, derive_recipe_routes, open_recipe_health,
)

with open_recipe_health(graph_path) as view:
    report = derive_recipe_routes(view, resource_selection_id,
                                  check_cancelled=check_cancelled)
```

`resource_selection_id` is an exact `item-variant` or `forge-fluid` node ID
returned by the same graph's search/inspection workflow. Text, recipe nodes,
unknown IDs and initialization observation V3 graphs are refused. The caller
owns the view lifetime. The callback is optional, called throughout traversal
and before returning, and any cancellation exception propagates without a
successful report. Atlas adds no daemon, retained state or profile dependency.

Optional `RecipeRouteOptions(max_depth=None, max_resources=None,
max_recipes=None)` limits are explicit caller choices. Depth is the number of
upstream recipe steps from the selected resource; zero retains the root and
reports its active producer frontier. Resource and recipe counts limit distinct
resource subproblems and expanded active recipe occurrences respectively. They
are integers (not booleans); depth may be zero and count limits must be positive.
All `None` is the default: complete exploration of the finite accepted dependency
closure, without the older chain query's depth/route/alternative defaults.

## Report

`format` is `workbench-atlas-captured-recipe-routes-v1`; `schema_version` is `1`.

| Field | Meaning |
|---|---|
| `context` | Existing exact graph context, including graph-set identity, source scope and partition summary. |
| `selection` | Original canonical resource node, including properties and evidence. |
| `exploration` | Mode `complete-finite` or `bounded`, status `complete` or `truncated`, explicit limits, visited resource count, expanded recipe count and examined distinct edge count. |
| `assumptions` | Inventory, seed supply, infrastructure and craftability remain unknown; execution is unassessed; no preferred route or quest order is inferred. |
| `roots` | Selected resource's graph-bound subproblem ID. |
| `subproblems` | Exact resources, minimum BFS depths, producer route IDs and expansion status. Each resource occurs once. |
| `routes` | `items` are recipe occurrences serving particular resource subproblems. Alternatives are OR **within a subproblem**; routes serving different subproblems are not interchangeable. |
| `prerequisites` | One operation per route; AND requirement groups and explicit resource-to-route dependency edges, preserving reusable versus consumed slot roles. |
| `cycles` | Strongly connected components in accepted alternative dependencies, with one exact captured-edge cycle witness each. |
| `unresolved` | Missing producer, lookup state, selector qualification, finite-domain empty match and other evidence gaps. |
| `frontiers` | Dependencies omitted by requested structural limits. The number of rows is not bounded by the number of visited resources. |
| `evidence_gaps` | Projection limitations and explicit execution/acquisition, registration-causation and quest-ordering boundaries. |
| `summary` | Route, recipe, resource, cycle, unresolved and frontier counts; explicit truncation; craftability `unknown`. |

Route and subproblem IDs are graph-bound SHA-256 identities. Canonical node and
edge records are retained unchanged, including their evidence arrays. Traversal
and report ordering are deterministic for the same graph and options.

## Recipe requirements and values

Only occurrences with `lookup_active is True` become routes. Observed inactive
and unknown-state producers are retained as unresolved candidate producers with
their full records and output edges; their inputs are not recursively promoted.
No producer in this graph means **no observed producer**, not impossible
acquisition: other families, external sources or missing evidence may supply it.

Each route preserves the original `producer` and its duration, EU/t, properties,
category and occurrence identity. `mechanics.recipe_map_bindings` preserves map
membership and original `uses-recipe-map`/`currently-selects-recipe-map` edges.
Its `execution_status` remains `unassessed`. A machine's map binding is not proof
of voltage compatibility, formation, world environment, energy or player access.
An observed map membership with no machine binding produces its own gap.

`producer.properties.captured_input_counts`, when present, contains exact
nonnegative `item` and `fluid` counts and must match the selector occurrences.
A discrepancy is refused. If counts are absent, the report records
`recipe-input-inventory-unavailable`, even for an empty selector list: missing
selector evidence must not be mistaken for a confirmed inputless recipe.

`ingredient_slots` and `reusable_requirements` are separate AND groups. Every
captured input selector remains a distinct slot, even if two slots name the same
resource. A true `non_consumable` selector is a reusable requirement; an unknown
flag produces an explicit consumption gap rather than claiming consumption or
free reuse. Original quantities are retained as integers, without floating-point
conversion or multiplying them by a demand target. Alternative resource producers
are represented by shared subproblems, not enumerated combinations of full paths.

Within each slot, accepted edges form OR alternatives. `acceptance_complete is
True` means the adapter reports exhaustive alternatives within its captured
resource domain, whose identity/scope remain in the original selector properties.
It does not mean all possible game stacks or successful native execution.
Missing/false completeness produces a gap even if some accepted edges exist.
Runtime matching limits (for example unobserved capability checks) remain gaps.
An explicitly complete empty domain gets `no-accepted-resource-in-captured-domain`;
an incomplete empty domain gets `selector-acceptance-incomplete`.

Observation-only representative edges stay in `observed_representatives`; they
do not gain producer links or become accepted alternatives. A selector claiming
complete acceptance while containing observation-only alternatives is refused.

`production.matched_outputs` retains every matching output occurrence from the
recipe, including guaranteed and chanced entries to the same resource.
`production.all_outputs` also retains cooutputs. Output family and ordinal remain
intact; ordered XOR/chance entries retain their original logic class, chance and
boost. A later 10000 chance entry is not converted into a guaranteed independent
output. Atlas performs no probability, expected-yield, quantity-balancing or
machine-overclock calculation.

## Completion, cycles and quest planning

`exploration.status=complete` means traversal exhausted the admitted finite
accepted dependency closure. It can coexist with incomplete selectors, unknown
matching, missing producers, lookup-inactive alternatives and unknown acquisition.
It is never a craftability claim or proof that all recipe families were captured.

Cycle components use the directed relationships resource → producing recipe
(reverse output edge) → input selector → accepted resource. Witness arcs include
original edge IDs, relation and direction; all IDs can be checked in the admitted
graph. Components include OR alternatives and reusable requirements, so a cycle
does not prove every route needs itself. Seed supply and viability remain unknown,
even when another producer is observed. No player seed inventory is invented.

These routes are authoring evidence: inspect a target batch, compare producers,
follow its AND/OR inputs, and investigate machines, reusable tools, missing
sources and cyclic dependencies. Quest dependency design, route selection,
inventory assumptions and proof of completion remain separate decisions.
