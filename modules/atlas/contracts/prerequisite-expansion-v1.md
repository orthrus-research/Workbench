# Unified Process Atlas prerequisite expansion v1

Status: route readiness, shared construction traversal, and production-proven
infrastructure projection implemented and active

This expansion separates three player questions that cannot safely share one
flat prerequisite list:

1. `PLAYER-BUILD-PREREQUISITES-001`: What machine, inputs, and reusable
   requirements must exist before this route runs?
2. `PLAYER-MACHINE-CONSTRUCTION-001`: How do I obtain or construct each
   required machine?
3. `PLAYER-INFRASTRUCTURE-REQUIREMENTS-001`: What power, voltage tier, hatches,
   structures, dimensions, or environmental conditions are required?

All three player questions are implemented and active. Infrastructure
readiness remains explicitly partial where source authority, formed-world
state, or bounded traversal cannot close a claim.

The dedicated
[infrastructure expansion](infrastructure-expansion-v1.md) adds the companion
developer question, exact operation handoff, source/runtime fidelity contract,
production census, known capture gaps, and activation gates.

## Shared rules

- Every result is scoped to an exact snapshot, profile, physical side, target,
  and bounded traversal.
- The default player-state assumption is unknown inventory and unknown existing
  infrastructure. The Atlas reports requirements; it does not claim that the
  player lacks them.
- Distinct requirements remain `AND` groups. Recipe, machine, or ingredient
  choices remain `OR` or explicit match-domain groups.
- No projection chooses a shortest, cheapest, earliest, or otherwise preferred
  route without a separately versioned policy.
- Quest ordering remains quest guidance. It never becomes a mechanical
  prerequisite without separate runtime-mechanics or pinned-source evidence.
- Presentation, consultation, and category membership do not prove execution.
- Cycles, unresolved identities, symbolic domains, missing producers, profile
  gaps, and traversal limits remain explicit.
- A summary may describe only the bounded observed graph. It cannot upgrade a
  truncated result into a complete global requirement set.

## Question 1: route readiness

### Meaning

For each producer hyperedge in the existing bounded route DAG, report what must
exist before that operation can execute. This is readiness for an operation,
not a claim that every machine or upstream material has been constructed from
scratch.

### Derivation

The `prerequisites` projection is a lossless projection of `routes`:

- executable-machine choices come only from `route.mechanics.execution`;
- consumed input groups come from `route.ingredient_slots`;
- reusable groups come from `route.reusable_requirements`;
- exact upstream ordering comes from linked alternative `subproblem_id` values;
- procedural, worldgen, and symbolic boundaries come from the route boundary
  and unresolved-leaf records;
- cycles and truncation are copied from the same route DAG.

Every route operation has an outer `AND` group. Its machine candidates form one
`OR` group, each consumed or reusable slot forms another requirement group, and
the alternatives inside a slot retain their observed `OR`, `MATCH_DOMAIN`, or
`SYMBOLIC` mode. The machine group is `resolved`, `not-required`, or
`unresolved`; `not-required` is permitted only for an explicit no-machine
acquisition boundary.

An operation with no execution-proven machine does not receive a guessed
machine. It carries an unresolved machine requirement unless its evidence
explicitly identifies a no-machine acquisition boundary such as worldgen.

### Required projection shape

`prerequisites` contains:

- `status`;
- `target`;
- `roots`;
- `subproblems`;
- `assumptions`;
- `operations`, keyed by route and subproblem identity and retaining the exact
  production record that proves the operation belongs to that subproblem;
- `dependency_edges`, linking an upstream subproblem to the operation that
  requires it;
- `cycles`;
- `unresolved`;
- `truncation`;
- `evidence_ids`.

The projection must use the same target, scopes, route items, cycles,
unresolved leaves, and truncation state as `routes`. Validation rejects a
prerequisite result independently reconstructed from a different traversal.

### Implementation boundary

No new runtime capture is required. The first implementation belongs beside the
existing ingredient-slot and reusable-requirement projections in
[`corpus_bridge.py`](../src/workbench_atlas/corpus_bridge.py).

## Question 2: machine construction

### Meaning

For each exact machine candidate returned by route readiness, report how its
placeable or usable form can be obtained. This is a separate traversal because
machine construction can require other machines and can contain bootstrapping
cycles.

### Derivation

1. Start from each exact execution-proven machine node.
2. Resolve its exact registered stack through a `has_form` edge whose role is
   `registered_mte_stack_form`, or an equally explicit form relation for
   another machine family.
3. Start a bounded producer traversal from the already-resolved form node.
   `NodeSelector.by_id(form.id, kind=form.kind)` is the exact single-root
   prototype; it does not reverse-engineer a semantic selector.
4. Preserve crafting holes, input grouping, reusable requirements, execution
   machines, procedural boundaries, cycles, and truncation exactly as in an
   ordinary process chain.
5. Deduplicate shared construction subproblems by exact scoped node identity,
   without merging machines that merely share a display name.

The production projection seeds all resolved forms through
`build_process_chain_from_targets`, which uses
`RuntimeGraphChainQuery.chain_resolved_targets` and one internal multi-root
traversal state. Running one independently bounded chain per machine would
repeat shared subproblems and silently multiply every construction budget.
Public exact selectors may wrap this resolved-target entry point, but
construction traversal does not depend on fuzzy or lossy key conversion.

The route-to-construction handoff is
`corpus_bridge._machine_construction_seeds`. It aggregates identical exact
machine nodes and retains every requiring route, subproblem, and execution
binding. It performs no form or producer inference.

### Production readiness census

The 2026-07-28 pilot used
`PLAYER-BUILD-PREREQUISITES-001` for
`susy:diluted_oil_light` in the
`COMMON_FINAL_STATE:DEDICATED_SERVER` scope, with the primary route traversal
bounded at depth 8, 50 routes, 25 alternatives per slot, and 10,000 visited
nodes. The resulting bounded prerequisite graph contains:

- 50 route operations;
- 92 unique execution-proven machines across 513 route-machine links;
- 92 exact, unique `item_variant` stack forms, each reached by exactly one
  `has_form` edge with role `registered_mte_stack_form`;
- 63 forms with exactly one mechanically eligible root producer: 62
  `recipe_rule` owners and one executable `recipe` owner;
- 29 forms with no observed mechanical producer.

This census proves that current capture has a lossless form handoff for every
observed machine candidate. It does not make the 29 missing producers
impossible or creative-only, and it does not claim that a deeper construction
forest will be complete.

Three probes define the cases fixtures and the production pilot must retain:

- `susy:blender` resolves through its exact stack form and expands into a
  nontrivial recipe/rule chain; a depth-2, 12-route probe reached 37
  subproblems and correctly reported both depth and route truncation.
- `susy:batch_reactor.lv` resolves to one rule-backed root whose symbolic
  crafting domains remain explicit and can hit the per-slot alternative bound.
- `gregtech:autoclave.opv` resolves to an exact stack form but has no observed
  mechanical producer, so it remains an unresolved construction root.

The activated production answer closes execution-machine requirements into the
same bounded forest. Starting from the 92 material-route machines, it discovers
157 additional construction-route machines. All 249 machines have one exact
admitted form and one construction root. The shared forest contains 341
deduplicated subproblems, 250 retained routes, and 881 resolved execution-
machine dependency edges. No bootstrapping cycle is retained under this bound.
It reports `max-routes` and `max-alternatives-per-slot` truncation plus 84
`no-mechanical-producer` leaves across roots and descendants. Its 12,770-record
runtime-mechanics evidence closure has stable canonical answer SHA-256
`db5eee744c622cf438df206dd7a0ac42872209381fe14638b2370cfa651c82f8`.
These are bounded traversal observations, not a complete construction answer.

### Implemented contract

1. Aggregate machine seeds only from the implemented `prerequisites`
   projection. A machine retains sorted `required_by` records containing its
   route ID, subproblem ID, and exact execution bindings.
2. Resolve stack forms in the same profile and physical side as the machine.
   Retain the machine node, `has_form` relationship, and form node as one
   evidence path. Zero or multiple admitted forms are explicit unresolved
   states.
3. `RuntimeGraphChainQuery.chain_resolved_targets` is the resolved-target,
   multi-root entry point. It validates every scoped node against the open
   graph, deduplicates and sorts the root set, and seeds its breadth-first queue
   before expanding any descendant. The existing subproblem identity
   deduplicates shared construction work. Its visited-node budget must be large
   enough to represent every explicit root.
   Its additional-root provider closes execution-proven machines discovered by
   retained construction routes into the same state.
4. Give the construction forest one global `ChainOptions` budget, independent
   from the primary material-route budget. The first production pilot uses
   depth 8, 250 routes, 25 alternatives per slot, and 10,000 visited nodes.
   These are bounds, not completeness claims.
5. Report every required machine even when a root cannot be seeded after a
   machine-root or visited-node bound. A seeded root with no eligible producer
   is `no-mechanical-producer`; neither state is silently omitted.
6. Evidence closes over machine records, execution bindings, form paths, and
   every retained construction record.

### Required projection shape

`machine_construction` contains:

- `status`;
- `machines`, retaining the route IDs that require each machine;
- each machine's exact form-resolution path;
- a bounded construction route DAG;
- bootstrapping cycles;
- unresolved forms or producers;
- independent construction-traversal limits;
- `evidence_ids`.

A machine with no exact form or no mechanical producer is reported unresolved
or externally acquired. Absence of a recipe is not proof of creative-only,
quest-only, or impossible acquisition.

## Question 3: infrastructure readiness

### Meaning

For each route operation and machine-construction operation, report explicit
non-inventory conditions required for execution.

### Evidence classes

The implementation may project only conditions represented by exact runtime
mechanics and, wherever interpretation is required, an exact pinned-source
semantic:

- recipe EU/t, duration, and explicit energy-carrier use;
- registered machine tier and explicit voltage constraints;
- machine or rule `has_constraint` records;
- required hatches, coils, catalysts, formed capabilities, and structure
  conditions expressed by exact `requires` slots or constraints;
- exact structure resources where the runtime graph binds them;
- dimension and worldgen relations;
- environmental, world-state, or procedural boundaries.

Mapping EU/t to a named voltage tier is a derived assertion. It requires an
explicit normalized rule or exact pinned-source mapping and must cite that
evidence. The Atlas may always report observed EU/t without naming a tier.

The player question now requires both `runtime-mechanics` and `pinned-source`
evidence. Its active developer companion,
`DEVELOPER-INFRASTRUCTURE-SEMANTICS-001`, additionally requires declaration and
ownership context so an answer can teach which pack, SusyCore, GTCEu, or Forge
surface establishes and enforces a condition. Forge lifecycle or capability
substrate does not become ownership evidence for GTCEu voltage or SuSy
controller semantics.

`corpus_bridge._infrastructure_operation_seeds` is the implemented handoff. It
retains every complete material-route and construction-route operation and
names its exact same-scope owner and execution-machine condition subjects. It
does not admit candidate edges or derive tiers.

The production pilot handoff contains 50 material-route and 250
construction-route operations, 300 unique owners, and 249 exact machines. Those
subjects expose 1,026 `has_constraint`, 237 `uses_energy`, 13 `configured_by`,
and 23 grouped `requires` candidate paths. These are candidates, not admitted
requirements. The successor projection source-binds all 12 selected
Gregicality Multiblocks controllers and resolves all 19 selected registered
structure definitions. It retains 32 partial formed-energy-input cases across
16 null-tier controller prototypes, plus all traversal and placed-world
boundaries, as explicit fidelity gaps.

### Required projection shape

`infrastructure_requirements` contains:

- `status`;
- `operations`, keyed to the route or construction operation they constrain;
- typed requirement groups for energy, tier, machine structure, capabilities,
  dimension, environment, and other procedural state;
- applicability or conditionality for every requirement;
- unresolved constraints and boundaries;
- inherited route and construction truncation;
- `evidence_ids`.

Executable-machine identity does not by itself prove a complete multiblock
bill of materials, power network, or world condition.

## Current evidence boundary

Questions 1 and 2 answer route readiness and exact machine construction.
Question 3 is active with a production-proven fail-closed projection; its
partial status communicates exact remaining evidence boundaries.
