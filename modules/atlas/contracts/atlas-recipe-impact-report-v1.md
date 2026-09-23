# Atlas recipe-impact report V1

Status: additive read-only Atlas report over one verified categorical graph.

Format identity: `workbench-atlas-recipe-impact-report-v1`.

## Question

For one exact `gt-recipe` node returned by recipe-health search, what observed
finite recipe dependencies, machine signals, and BetterQuesting definitions
would be exposed if that exact recipe were unavailable?

V1 models removal of the current recipe. A change is represented only by the
loss half of that counterfactual. New inputs, outputs, quantities, or runtime
behavior require either a new observed graph or a separately validated
proposed-recipe model; V1 does not invent them from source text.

## API

```python
from workbench_atlas_recipe_health import open_recipe_health

with open_recipe_health(graph_root) as view:
    report = view.impact(selection_id, max_depth=4, max_nodes=500)
```

`selection_id` must resolve to one observed `gt-recipe`. Source-only contexts
and non-recipe selections fail closed. `max_depth` is in `1..12`; `max_nodes`
is in `10..2000`. Both recipe propagation and quest-prerequisite expansion are
bounded. Any omitted expansion is recorded in `frontiers`, and incomplete
evidence that prevents a classification is recorded in `unknowns`.

## Report sections

- `direct.inputs` retains exact current selectors and accepted resources.
  Direct selectors, resources, and outputs consume the same declared node
  budget as every downstream traversal; omissions produce a frontier and an
  explicit unknown instead of escaping `max_nodes`.
- `direct.outputs` retains each exact current output, observed lookup-active
  alternative finite producers, and direct lookup-active finite recipe
  consumers. A recipe registration that is category-present but lookup-inactive is not a viable
  producer alternative. A missing or non-boolean lookup state leaves the
  producer set truncated and is recorded in `unknowns`. Alternative producer
  viability beyond that exact runtime lookup state is always `not-assessed`.
  The portfolio is `sole-observed-finite-producer` only when the selected
  recipe is the last observed active producer. A complete producer set with
  zero active recipes is `no-observed-active-producer`; an inactive selected
  registration does not count as a sole producer. Other portfolio statuses
  are `observed-alternatives-present` and `producer-set-truncated`.
  Lookup-inactive consumers are excluded from direct and propagated exposure;
  missing or non-boolean consumer activity leaves that consumer set incomplete
  and records `consumer-lookup-state-unavailable` in `unknowns`.
- `propagation.at_risk_resources` contains candidate resources for which every
  observed lookup-active finite recipe producer is already an unavailable
  candidate.
- `propagation.at_risk_recipes` contains candidate recipes with at least one
  exact selector whose observed alternatives are all candidate at-risk
  resources.
- `propagation.alternative_dependency_cycle_signals` reports bounded structural
  cycles found while examining alternative producers. Convergent paths are
  canonicalized, cycle work is capped at `8 * max_nodes`, emitted signals are
  capped at `max_nodes`, and any omitted path expansion is recorded as a
  frontier. A cycle signal does not prove that an alternative route is
  unusable.
- `progression_signals.energy_and_machine_signals` reports the selected
  recipe's numeric EUT/duration and exact machines observed using its recipe
  map. Numeric machine tiers are retained without mapping them to pack
  progression tiers.
- `progression_signals.quest_signals.direct_resource_requirements` joins
  affected item/fluid resources through exact requirement occurrences and
  tasks to BetterQuesting quest definitions. Item occurrences that also accept
  an ore-dictionary key retain that selector and are not presented as an exact
  item requirement.
- `progression_signals.quest_signals.structural_prerequisite_dependents`
  follows exact prerequisite occurrences to downstream quest definitions.
  Quest/task logic is retained in node properties and is not reinterpreted.
- `summary` contains presentation counts only. Counts describe candidates and
  structural exposure, never proven broken progression.

## Authority boundary

The report is an Atlas derivation from an exact admitted graph. Its monotone
propagation rule is useful for finding likely dead-path review targets, but it
is not gameplay reachability. In particular, V1 does not assess:

- world generation, loot, trade, inventory, commands, or other acquisition;
- procedural/dynamic recipe execution;
- amounts, probabilities, throughput, reusable inputs, or inventory balance;
- machine formation, power availability, or profile-specific tier policy;
- BetterQuesting task matching, completion, rewards, or player progress; or
- the runtime counterfactual after the recipe is actually removed or changed.

An IDE may render candidate paths prominently only if it also renders
`claim_boundary`, `frontiers`, `unknowns`, and `evidence_gaps`. It must not
rename candidate at-risk nodes to “broken” or “unreachable.”

When the admitted graph retains projection limitations, the report appends a
`graph-projection-limitations` gap after its existing seven evidence gaps.
The graph's limits remain applicable to derived impact candidates.
Selectors marked `acceptance_complete: false` retain explicit
`selector-acceptance-incomplete` uncertainty and a traversal frontier. Their
captured representatives do not establish a complete accepted-resource set or
an exposed consumer, even when the graph also carries some exact acceptance
edges.

## Operational presentation

The V1 report bytes and phase-local `propagation.status` meaning remain
unchanged. The Workbench CLI derives an additive overall closure from all
retained frontier rows. If any phase produced a frontier, the human view says
`Overall closure: truncated`, gives an exact per-phase/per-kind frontier count,
and never presents `complete-within-model` as the report-wide result. The human
view caps detail; `--json` retains every exact V1 frontier and unknown row.
