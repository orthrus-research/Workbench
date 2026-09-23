# Finite GT recipe projection V1

The Supersymmetry profile supplies the explicit `workbench.recipe_graphs`
extension, API version 1, implemented by
[`recipe_graphs.py`](../src/workbench_profile_supersymmetry/recipe_graphs.py).
Its output is an Atlas categorical graph bundle V2. This profile interprets
GT records, Crucible verifies retained capture custody, and Atlas constructs
and queries the graph. No game launch or source mutation occurs during import.

```bash
workbench atlas recipes import-capture /path/to/capture \
  --input-manifest /path/to/input-manifest-v1.json \
  --pack-profile supersymmetry --output /path/to/new-graph --json
```

The profile, Atlas, and Crucible packages must be installed and the profile
admitted. The output must be a new path. A failed import does not replace an
existing output or modify the capture. The source-byte bound covers the
manifest, bound input manifest and every payload, including unselected ones.

## Admission and binding

The Crucible reader verifies the raw V1 manifest, completion marker, all
payload hashes and sizes, canonical category records and result seals, and
two identical completed samples. Four categories must be complete and stable
with no unsupported values or diagnostics, at `post-start-end-tick`:

| Adapter ID | Category ID |
| --- | --- |
| `gt-recipes` | `transformation-recipe` |
| `gt-recipe-maps` | `transformation-recipe-map` |
| `gt-meta-tile-entities` | `machine-core` |
| `gt-machine-recipe-maps` | `machine-transformation-binding` |

The input manifest must match the capture hash and bind a Supersymmetry
foundation target and a Cleanroom foundation target, or meet the exact branch
protocol below. The graph retains these
original bindings, physical side, checkpoint and candidate-lock digest. Its
evidence binding retains the raw manifest, category seals and sample summaries,
input manifest, and adapter source digest. The digest covers the canonical map
of SHA-256 hashes for `recipe_graphs.py`, `finite_item_matching.py`,
`finite_ordinary_item_matching.py` and `item_names.py`; that map
is retained as `projection_sources`. Recipe evidence points to the exact
captured record ordinal and hash. Content integrity does not independently
establish trusted execution, current applicability or release qualification.
The projection format and source digest also form part of graph scope, so
different interpretation revisions cannot be compared as a runtime change.

The profile checks consumed recipe fields and types, recipe semantic digests,
unique contiguous duplicate ordinals, map occurrence counts and machine/map
references. Unsupported shapes, mismatched identities, missing categories or
incomplete required categories refuse the import instead of producing a partial
recipe graph.

### Explicit Forge branch admission

`workbench-supersymmetry-branch-observation-input-v1` additionally admits the
circuits-overhaul observation target below. It does not admit arbitrary Forge
packs or reinterpret load-complete as post-start evidence. All four required
categories, and each optional category when present, still require two
stable `post-start-end-tick` samples.

- `pack_source` is exactly `{revision, tree}`, with revision
  `575b540c64f2d2e33222660da1a78694bcae3eb9` and tree
  `7bda9fceffeee614f7c694c0d908ec76e72e34f8`.
- `platform` is exactly `{minecraft_version: "1.12.2", loader: "forge",
  loader_version: "14.23.5.2860", java_major: 8}`.
- `runtime_artifacts` is exactly the three artifact hashes below plus
  `groovyscript_sha256` =
  `07617b7ce9170a857199bd61d730a0db3af2685cf83d31734a2d4b628fda7533`
  and `susy_core_sha256` =
  `ec9b56070f8788b63f5d381c765cd05ed66ba42755e1f87ad2eb6605285a5a46`.
- `pack_binding_id` is `supersymmetry-branch-observation:sha256:` followed by
  SHA-256 of canonical `pack_source` JSON. `platform_binding_id` is
  `forge-branch-observation:sha256:` followed by SHA-256 of canonical
  `{platform, runtime_artifacts}` JSON. Canonical JSON uses sorted object keys,
  no whitespace, and unescaped non-ASCII characters.
- `capture_id`, `launch_id`, `physical_side`, `candidate_lock_sha256`, and
  `adapter_profile_sha256` must agree with the capture manifest. The latter
  digests bind the retained dependency lock and observation protocol inputs;
  equality is not independent verification of a publisher's execution claim.
  This branch path requires `physical_side: "dedicated_server"`.

Other input metadata, such as repository URL and invocation custody, remains
retained. It cannot change the exact consumed target fields. Admission verifies
the declared target and captured bytes; successful import alone does not prove
that native startup ran, that transformed classes equal the original archive
bytes, or that this target is qualified for release.

## Graph meaning

Recipes use `recipe_map|semantic_sha256|duplicate_ordinal` identities and preserve
lookup and category membership. Maps with no finite rows remain present. Machine
nodes represent captured prototypes and their declared map bindings; a map
binding does not prove machine formation, operating capability or tier coverage.
Recipe properties include `captured_input_counts: {item, fluid}` with the exact
original selector-array lengths, including explicit zero counts.

Selector amounts may be zero: GT's builder admits these, and effective captures
can contain zero-amount ore slots with no representatives. Their original slot,
quantity and matching evidence remain present. Atlas routes report the quantity
as unresolved; importing it does not establish a free ingredient, a required
positive quantity, or an executable operation. Negative or non-integer amounts
still refuse import. Captured stack quantities remain strictly positive.

Item resources use `captured-item-variant-v1`: registry name, metadata, item
damage and captured NBT determine identity. Fluid resources use
`captured-fluid-variant-v1`: fluid name and captured NBT determine identity.
Quantity is excluded, allowing input and output amounts to refer to the same
resource. The graph retains amount, original observed stack, input ordinal,
reusability, and output chance/boost/logic on the relevant selectors and edges.
Chance values are retained observations and are not converted into guaranteed
yields or expected production rates.

Without an admitted ordinary witness below, `GTRecipeItemInput` selectors have `acceptance_complete=false` and
`ordinary-item-matching-closure-not-qualified`. Original matching compares item
metadata, full NBT equality and item capabilities, while resource identity also
retains item damage and the capture omits capabilities. Its stored
representatives are copies and cannot establish even their own acceptance from
these fields alone. They use `observes-gt-item-input-representative` and retain
the matching limit `item-capability-compatibility-not-captured`. Routes retain
these original inputs and their gaps, but do not expand them as qualified
accepted paths. Exact resource identities remain unchanged.

Default `GTRecipeFluidInput` selectors expose `accepts-gt-fluid-input` for their
captured exact fluid and tag alternatives. If a tag contains a float or double,
they instead retain observations with `acceptance_complete=false` and
`floating-nbt-equality-not-qualified`: original NBT equality equates signed zero
and can reject copied NaNs, while the graph preserves their exact encodings.
This rule includes floating tags nested inside lists or compounds. The
projection does not execute the fluid matcher.
Ore-dictionary inputs can ignore NBT, and circuit inputs compare their
configuration while allowing additional NBT. Their captured representatives do
not enumerate that broader matching set. These inputs, unknown runtime classes,
custom NBT matchers, wildcard item metadata and empty item selectors have
`acceptance_complete=false` and explicit `acceptance_gaps`.
Unqualified representatives use `observes-gt-item-input-representative` or
`observes-gt-fluid-input-representative`, preserving inspection without claiming
exact matching. An empty exact-alternative set on such a selector is unknown
matching evidence, not proof that its input cannot be supplied.

## Optional observed item names

The optional adapter `gt-item-names`, category `reference-item-names`, records
actual `GroovyScriptModule.getMetaItem` results at the same capture checkpoint.
It connects source spellings such as `metaitem('circuit.microprocessor')` to
observed stacks without deriving metadata from declaration order, translated
display names or arithmetic. Missing name evidence preserves the existing
projection and does not establish that a symbol is absent.

Each record has exactly these fields:

| Field | Meaning |
| --- | --- |
| `record_type` | `gt-item-name-binding` |
| `query` | Exact argument passed to the native resolver |
| `namespace`, `name` | Original resolver's split of that argument |
| `authority` | `GroovyScriptModule.getMetaItem` |
| `resolution` | `cache`, `meta-tile-entity`, or `unresolved` |
| `stack` | Exact captured item stack, or null only for `unresolved` |
| `gregtech_sha256` | Original GT archive hash listed below |
| `resolver_class_sha256` | Original `GroovyScriptModule.class` entry hash: `22b4f9b8261594346cbc635b415fc1a3b2b6667cfd65cf4484680ef72c55a891` |

The resolver first uses its existing namespace/name cache and then the machine
registry fallback. Capture must not clear or rebuild that cache. Qualified
cache and machine names, and unqualified `gregtech` names, can be observed;
the bound input manifest can additionally supply unique `item_name_queries`.
When that list is present, the category must contain a result for every query,
including an explicit `unresolved` row when the resolver returns null.
Duplicate query rows, conflicting results for equivalent queries, wrong
original-byte pins, malformed stacks and mismatched native name splits refuse
the import. The pinned splitter defaults to `gregtech` and uses an explicit
prefix only when the colon's index is greater than one; the observed split must
preserve that behavior. Native archive hashes do not attest transformed JVM
class bytes or independently prove execution.

Matching resource nodes gain `properties.observed_item_names` entries containing
the query, namespace, name, authority and resolution, with exact raw record
evidence. Existing recipe search includes these properties. The alias is
attached only when the returned stack's count-independent exact identity
already occurs in the recipe graph; different NBT variants do not inherit it.
Aliases do not change resource IDs, recipe occurrences, accepted alternatives
or recipe edges, and do not add resources outside the captured recipe domain.

`evidence_binding.item_name_observations` retains binding and annotation counts,
explicit unresolved queries, and observed bindings outside the recipe resource
set, each with raw evidence references. Its scope is observed queries at that
checkpoint. Neither an absent query nor a null lookup proves item acquisition,
recipe survival, source registration causation or whole-game absence. Selecting
a source recipe still requires its map, full inputs and outputs, quantities,
conditions and occurrence evidence; a symbolic item binding alone is not a
recipe identity.

## Optional executed finite item matching

The optional adapter `gt-item-matching`, category
`transformation-item-matching`, supplies native predicate observations. Its
absence preserves the preceding representative-only interpretation. If present,
the category must pass the same complete/stable capture admission as recipes;
malformed evidence refuses the import. Missing individual selector witnesses
retain the previous incomplete interpretation.

The model is `gt-2.8.10-forge-1.12.2-finite-item-matching-v1`, implemented in
[`finite_item_matching.py`](../src/workbench_profile_supersymmetry/finite_item_matching.py).
It covers only exact `GTRecipeOreInput` and `IntCircuitIngredient` classes with
no custom NBT condition or matcher. Runtime subclasses are not admitted merely
because they share a name suffix. GroovyScript 1.4.3's `OreDictIngredient` becomes
a GT ore input during recipe building; it is the captured GT input's effective
cached `getInputStacks()` array that governs this observation, not a later
ore-dictionary membership query.

### Native category records

There is exactly one header, with exactly these fields:

```json
{
  "record_type": "gt-item-matching-domain",
  "model": "gt-2.8.10-forge-1.12.2-finite-item-matching-v1",
  "domain_scope": "captured-gt-input-output-item-variants",
  "domain_sha256": "<64 lowercase hex digits>",
  "domain_count": 0,
  "artifacts": {"gregtech_sha256": "...", "forge_sha256": "...", "minecraft_sha256": "..."},
  "class_sha256": {"GTRecipeOreInput": "...", "IntCircuitIngredient": "...", "MetaItem$MetaValueItem": "...", "OreDictionary": "..."}
}
```

`artifacts` and `class_sha256` must exactly equal these original byte pins:

| Key | SHA-256 |
| --- | --- |
| `gregtech_sha256` (2.8.10-beta) | `54744bb11ea4679df4b55846d8073e9bb2ef8c1c53aae8aee41cda3fc22d927e` |
| `forge_sha256` (14.23.5.2860 runtime) | `cd3fbf85d7ca744507fd6a37a41b90122d43e616a4f8962332b1b658655e8a64` |
| `minecraft_sha256` (1.12.2 server) | `fe1f9274e6dad9191bf6e6e8e36ee6ebc737f373603df0946aafcded0d53167e` |
| `GTRecipeOreInput` | `a951fcaa6d5acc2c92401df58ee6cc5f66ff4183d8ac90f6b04b7716d5c0cf17` |
| `IntCircuitIngredient` | `490eccd082dc61e5ccd5f0296701df6ddaa5a916d376172838930c065201bf18` |
| `MetaItem$MetaValueItem` | `3804fcee35351de0da57077d24fcb3e31485d9d26e9fc45a7aaf7c7875782965` |
| `OreDictionary` | `41e31fd802528cf90431e60ed18f0b5ba01d3c997f227110e95b2d82a87e735f` |

Each qualified selector has exactly one witness with these exact fields:

```json
{
  "record_type": "gt-item-matching-selector",
  "recipe_map": "<captured map>",
  "semantic_sha256": "<original recipe digest>",
  "duplicate_ordinal": 0,
  "recipe_record_sha256": "<original occurrence record digest>",
  "selector_ordinal": 0,
  "matcher": "ore-dictionary",
  "matching_configurations": null,
  "integrated_circuit": null,
  "accepted_domain_ordinals": [0, 2]
}
```

For `integrated-circuit`, `matching_configurations` is the actual stored Java
`int` field, and `integrated_circuit` is exactly `{registry_name, item_damage}`
for `MetaItems.INTEGRATED_CIRCUIT`. All circuit witnesses must agree on that
item identity. Display-stack NBT alone does not establish the stored field.
The native producer calls the original selector's `acceptsStack` on a **copy**
of every domain stack; the circuit getter can add `Configuration: 0` to a
tagless input. `accepted_domain_ordinals` is the sorted, duplicate-free list
of true results. Empty is a valid exhaustive result, not missing evidence.

### Domain and independent check

Enumerate canonical `gt-recipes` records in order. Within each recipe visit
`item_inputs` and their representative arrays, then `item_outputs`, then
`chanced_item_outputs.entries`. Deduplicate by exact
`{registry_name, metadata, item_damage, tag}` identity, retaining the first
occurrence and original quantity for provenance. Do not collapse NBT globally.
The domain hash is SHA-256 of the canonical JSON list of those first-occurrence
locators, each exactly `{recipe_record_sha256, pointer}`. Pointers have the form
`/records/N/recipe/item_inputs/I/item_stack_representatives/J`,
`/records/N/recipe/item_outputs/J/value`, or
`/records/N/recipe/chanced_item_outputs/entries/J/value`. The count and hash
must match a fresh complete enumeration; selector representatives alone cannot
stand in for the domain.

The profile independently predicts every qualified selector's accepted set:

- Ore inputs require item registry identity and either equal item metadata or
  wildcard metadata `32767` on the **target** cached ore stack. Target NBT and
  quantity do not constrain this default predicate. Candidate wildcard metadata
  is not a symmetric wildcard.
- Circuit inputs require exact integrated-circuit item and damage, plus the
  stored configuration compared with Minecraft's `getInteger("Configuration")`.
  Missing/non-numeric configuration reads as zero. Numeric tags use their
  original Java conversions, including signed long narrowing and floating
  `MathHelper.floor` behavior. Additional NBT is allowed. Typed captured NBT
  is validated, including signed 64-bit long arrays (tag 12); unsupported or
  inconsistent numeric encodings refuse admission.

In this witnessed domain, every list tag (tag 9) is exactly
`{tag_id: 9, element_type, value}`. `element_type` retains the original stored
list type, including empty lists, and must be an integer from 0 through 12.
Nonempty children must all have that exact tag type. An empty list previously
holding integers can retain type 3 and is distinct from an empty type-0 list
under original NBT equality. Missing list type refuses optional qualification
rather than merging those identities. Other typed tags are exactly
`{tag_id, value}`. Captures without the optional matching category retain their
existing interpretation; this rule does not rewrite historical NBT.

These rules are grounded in GT revision
`9fe140febe8747bbe2f06dfd570421331ec06f4b`,
`GTRecipeOreInput.getInputStacks/acceptsStack`,
`IntCircuitIngredient.acceptsStack/getCircuitConfiguration`,
`MetaItem.MetaValueItem.isItemEqual`, and Forge 2860's
`OreDictionary.itemMatches`. Groovy conversion is in
`RecipeBuilder.ofGroovyIngredient`; the original Groovy ingredient is at
revision `6a3340814ce9527d23c2b88cdfd340b4f146b4c4`. Minecraft conversions are
checked against the pinned server bytecode, including NBT primitives and
`MathHelper`. Source agreement and hand-written fixture results do not replace
executed native qualification evidence.

Any native/predicted disagreement, duplicate or unbound witness, invalid domain
ordinal, unsupported matcher, or mismatched original byte pin refuses import.
Successful selectors expose `acceptance_complete: true`, the model above,
`acceptance_domain: {scope, sha256, count}`, and
`matching_state: {matcher, matching_configurations, integrated_circuit}`.
Their original representatives remain in `captured_item_stack_representatives`;
only admitted domain alternatives emit `accepts-gt-item-alternative` edges.
Those edges contain `domain_ordinal`, original `observed_stack`, amount and
reusability, with both native witness and candidate-source evidence. There are
no representative observation edges on a qualified selector. Zero acceptance
edges with complete matching means no accepted variant **in this captured
domain**; it does not prove global unsupplyability. Legacy analysis surfaces
that cannot distinguish complete-empty from missing evidence must retain their
conservative unknown result.

This finite predicate observation does not execute whole-recipe lookup,
consumption, output generation, machine processing or progression. It does not
qualify capability-sensitive ordinary item inputs, custom NBT matchers, other
recipe families, or candidate stacks absent from the captured universe.

## Supported limits

- The graph covers captured finite GT recipe occurrences. Dynamic rules,
  crafting, smelting, other mods, quest progression and external acquisition
  require separate adapters or evidence. Zero finite rows do not prove no
  runtime behavior.
- Recipes and their finite machine/map bindings support inspection and bounded
  dependency analysis. Original source attribution and causation are not
  synthesized from final records.
- Cycles, reusable inputs and alternate producers do not prove bootstrap
  supply, reachability, energy feasibility or material balance. A solvent loop
  can require an external seed even when regeneration is visible.
- This identity model does not supply the legacy quantity-bearing item
  resource contract used to resolve newly proposed item stacks. New item plan
  resolution, ore-key expansion and application/replay qualification are
  outside this projection milestone. Existing recipe inspection and removal
  evidence must not be presented as acceptance of those new-item plans.
- Importing a historical capture preserves its historical scope. No live
  runtime, fresh capture, installed IDE behavior or whole-pack support is
  inferred from a successful import.

## Optional ordinary matching: null tags and zero serializable writers

The separate optional adapter `gt-ordinary-item-matching`, category
`transformation-ordinary-item-matching`, admits model
`gt-2.8.10-forge-1.12.2-null-tag-zero-writer-item-matching-v2`. It does not alter
the ore/circuit model. Like other optional categories it requires complete,
stable, bound `post-start-end-tick` evidence. Missing individual witnesses keep
the ordinary input's explicit incomplete interpretation. The earlier ordinary
V1 model is refused: an absent capability dispatcher alone cannot establish
zero writers when capability initialization is deferred.

Only the exact `GTRecipeItemInput` class without a custom NBT matcher or
condition is supported. The witness preserves the original ordered internal
`itemList → metaToTAGList → tagToStack` state, rather than reconstructing it from
`getInputStacks()`. Every original target must have a null stored tag and zero
serializable capability writers **after initialization**. An initialized stack
with no capability dispatcher and an initialized dispatcher with zero writers
both satisfy this condition. An uninitialized or unknown dispatcher does not.
This does not assert that the item has no capabilities: Forge's compatibility
predicate ignores nonserializable providers.

The pinned LoliASM 5.31 capability delayer changes the applicable initialization
boundary. When the original accessor reports deferred state, native observations
invoke the original
`IItemStackCapabilityDelayer.initializeCapabilities()V`, require its normal
return, then re-read `hasInitializedCapabilities()Z` and the live dispatcher.
The initializer sets its flag before calling mod providers and attachment
callbacks; a true flag after a thrown call is insufficient. Such failures are
retained and cannot produce a completed qualifying witness. Only final
`initialized` or `unknown` states are recorded, so equivalent repeated
snapshots do not differ merely because an earlier read initialized the stack.
`initialized` describes the observed effective state; it does not certify that
callbacks which ran before this observation completed successfully. A provider
already marked initialized is not reset or replayed to invent that history.
Callbacks receive live stacks, so full visible identity and count must remain
unchanged across initialization; these checks are separate from writer counts.

### Domain and occurrence custody

The candidate domain retains the same exact count-independent resource
identities and first-occurrence order as the ore/circuit model. Before
collapsing duplicate identities, however, this category must cover **every raw
item occurrence**: each input representative, ordinary output and chanced
output, in canonical recipe-record order and original array order. Each locator
is `{recipe_record_sha256, pointer}`, with the pointer rooted at `/records/N`.
The occurrence ordinal is its position in this complete list. Per-candidate
occurrence lists preserve their relative order in that list.

Each occurrence retains the original stack after initialization, a native
copy's full item stack before and after initialization, both final initialization
states, and both live serializable capability writer counts. The raw locator
provides the original stack before initialization. The profile independently
recomputes all locators, grouping, counts and digests. Missing, duplicated,
reordered or cross-bound occurrences refuse admission even if their payload
seals are otherwise valid. It checks copied count-independent identity against
the original stack for **all** occurrences, including candidates that did not
originally match. Copied counts must also equal their original occurrence counts.
All original occurrences and retained header copies must have initialized
capabilities. If any initialization state is unknown, or any identity or count
changes during copying or initialization, the category may retain its candidate
observations but cannot qualify any ordinary selector. Positive original and
copied counts remain required; this model does not establish quantity balance.

### Exact records

Every record has exactly the fields specified below. All digests use SHA-256
of canonical JSON with sorted object keys, compact separators and unescaped
non-ASCII characters. Locator hashes contain the ordered locator objects only,
not copied stack or capability fields; the sealed category binds those fields.

One `gt-ordinary-item-matching-domain` header contains:

- `record_type`, `model`, `domain_scope` (`captured-gt-input-output-item-variants`).
- `native_copy_initialization_policy`: exactly
  `initialize-source-potential-matches-otherwise-preserve-deferred-v1`.
- `domain_count`, `domain_sha256`: number and ordered first-locator digest of
  collapsed candidates.
- `occurrence_count`, `occurrences_sha256`: number and ordered locator digest
  of every original raw occurrence before collapse.
- `artifacts`: the original `gregtech_sha256`, `forge_sha256` and
  `minecraft_sha256` pins used by the ore/circuit model, plus `loliasm_sha256`
  `110741f7ddbff454dea6d55ccefa56096f25973a2ef875e2e3e207566682e45a`.
- `class_sha256`: exactly the following original archive-entry hashes:

| Key | SHA-256 |
| --- | --- |
| `GTRecipeItemInput` | `67f7d28d5fe9b0d8028482b0afb59c5d691da109bf9eceafb8af8c4c6b0e6c75` |
| `GTRecipeInput` | `3d51be9938860f213a240a58e9411eac6374d5c1b81c6a19120878d27ca7e155` |
| `CapabilityDispatcher` | `43b3b448d8325c87446e7408235757aa04a80bcb7d35eb35aed403fdd65174d7` |
| `LoliItemStackMixin` | `6084f9e59f4905324bb4a470632c8e9ac4e46d19a88f55ad789a163b3e4730fd` |
| `IItemStackCapabilityDelayer` | `b664f358750badcd595d8ce4f8dbfa368fd9b5fefad8358cd9c2a561ffe01275` |

One `gt-ordinary-item-matching-candidate` record per candidate contains:

- `record_type`, `domain_ordinal`, `occurrence_count`, `occurrences_sha256`.
- `occurrences`: the complete ordered list for that candidate. Each entry has
  exactly `recipe_record_sha256`, `pointer`, `original_initialized_stack`,
  `copy_before_initialization_stack`, `copy_stack`,
  `original_capability_initialization_state`,
  `copy_capability_initialization_state`, `original_capability_writer_count`,
  `copy_capability_writer_count`. All three stack values have the original
  item shape: `registry_name`, `metadata`, `item_damage`, `count`, `tag`.
  Each initialization state is exactly `initialized` or `unknown`. An
  initialized state's writer count is a nonnegative integer from the live
  dispatcher; an unknown state's writer count must be null. Cached constructor
  `capNBT` and an uninitialized null dispatcher are not writer-count evidence.

Each qualifying `gt-ordinary-item-matching-selector` record contains:

- `record_type`, `recipe_map`, `semantic_sha256`, `duplicate_ordinal`,
  `recipe_record_sha256`, `selector_ordinal`.
- `targets`: original ordered targets, each exactly `{registry_name, metadata,
  tag, capability_writer_count, capability_initialization_state,
  stack_before_initialization, stack_after_initialization}`. The stack fields
  have the same full item shape above and must retain identical identity and
  count. State must be `initialized`. Metadata and tag are the stored internal
  matcher fields; registry identity comes from its original item group. The
  writer count comes from the target's live stack. All tags must be null and
  writer counts zero. An explicitly observed empty target list is allowed.
- `accepted_occurrence_ordinals`: sorted unique ordinals accepted by original
  `acceptsStack(candidate.copy())`, covering every raw occurrence. Reusing an
  already executed complete result vector for the same live input object is
  permitted; testing only the first collapsed representative is insufficient.
- `accepted_domain_ordinals`: sorted unique collapsed ordinals, whose native
  outcomes must agree across every constituent occurrence.

The independent predictor compares copied registry identity and metadata with
original stored targets and requires a null copied tag. Every potentially
matching occurrence must have zero original **and** copy writer counts. Actual
native occurrence results must equal this prediction, and collapsed results
must equal the independently regrouped occurrence results. Unsupported target
state or capabilities cannot be promoted by supplying a native accepted list.
Every original predicate call still executes on a fresh copy of every raw
occurrence. Copies that could match the source-proven registry, stored metadata
and null-tag predicate must be initialized successfully and agree with retained
candidate identity, count and capability state before invocation. Copies that
cannot reach the capability comparison may remain deferred: no zero-writer
claim is made for that deferred state, and the original predicate must still
return false with unchanged visible copy state. Native checks also reobserve
targets after the complete vector. Failed checks retain selector identity and
before/after target evidence; they cannot publish a qualifying capture.

### Graph and applicability

Qualified ordinary selectors have this model's `acceptance_model`,
`acceptance_complete=true`, empty `acceptance_gaps` and empty `matching_limits`.
They retain the original representatives in
`captured_item_stack_representatives` and the ordered targets in `matching_state`.
Their `acceptance_domain` additionally retains the complete raw occurrence
count and locator digest. Accepted resources use `accepts-gt-item-alternative`
with `domain_ordinal` and exact raw evidence. Qualified selectors do not emit
observation edges that could be mistaken for additional acceptance. A proved
empty accepted set is distinct from a missing witness.

`evidence_binding.ordinary_item_matching` summarizes the model, domain and raw
occurrence digests/counts, `copy_identities_unchanged`,
`copy_counts_unchanged`, `capability_initialization_complete`,
`initialization_identities_unchanged` and `initialization_counts_unchanged`
invariants, and qualified-selector
count. Original archive hashes identify stored inputs, not transformed class
byte attestation. This finite copied-domain qualification does not establish
future inventory matching, tagged/custom capability behavior, acquisition,
world readiness, recipe execution, or registration causation. It does not
implement arbitrary capability serialization or floating-NBT equality.

## Explicit capability preparation

The exact Forge branch input may additionally declare
`observation_preparation: {policy:
"forge-loli-original-capability-materialization-v1", phase:
"before-two-effective-samples"}`. This is an explicit operation on live stacks
before two new effective observation samples. It is not a passive observation,
and it does not claim that the original and prepared item identities are equal.
The historical Cleanroom profile does not admit this declaration.

A declared preparation requires the sealed auxiliary payload
`capability-preparation.json` and a matching
`checkpoint.json.observation_preparation_policy`. Crucible's public auxiliary
reader verifies the retained file, manifest and canonical numeric-token hashes.
The profile verifies the `workbench-forge-item-capability-preparation-v1`
envelope, integer schema version 1 and all six capture/input bindings.
Its `preparation` object contains exactly:

- `policy`, `state: "complete"`, and the full `recipes_before` array.
- `recipe_bindings`, one `{recipe_map, before_record_sha256,
  after_record_sha256}` per original live recipe/map occurrence. These hashes
  form a complete bijection onto the later published recipe records.
- `objects`, one row per unique retained live stack. Each row has
  `object_ordinal`; `stack_before`, `stack_before_initializer`,
  `stack_after_initializer`, `stack_after`; the corresponding four
  `initialization_state_*` fields; `initializer_invoked`; and `references`.
  All stack values retain registry name, metadata, item damage, count and NBT.
  States are `deferred`, `initialized` or `unknown`. Invocation is required
  exactly when the local pre-call state is deferred and must return normally
  with local initialized state. A final deferred state cannot be complete.
  Unknown state remains unknown, and conveys no zero-writer assertion.
- `counts`: `recipe_count`, `object_count`, `reference_count`,
  `occurrence_reference_count`, `stored_target_reference_count`,
  `initializer_invocation_count`, `changed_object_count` and
  `unknown_object_count`. All counts are checked against the retained records.

Each reference contains `role`, `before` and `after`. A
`recipe-item-occurrence` locator has `recipe_record_sha256` and an exact raw
JSON `pointer`. Every original and later item representative, ordinary output
and chanced output must appear exactly once in this inventory. The pointed
stack must equal its object's whole-phase observation on that side. An
`ordinary-stored-target` locator contains `recipe_record_sha256`,
`selector_ordinal`, `item_entry_ordinal`, `metadata_entry_ordinal`,
`tag_entry_ordinal`, `registry_name`, `metadata` and `tag`. It retains the
original ordered matcher fields separately from the live stack. Later ordinary
matching witnesses must agree with these prepared target observations.
Before/after references preserve the original slot. Canonical sorting may
change the leading recipe ordinal or representative positions within the same
selector; it cannot move an output or target into another slot. Both exact raw
pointers remain checked. Shared references retain object aliasing.

The auxiliary evidence preserves whole-phase and local-call observations so
indirect changes caused during another object's initialization are visible.
It does not attribute every difference to a particular callback or certify
successful callbacks before observation. Identity or quantity changes remain
explicit transitions, including a null tag becoming UUID/energy NBT. Recipe
records and finite resource identities are rebuilt from the prepared state.

Graph scope declares `recipe_observation_state:
"after-capability-preparation"` and the preparation policy.
`evidence_binding.capability_preparation` retains the auxiliary filename,
SHA-256, raw-record/transition pointers, verified counts and
`identity_equivalence_claimed: false`; the large original recipe array stays
in the sealed capture. Graph limitations disclose the preparation phase.
The existing ordinary V2 model remains strict: any later initialization or
copy identity/count change still prevents qualification. Preparing original
objects does not prove that a fresh copy is equivalent or support tagged or
nonzero-writer matching outside the admitted model.
