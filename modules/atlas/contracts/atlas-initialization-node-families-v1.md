# Atlas initialization observation node families V1

Atlas projects an owner-admitted Axiom retained report into navigable records and
relationships. Axiom owns the original execution and observation meanings. Core
owns retained custody and access. Atlas owns this projection; it does not import
the Axiom execution implementation, initialize game classes, or require a pack
profile to read these records.

The projection consumes the full retained `report` section and one explicitly
selected side (`single`, `baseline`, or `candidate`). Source, configuration,
runtime, lifecycle and observer limitations remain attached to that historical
snapshot. Reading it does not establish the state of a running game.

## Identity and original values

All node kinds begin with `initialization-`. An occurrence's semantic key hashes
the snapshot identity, selected side, exact JSON pointer and node kind. The same
spelling, recipe digest or native numeric ID in another snapshot or catalog does
not identify the same occurrence. These nodes are not the `gt-recipe` nodes used
by Atlas's finite recipe impact model.

Every node retains `family`, `label`, `json_pointer` and `raw_value`. Collection
owners retain metadata and declare `projected_fields` for their child collections,
with each collection's original type and count in `projected_collections`;
child records preserve each original value and its key or ordinal. Shallow
metadata collection owners instead declare `collection_type` and `record_count`.
They retain their full original value. Empty collections and scalar values remain
on their owner, so an explicitly empty array cannot become an absent field.
Thus a large native-value catalog is not copied into every parent node. Unknown
fields remain labeled retained metadata records, without invented domain edges.

Every evidence row binds `snapshot_id`, `side`, `section: report`,
`record_key: value`, `json_pointer`, and `observation_kind: observed-storage`.
This means the graph reads a retained observation. The original record explains
whether that observation was a stored field, invocation trace, identity check or
selected native getter result. Atlas performs none of those native operations.

Typed integers, floating-point bit strings, NBT, amounts, chance fields, ordering,
duplicates, multiplicities, incompleteness and native errors retain their original
representation. There is no count removal, NBT normalization, matcher replay or
resource coalescing across incompatible meanings.

## Families and defensible relationships

| Family | Graph records and relationships | Boundary |
| --- | --- | --- |
| Report, source, artifact, transformation | Selected report/result fields, captured source scope, script index, dispatch observations, artifact identities, mixin/transformer observations | Captured input or a defined class is not proof every source statement or method executed. |
| Lifecycle and mods | Stage records, observed mod states, explicit earlier-stage storage and inherited-field links | Host return is not error-free initialization. A listener in a dispatch list is not proof it returned. Inherited storage is not another invocation. |
| Configuration | Declared configuration owners and observed original class/file bindings | A binding is not all-file applicability or a replay of configuration predicates. |
| Material registries and fingerprints | Native registry rows; material/fluid/generated-form inventory member and fingerprint records | Complete membership and selected-state hashes do not contain all material/property/form values. Failed catalogs retain their failure. |
| Selected material witnesses | Material values, declared components, observed flags/property keys, attached property values, queued/stored fluid bindings | Composition is a stored declaration, not a production recipe. Unknown property values remain unknown; queued fluids are distinct from registered bindings. |
| Generated forms | Selected form witnesses, observed generated forms and observed unifier selections | Generated membership, eligibility and selected unifier result are different fields. Numeric versus named material registry discrepancies remain intact. |
| Custom items | Original parent item, variant and component records | Stored component presence does not prove later item callbacks or game behavior. |
| GT maps and recipes | Map storage, lookup recipe-value catalogs, categories, category-to-lookup references, and separate outside-lookup catalogs; stored ingredient and output rows | Category-only entries are not active lookup recipes. Equal stored recipe values retain multiplicity, not manufactured object identities. Stored selectors, chance logic and properties are not executed matching or success. |
| Crafting | Named recipe records, catalog-local native values, reference edges, separate name-iteration and numeric-lookup order records | Object references preserve aliasing and cycles. They are not ingredient acceptance or recipe dependency edges; ordering alone does not identify a winning recipe. |
| Furnace | Separate stored smelting, experience, wildcard-time, metadata-time and fuel-conversion rows; defaults remain on the owner | The observer did not call smelting/experience lookup. Retained map order and wildcard selectors remain relevant evidence, not an evaluated selection. |
| Ore dictionary | Current membership for observed names and individual members; separate `backup`/`scripted` reload records | Groovy reload storage is not an operation log or the complete ore dictionary. Recorded names and current membership are different evidence. |
| Callbacks | Stored callback/source closure identity, captured values and native factory observations | A retained function, source line or factory result does not prove deferred function invocation or registration causation. |
| Biomes, worldgen bindings, hardness | Observed biome rows, disabled names, saved worldgen definition bindings and per-biome weights, stored block hardness | Selected biome-weight function results are explicitly retained by Axiom; they do not establish terrain/world generation. Stored hardness is not world-dependent hardness evaluation. |
| Diagnostics and retained metadata | Original errors, findings, locations, attribution and remaining native fields | Locations keep their original precision. Final state does not manufacture a creator/remover source span. |

In particular, the graph does not pair a distillery or scanner recipe with a
supposed generating parent by content. Original native callbacks may leave
generated children after parent rejection or removal. Operation provenance needs
its own observed identity and outcome.

## References, completeness and streaming

`nativeValueRef` resolves only against the enclosing crafting `nativeValues`
catalog. A reference is exactly an object containing that string field. Missing
or malformed references are refused. Each reference occurrence retains its own
JSON pointer; repeated references share a target. Reference cycles terminate
because projection walks encoded records without recursively expanding targets.
GT category references must resolve in the same map's lookup catalog. Crafting
order entries must resolve in the same crafting registry.

Compact stage snapshots retain `inheritedKeys` and `values`. The graph links each
inherited key to the exact enclosing stage field (or its declared execution-level
storage after compaction) and refuses a missing target.
Fields moved by Axiom's snapshot encoding keep their actual execution-level
pointers. No synthetic decoded source pointer is presented as a retained path.

The node and edge streams hold references to one admitted report and are
repeatable while that report remains immutable. Cancellation is checked during
both streams. There are no arbitrary recipe, reference-depth or node-count cuts
in this adapter. Custody and graph-bundle encoding limits remain the caller's
responsibility; failures cannot silently truncate an observation.

Family coverage counts describe projection, not native qualification. Before a
complete node stream they are `not-scanned`; afterward a family is `projected` or
`not-observed`. `projected` can include an original unavailable/incomplete catalog
or failed native check. `not-observed` does not claim an empty registry or decide
whether the missing family was applicable. The original statuses, affecting gaps,
scope obligations and result outcome remain authoritative.

The retained report's supported envelope does not imply support for arbitrary
nested native schemas. Known GT, crafting, furnace, configuration, ore, biome,
fingerprint, property-state and compact-stage observation families require their
declared V1 schema. An unknown or missing required nested schema refuses this
domain projection; it does not turn that family into an empty catalog. An ordinary
failed report with `native: null` can still project its report metadata and
findings, with native families remaining `not-observed`.

## Source-backed ownership

The public Axiom [initialization MVP](../../axiom/spec/initialization-mvp.md)
defines current original execution and retained-result scope. The selected source
revisions are recorded in its [program source lock](../../axiom/sources/material-program.lock.json)
and [upstream baseline](../../axiom/sources/supersymmetry.lock.json): Supersymmetry
`3e83cd7bad57bd4c424de4e6cc707ab02fe32f54`, GTCEu
`9fe140febe8747bbe2f06dfd570421331ec06f4b`, Susy-Core
`2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a`, and GroovyScript
`6a3340814ce9527d23c2b88cdfd340b4f146b4c4`. These describe the selected producer
context, not unconditional support for every release of those projects.

Observer definitions are in Axiom's
[retained section declarations](../../axiom/src/workbench_axiom/retained_snapshots.py),
[original lifecycle collection](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeGroovyClassSpace.java),
[registration fingerprints](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRegistrationEffects.java),
[material properties](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeMaterialPropertyState.java),
[generated forms](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeGeneratedContentObservations.java),
[effective GT/ore observations](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeEffectiveRecipeObservations.java),
[crafting/furnace storage](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeVanillaRecipeObservations.java),
[native value encoding](../../axiom/jvm/src/materialRuntime/java/research/orthrus/axiom/materialhost/NativeRecipeValues.java),
and [stage snapshot encoding](../../axiom/jvm/src/main/java/research/orthrus/axiom/NativeStageSnapshots.java).
These source anchors explain the relationships; retaining a graph does not itself
execute a new original-versus-observed conformance run.
