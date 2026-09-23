# Atlas proposed-recipe assessment V1

Status: additive read-only Atlas assessment over one verified categorical V2
graph and one owner-validated ADD plan projection.

Format identity: `workbench-atlas-proposed-recipe-assessment-v1`.

## Question

Given a proposed finite GregTech recipe that was validated by its construction
owner, what exact observed graph keys can Atlas resolve, what existing finite
recipe neighborhoods look similar, and which collision, output-flow,
output-to-input cycle, machine, and quest-definition signals deserve review?

V1 does not render or validate the source plan. The caller must reopen the
retained plan through its owner and translate the validated request plus the
bound recipe-map registry name. Atlas validates only that bounded generic
translation. This avoids creating a second plan authority.

## API

```python
from workbench_atlas_recipe_health import (
    assess_proposed_recipe,
    open_recipe_health,
)

with open_recipe_health(graph_root) as view:
    report = assess_proposed_recipe(
        view,
        owner_validated_projection,
        max_depth=4,
        max_nodes=500,
    )
```

The context must be an explicit verified categorical V2 graph. Source-only
contexts fail closed. `max_depth` is in `1..12`; `max_nodes` is in
`10..2000`. Omitted traversal is reported in `frontiers`.

The public Workbench adapter must compare the owner-validated plan's pack
profile, platform profile, and platform version with the graph scope before it
calls Atlas. V1 graph manifests do not bind the proposed workspace revision,
so capture freshness remains an explicit evidence gap even after that scope
check succeeds.

The generic projection binds the source plan's content ID, format and kind,
ADD mutation, recipe-map registry name, duration, symbolic voltage tier, and
ordinary item/fluid input and output descriptors. It never contains source
bytes or claims that those descriptors resolved at runtime.

## Report sections

- `resolution` distinguishes an exact observed fluid or ore key from an
  ambiguous item candidate, an unresolved key, and a symbolic metaitem whose
  source-to-runtime binding is unavailable. Ordinary item candidates bind the
  requested stack count as well as registry name and optional metadata.
- `collision_assessment.exact_runtime_signature` remains unavailable until a
  runtime observes the proposed recipe and its semantic digest.
- `collision_assessment.resolved_request_structure` reports current recipes
  that match all resolvable request dimensions. These are structural review
  candidates, not exact signature collisions. If the bound recipe-map node is
  absent, this section is `evidence-unavailable` rather than a clean negative.
- `output_flow` reports existing observed finite producers and consumers of
  each exactly resolved proposed output.
- `dependency_cycle_candidates` follows bounded existing finite-recipe paths
  from a proposed output to a proposed input. Adding the proposed recipe would
  close the reported structural cycle; viability and progression effects
  remain unknown.
- `progression_signals.quest_signals` joins proposed outputs to exact
  BetterQuesting requirement occurrences, tasks, and quest definitions.
- `progression_signals.energy_and_machine_signals` reports the proposed
  duration and symbolic voltage tier beside observed numeric recipe EUT,
  duration, and machine tier values. V1 does not convert the symbolic tier to
  a claimed runtime EUT or pack progression tier.
- `summary` contains presentation counts only.

## Dead-path and progression boundary

The accepted plan is ADD-only. This structural model removes no observed
finite graph edge, so V1 reports no dead-path candidates. Runtime lookup
ordering or collisions could still have effects, but those effects require a
new runtime observation rather than inference from source bytes.

Collision candidates, cycle candidates, output consumers, and quest
references may help a developer review unintended shortcuts. They are not
proof of a bypass, a usable recipe, player reachability, task completion, or
broken progression. The assessment also excludes non-recipe acquisition,
dynamic recipe execution, inventory balance, throughput, reusable-input
semantics, chance, machine formation, and pack-specific tier policy.

V1 also reports that graph-capture freshness is unknown: current categorical
graph manifests and the bounded generic proposal do not bind a shared
workspace revision. Pack/platform compatibility is enforced by the Shell
owner adapter before Atlas receives the proposal.

Graph projection limitations remain visible in `evidence_gaps`. A selector
marked `acceptance_complete: false` cannot establish an exact structural
collision candidate; its incomplete acceptance is recorded in `unknowns`.

An IDE must present `analysis_model.claim_boundary`, `frontiers`, `unknowns`,
and `evidence_gaps` beside prominent candidate counts. It must not relabel a
candidate as “collision,” “bypass,” “dead,” “broken,” or “unreachable.”
