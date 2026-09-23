# Unified Process Atlas infrastructure expansion v1

Status: production-proven and active; exact capture, typed normalization,
source binding, schema, semantic recomputation, unresolved coverage, and
evidence closure implemented

This expansion answers two related questions without treating them as the same
answer:

- `PLAYER-INFRASTRUCTURE-REQUIREMENTS-001`: What non-inventory conditions must
  the player satisfy to run the selected material and machine-construction
  operations?
- `DEVELOPER-INFRASTRUCTURE-SEMANTICS-001`: Which implementation,
  configuration, registration, and runtime rule establishes each reported
  condition?

The player answer says what is required and when it applies. The developer
answer additionally says who owns the rule, where it enters the game, which
source symbol enforces it, how the final runtime instantiates it, and where the
evidence stops. Both are active over the same production-proven typed
projection; partial and unresolved conditions remain visible rather than
blocking the whole bounded answer.

## Purpose

The Atlas is intended to be a version-bound explanatory model of the assembled
game, not a manually copied recipe wiki. Supersymmetry is constructed by several
authorities acting in sequence:

| Layer | Truth it can establish | Truth it cannot establish alone |
| --- | --- | --- |
| Minecraft Forge/FML | lifecycle ordering, registries, configuration loading, capabilities, dimensions, sides, and world substrate | GTCEu voltage names, machine patterns, or pack progression intent |
| GTCEu | material and MTE registries, recipe execution, energy and overclock behavior, multiblock patterns and abilities, cleanroom rules, and machine persistence | SuSy controller overrides or pack-authored recipe/config state |
| SusyCore and admitted machine mods | custom controllers, capabilities, recipe properties, hatches, dimensions, and machine-specific enforcement | the final Groovy-mutated registry state |
| Supersymmetry pack | Groovy mutations, configuration, resources, structures, quests, and build/load order | the internal meaning of dependency APIs without their pinned source |
| accepted runtime graph | the exact final objects, values, relations, profile, and physical side produced by all preceding layers | why an implementation behaves that way, or an unobserved formed-world state |
| curated interpretation | a player or developer explanation derived from cited source and runtime records | a new mechanical fact |

The source boundary is exact for the current pilot: pack commit
`9d3aa7ae0294bf27f0b8acbb893d61da23a06972`, GTCEu commit
`9fe140febe8747bbe2f06dfd570421331ec06f4b`, SusyCore commit
`b5ee1120df93c95b66e6a4dfff35582928f2d348`, Gregicality Multiblocks
commit `54168e0dc6d55089469be43eb4ab7517776972b2`, and Forge commit
`d3f01843f7e7a4f613b5e8113d381fd8747b4343`. An answer never transfers
a claim across versions without another exact mapping.

## Fidelity contract

Each positive infrastructure assertion must retain a complete truth path:

1. **Operation identity:** the exact material-route or construction-route
   operation to which the condition applies.
2. **Runtime observation:** the exact recipe, rule, machine, relationship,
   constraint, resource, configuration, or dimension observed in one snapshot
   and profile/physical-side scope.
3. **Source semantic:** an exact repository revision, path, symbol, and source
   span that declares or enforces the rule when interpretation is required.
4. **Derivation:** a named, versioned policy for any transformation, such as
   mapping numeric EU/t to a named voltage tier.
5. **Explanation:** player-facing wording or developer teaching that cites the
   preceding records and does not add another mechanic.

If any required link is missing, the assertion is `unresolved`; it is not
omitted and does not become a best-effort guess. Runtime and source evidence
must agree on identity and applicability, but they are not collapsed into one
authority. Source code can define possible behavior while runtime evidence
selects the implementation, configuration, and registered prototype actually
present in this snapshot.

Absence has three distinct meanings:

- `not-applicable`: positive evidence proves that the requirement does not
  apply to this operation;
- `not-observed`: the bounded capture has no admitted positive path;
- `unresolved`: the capture or source model is not sufficient to decide.

Only `not-applicable` may be rendered as “not required.” A missing recipe,
constraint, structure edge, or source binding is never negative proof.

## Exact operation handoff

`corpus_bridge._infrastructure_operation_seeds` is the stable handoff from the
two implemented traversal domains. It takes:

- every retained item in the bounded material-route DAG; and
- every retained item in the bounded machine-construction forest.

For each operation it retains the complete route record and identifies the only
initial condition subjects:

- the exact operation owner (`recipe`, `recipe_rule`, `process_rule`, or
  `worldgen_deposit`); and
- every exact same-scope machine reached through execution mechanics.

The handoff performs no infrastructure admission, voltage-tier derivation,
source interpretation, route preference, or completeness upgrade. The origin
is part of operation identity, so an equal runtime route retained in both
domains does not lose why it is required.

## Exact capture proof

Dedicated final-state capture `EVID-9092` was produced by extractor artifact
`33e545f10a4a857b6517eeee55730e1c1c54660fc9ad1cb363d6039134b065a2`
at the active pack commit. It atomically published 994,211 nodes, 2,047,763
edges, and 2 diagnostics. Independent raw validation closed all 18 adapters and
136,439 terminal roots.

The capture contains 161 `multiblock_structure_pattern` nodes from 147 factory
owners. Every `createStructurePattern()` factory was invoked twice and both
canonical projections agreed. The 1,038 predicate instances contain 1,867
simple alternatives: 1,609 have only supported captured values, 226 have no
captured fields, and 32 retain explicit unrecognized capture types. It also
records the effective final `cleanMultiblocks=false` value on cleanroom
envelopes. No world pattern check, structure formation, or placed-state
observation was invoked.

### Copied base-predicate identity

`FactoryBlockPattern.where` copies a `TraceabilityPredicate`, so the outer
predicate-container identity cannot reliably distinguish a constructed
predicate from the GTCEu `ANY` and `AIR` constants. The copy retains the
underlying `SimplePredicate.predicate` function object, however. The successor
extractor therefore validates the pinned shape of both base constants—exactly
one common simple predicate, no limited alternatives, and a non-null predicate
function—and compares the copied alternative's function by reference.

The raw projection emits only `gt_any`, `gt_air`, or `constructed`.
`gt_any` and `gt_air` close an otherwise empty capture because their semantics
come from the exact pinned GTCEu base predicate; `constructed` still requires
captured fields, candidates, or another supported semantic. An unrecognized
closure value remains partial. This discriminator establishes registered
pattern semantics only; it never asserts that a placed state passed a world
check.

Successor dedicated capture `EVID-9093` was produced by the 868,653-byte
extractor artifact
`4c379eb3c5f6c03869d6084e41e22a50e9208d0b5d299658475b21e302f09671`.
It published the same 994,211 nodes, 2,047,763 raw edges, 2 diagnostics, 18
complete adapters, and 136,439 terminal roots. Its 1,867 simple alternatives
classify as 98 `gt_any`, 94 `gt_air`, and 1,675 `constructed`. Independent raw
validation passed. Normalized evidence set `RUNTIME-GRAPH-9093` independently
passes the full semantic validator with 994,211 nodes, 2,009,615 edges, 2
diagnostics, one dedicated profile, and no reconciliations.

## Production readiness census

The 2026-07-28 dedicated-server pilot uses the active
`PLAYER-MACHINE-CONSTRUCTION-001` answer for
`susy:diluted_oil_light`. Its bounded operation handoff contains:

- 50 material-route operations and 250 construction-route operations;
- 300 unique retained operation owners: 110 finite `recipe` rows, 188
  `recipe_rule` rows, and 2 `process_rule` rows;
- 249 exact execution-proven machines: 221 single-block workables, 19
  multiblock controllers, and 9 other MTEs;
- 222 machines with numeric registered tiers and 27 with a null registered
  tier;
- 247 machines whose registered recipe logic reports energy consumption, with
  2 machines whose value is null.

Across the exact operation-owner and machine subjects, the current graph
contains 1,299 unique candidate condition paths:

- 1,026 `has_constraint` paths;
- 237 `uses_energy` paths;
- 13 `configured_by` paths; and
- 23 grouped `requires` paths.

Because one execution machine can participate in several retained operations,
the operation-context projection contains 7,173 candidate occurrences:
7,150 direct paths and 23 grouped slot paths. Those occurrences resolve to the
1,299 unique runtime relationships above. Both counts are retained so that an
operation receives all of its applicable candidates without inflating the
number of distinct runtime rules.

The constraints comprise 247 each of
`mapped_recipe_configuration`, `effective_method_owners`,
`cleanroom_recipe_envelope`, and `output_fit_trimming_and_voiding`; 9
`implicit_steam_energy_carrier` constraints; 7 `gt_recipe_property`
constraints; and one each of `no_energy_recipe_logic`,
`ambient_water_formula`, and `infinite_water_supply`; plus 19 exact
`multiblock_structure_pattern` constraints.

Every finite recipe owner retains observed `eut` and `duration` values. The 190
procedural owners do not receive fabricated recipe EU/t. The pilot contains no
direct `produces_energy`, `spawns_in`, or structure-resource path on these 300
owners, which is an observation about this bounded slice, not a claim that
those requirement classes do not exist.

These 1,299 paths are a candidate census, not 1,299 admitted player
requirements. For example, a `configured_by` relation can identify a registry
domain without imposing an operational prerequisite, and output-voiding
semantics are execution behavior rather than necessarily infrastructure.

## Production answer proof

The production player and developer answers both return `result_status =
answered` with an explicitly `partial` infrastructure projection. Each retains
300 operations, 1,299 candidate relationships, and 7,173 candidate
occurrences. Admission produces 480 typed definitions: 448 resolved, 32
partial, and none unresolved. By type these are 435 energy/voltage definitions,
19 machine structures, 14 cleanroom configurations, 9 steam requirements, 2
environment requirements, and one exact no-energy result.

The 7,173 candidate occurrences divide into 1,568 admitted and 5,605 excluded,
with no unresolved disposition. All 19 selected registered structure
definitions are resolved. The only partial definitions are 32
formed-energy-input cases across 16 controllers whose registered prototype tier
is null. Twenty-three under-tier prototype/recipe combinations are separately
retained as resolved rejections, not confused with unknown capacity.

The infrastructure projection cites 15,544 exact evidence IDs. The whole
player answer embeds 15,578 evidence records and has stable canonical SHA-256
`8b36fb07df75e3ba9a7d3030bcfc07ae43d50834c628e67d3ad1e7e2e7a84086`.
The developer answer uses the identical infrastructure projection, embeds
15,932 evidence records, and has stable canonical SHA-256
`3496d53391b4a248a5ab274a795107b2d37aba4a441eb0a2b1cc188589f98218`.
Independent repeat executions were byte-identical for both audiences.

## Candidate path collection

The query implementation retains candidate paths before applying an admission
policy. It collects, in exact scope:

- recipe/rule attributes such as EU/t, duration, and recipe properties;
- machine registration attributes such as tier, controller kind, exact
  prototype class, and recipe-logic class;
- `has_constraint`, `uses_energy`, `produces_energy`, `configured_by`,
  `defined_by_resource`, and `spawns_in` paths from operation owners and
  execution machines;
- `requires` slots already retained as non-consumable route requirements;
- `affects` slots and procedural boundaries that express world, entity,
  routing, signal, or machine state;
- exact configuration or structure resources only when a graph relation binds
  them to the enforcing owner; and
- unresolved world-dependent checks without invoking them on a prototype.

Collection is not admission. Each predicate and constraint kind needs an
explicit versioned rule defining its infrastructure type, applicability,
cardinality, and source-evidence requirement. Unknown kinds fail closed.

## Source-semantic authorities

The source pass creates structured citations rather than embedding prose in
the projection. A citation contains `source_id`, exact revision, repository
path, symbol, line span, and file SHA-256. At minimum, the first pass covers:

| Domain | Primary source authorities |
| --- | --- |
| tier names and numeric voltage limits | GTCEu `gregtech.api.GTValues` and exact tier/voltage utilities |
| recipe EU/t admission, overclocking, parallelism, and cleanroom checks | GTCEu `AbstractRecipeLogic` plus the selected effective subclass/controller |
| multiblock formation, repeat counts, block predicates, and ability limits | GTCEu `FactoryBlockPattern`, `BlockPattern`, `TraceabilityPredicate`, `MultiblockControllerBase`, and each selected controller's `createStructurePattern` |
| SuSy structures, hatches, custom properties, and controller checks | exact SusyCore controller, capability, recipe-property, and multiblock-part symbols |
| registry and recipe lifecycle | Forge `Loader`, `GameData`, registry events, and the pack/Groovy stage declarations that mutate final state |
| item/fluid/world capabilities | Forge capability registration and the exact GTCEu/SusyCore capability consumer |
| dimensions and world state | Forge `DimensionManager`, GTCEu worldgen authorities, SusyCore planet/dimension registries, and exact pack configuration |
| effective configuration | Forge configuration semantics, the owning mod's config field, and the captured value or exact config artifact used by this snapshot |

Forge is cited where it supplies or orders the substrate. It must not be cited
as the owner of GTCEu EU semantics merely because GTCEu executes inside Forge.
Likewise, a SuSy controller that calls a GTCEu pattern primitive has separate
declaration and enforcement owners. The developer projection retains the call
chain rather than assigning the entire behavior to the most visible class.

The source registry is implemented in
`infrastructure-source-authorities-v1.json`. It contains 49 exact citations
across 45 files—5 Forge citations, 12 Gregicality Multiblocks citations, 18
GTCEu citations, 9 SusyCore citations, and 5 pack citations—grouped into 12
semantic authority sets. Ordinary validation cross-binds every citation
revision to the active source lock. The explicit fidelity verifier additionally
reads the pinned Git bytes and verifies every file SHA-256, inclusive line
span, and in-span anchor. These authorities are inputs to normalization; their
existence alone does not admit a candidate condition or close its evidence
path.

Admission is implementation-specific. The ordinary EU/t tier rule applies only
when the captured effective `calculateOverclock` owner is GTCEu
`AbstractRecipeLogic`; another owner fails closed until its own source policy is
registered. Likewise, the pilot condenser's no-energy result cites the executed
SusyCore `NoEnergyMultiblockRecipeLogic`, not GTCEu's behaviorally similar but
unexecuted `PrimitiveRecipeLogic`.

## Typed projection contract

`infrastructure_requirements` contains:

- `status` and explicit player-state/completeness assumptions;
- `operations`, keyed by origin, route ID, and subproblem ID;
- exact operation owner and execution-machine subjects;
- typed requirement groups for energy carrier and rate, named/numeric voltage,
  machine structure, formed capability or hatch, environment, dimension,
  configuration, and other procedural state;
- applicability (`always`, `conditional`, `alternative`, `not-applicable`, or
  `unresolved`) and its exact condition;
- source-semantic explanations and ownership for developer questions;
- unresolved candidate paths, unknown policy kinds, and missing source
  bindings;
- inherited material-route and construction-route truncation; and
- the exact union of runtime-mechanics, pinned-source, and any
  curated-interpretation evidence IDs used by returned assertions.

Requirements remain grouped. Multiple structure blocks or capabilities can be
jointly required, while legal hatches, energy inputs, or dimensions can be
alternatives. A named voltage tier is a derived label over a cited numeric rule;
the numeric EU/t and voltage values remain present.

## Known capture and modeling gaps

The current graph and policy deliberately retain the following boundaries:

- controller `createStructurePattern` results are now normalized without
  invoking world checks or formation, so they establish registered pattern
  semantics but not placed-machine validity;
- the 12 selected Gregicality Multiblocks controller patterns are now bound to
  an exact admitted v1.2.11 source revision and method citation;
- copied base alternatives now retain lower-level `gt_any` and `gt_air`
  identity even when the outer traceability container is constructed;
- 32 of 1,867 captured simple-pattern alternatives contain an unrecognized
  closure value and remain partial for any future selected pattern that depends
  on one, rather than being guessed;
- preview candidates are recorded as non-exhaustive evidence; 269 alternatives
  are explicitly non-enumerable even when their captured predicate fields are
  otherwise exact;
- `affects` paths are retained as uninterpreted candidates, but the first typed
  policy does not yet claim that every procedural state slot is infrastructure;
- offline structure/config artifacts are inventoried but generally lack exact
  consumer/enforcement links;
- the effective final clean-multiblock value is captured and source-bound, but
  defaults, live values, and world-save overrides are not yet distinguished
  for every other configuration rule;
- prototype capture cannot prove a particular placed multiblock is formed,
  supplied, chunk-loaded, dimension-valid, or connected to a sufficient power
  network; and
- both route forests are intentionally truncated, so the pilot cannot claim a
  complete global infrastructure bill.

These are fidelity gaps, not presentation tasks.

## Formed-world evidence boundary

Closing the 32 remaining partial definitions for the 16 null-tier controller
prototypes requires disposable, version-bound world fixtures and the ordinary
dedicated-server formation lifecycle. Calling a check method on an unplaced
prototype does not close this boundary.

Each fixture manifest must bind the snapshot and physical side, exact world
save or construction-input digest, dimension and coordinates, controller
identity and class, facing, registered pattern identity, placed block states,
and installed multiblock parts. The observation must retain both the attempted
fixture and the server result:

- formed state before and after the ordinary structure check;
- matched pattern predicates and observed ability/part counts;
- exact energy-input parts, their registered tiers, and the live aggregate
  input-voltage/capacity boundary seen by the formed controller;
- effective recipe-logic owner plus relevant configuration, dimension, chunk,
  and environment state; and
- diagnostics and lifecycle events needed to distinguish an invalid fixture
  from unavailable instrumentation.

Normalized `placed_machine_observation` facts relate the fixture,
controller prototype, placed controller, matched registered pattern, installed
parts, and accepted energy inputs without replacing any of those identities.
Positive and negative claims must remain scoped to the exact fixture. A failed
formation can prove that one construction was rejected; it cannot prove that
the controller has no legal construction. Likewise, one accepted hatch layout
does not establish the full legal alternative set.

The boundary closes a null registered prototype tier only when the formed
controller exposes an exact, source-consistent energy acceptance boundary.
Otherwise the definition remains partial with a more precise world-state gap.
The wider bounded-route and byproducts/recycling frontiers remain separate
queries and must not be inferred from these fixtures.
