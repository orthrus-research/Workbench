# Atlas complete finite recipe-impact report V2

Format: `workbench-atlas-recipe-impact-report-v2`, schema version 2.

`workbench atlas recipes impact GRAPH SELECTION --exploration complete-finite --json`
opts into complete exploration of admitted finite recipe dependencies. The
Python API is `view.complete_impact(selection_id, check_cancelled=None)`.
Ordinary `impact` calls retain the bounded V1 contract and defaults. Explicit
complete mode cannot be combined with depth or node bounds. Source-only views
cannot provide this analysis.

## Meaning of complete

The engine exhausts the finite declared structure without an arbitrary depth,
node, path-work or signal budget. It maintains a reusable immutable structural
index for one verified graph view. Removal propagation processes monotone state
changes with a worklist. A resource becomes a candidate at risk when every
observed active finite producer is unavailable; a recipe becomes a candidate
when a complete, nonempty selector has only candidate at-risk alternatives.
Each selector is an OR of its alternatives, while recipe selectors are required
together. Unknown activity and incomplete matching prevent unsupported absence
claims. Quantities, chance and reusable inputs do not establish usable supply.

Cycle analysis uses iterative strongly connected components, excluding the
selected unavailable recipe. Component membership describes mutual dependency
paths in the admitted graph. One deterministic, closed witness uses actual graph
edges; it is not an enumeration of every cycle, nor a claim that every component
member occurs on the witness. Cycles and alternate producers never establish
bootstrap supply, viability, machine operation or player reachability.

Exploration completeness and evidence completeness are separate. A successful
complete report has no resource-truncation frontiers, while matching and activity
gaps remain explicit. Cancellation or failure raises and emits no completed
report. The caller can supply Core's cancellation callback; this does not create
a second process or lifecycle owner.

## Record shape

The exact top-level fields are `format`, `schema_version`, `context`, `selection`,
`scenario`, `analysis_model`, `exploration`, `evidence_completeness`, `direct`,
`propagation`, `progression_signals`, `frontiers`, `unknowns`, `evidence_gaps`, and
`summary`. V1 context, node summaries, edge summaries and scenario meanings are
retained. There is no V1 `bounds` object.

`analysis_model` retains V1's rule and claim-boundary fields with kind
`complete-observed-finite-recipe-dependency-exposure`.

`exploration` contains `mode: complete-finite`, `status: complete`,
`graph_node_count`, `graph_edge_count`, `visited_node_count`,
`traversed_edge_count`, `worklist_event_count`, and
`cycle_algorithm: iterative-scc`. Counts describe actual work and finite inputs.

`evidence_completeness` contains:

- `status`: `complete-within-declared-model` or `incomplete`;
- `graph_incomplete_selector_count` and `graph_unknown_lookup_recipe_count`;
- sorted `encountered_incomplete_selector_ids` and
  `encountered_unknown_lookup_recipe_ids`.

Matching or activity gaps in the admitted graph keep evidence completeness
incomplete even when the selected dependency closure is fully explored. Global
counts and encountered identities distinguish graph-wide limits from the
records traversed for this selection.

`direct` retains V1 input/output records. Each output additionally declares
`downstream_consumers_evidence_complete`; its
`downstream_consumers_truncated` is false. Each producer portfolio adds
`evidence_complete`, retains `truncated: false` and `viability: not-assessed`,
and uses status `sole-observed-finite-producer`,
`observed-alternatives-present`, `no-observed-active-producer`, or
`producer-evidence-incomplete`. The sole-producer status requires the selected
recipe to be lookup-active. Removing an already inactive registration does not
create a loss; unknown activity cannot establish an absent producer.

`propagation` contains `status` (`complete-within-model` or
`evidence-incomplete`), the V1 `at_risk_resources` and `at_risk_recipes` shapes,
and `alternative_dependency_components`. Recipe/resource depth is a deterministic
elimination round, never a stopping condition.

A component has exactly `component_id`, sorted `member_node_ids`, sorted
`root_resource_ids`, `witness`, `interpretation`, and
`viability_effect: unknown`. Its identity is bound to sorted member IDs.
The identity is `workbench-atlas-dependency-component-v2:sha256:` followed by
SHA-256 of the UTF-8 JSON array of sorted member IDs, with non-ASCII characters
unescaped and compact comma/colon separators. This also applies to quest components.
`witness` contains closed `node_ids` and ordered `arcs`. Every arc contains
`source_id`, `target_id`, `edge_id`, `relation`, and `direction` (`forward` or
`reverse` relative to the original edge). Resource-to-producer links traverse
output edges in reverse. Roots are selected-output resources whose admitted
alternative-producer dependency closure reaches this component.

`progression_signals.energy_and_machine_signals` retains V1 fields, with
`truncated: false` and additional `evidence_complete`. All observed selected-map
machine bindings are retained. `quest_signals` retains V1 reference/dependent
records and interpretation, replacing `prerequisite_cycles` with
`prerequisite_cycle_components` of the same component shape and empty
`root_resource_ids`. Quest depth is shortest BFS distance. Quest status is
`observed-definition-references`, `no-observed-reference`, `evidence-unavailable`,
or `evidence-incomplete`.

`frontiers` is empty. `unknowns` and `evidence_gaps` retain their V1 row shapes
and explicit model limits. Summary fields retain V1 counts except
`alternative_dependency_cycle_signal_count` becomes
`alternative_dependency_component_count`; add
`quest_prerequisite_component_count`. Summary `truncated` is false.

## Presentation and compatibility

Clients must explicitly support V2 and preserve exact graph, search and selected
recipe linkage. They present **observed finite exploration complete** separately
from evidence incompleteness and unknown cycle viability. They cannot translate
V2 into a V1 report by discarding components, gaps or actual work. Older strict
clients correctly refuse the new format. Complete-mode transport and cancellation
use Core's existing services; interactive previews may be lazy, but retained
report bytes and completeness claims must remain exact.
