# Native custom-item initialization

Axiom executes saved custom-item declarations through the selected original
GTCEu `StandardMetaItem`, `MetaItem` and `MetaValueItem` implementations. There is
no alternate item evaluator and no source repair. The unchanged selected pack
now returns from `RegisterMetaItems.groovy`, reaching the frozen material phase:
409 variants, the drone, all 11 batteries and final `fused_quartz_tube` declaration
are observed with no native errors or candidate-admission failures.

This is not complete pack initialization. Pack-wide item/block registry events,
remaining addon prerequisites, fluid registration and recipes remain deferred.
The reported outcome remains **incomplete**, even when these declarations succeed.

## Source and lifecycle ownership

[The immutable source inventory](../sources/material-program.lock.json) binds
Supersymmetry 0.1.16.15, GTCEu 2.8.10-beta, Susy-Core 0.1.118, GroovyScript 1.4.3
and the selected Cleanroom revision. These are selected revisions, not an
assertion about upstream latest. `RegisterMetaItems`, `classes/Battery` and
`globals/Batteries` execute as original saved Groovy, not extracted statements.

GT Core preInit posts MaterialEvent, closes material registration, posts
PostMaterialEvent, then freezes materials. The pack registers its custom-item
listener on PostMaterialEvent. Its `StandardMetaItem(2 as short)` constructor
appends the object to the original shared `MetaItem` list; `setRegistryName`
assigns its name but does **not** insert it into Forge's item registry.
Observed ownership is `gregtech:meta_item_2`: the callback runs while GT owns
the native event dispatch. Axiom does not rewrite it to a pack namespace.

`Battery.register` attaches the original rechargeable `ElectricStats` and
`BaubleBehavior(TRINKET)`, calls `setUnificationData`, sets model count and assigns
the creative tab. Native component attachment and its ordering are retained.
The observer reads declarations; it does not create stacks, invoke providers,
charge batteries, render tab icons or drain queues. Full capability injection,
charging/worn behavior, render suppliers and gameplay are not qualified.

GT initializes OreDictUnifier after PostMaterialEvent. The selected pack's
battery declarations therefore produce original Forge warnings about ore entries
created before item registry insertion. The warning text and saved locations are
retained, not hidden or repaired by reordering initialization. This observation
does not establish the final pack-wide ore dictionary outcome.

## Original classes and Groovy dispatch

The reduced MetaItem/StandardMetaItem API projections were removed. Complete
selected artifact owners and nested classes now serve both custom and generated
items. The build rejects overriding these owners, ElectricStats or BaubleBehavior.

MetaItem declares an optional EnderCore overlay interface, absent from the
selected pack. Cleanroom's original `ModAPITransformer` removes that interface
at runtime. Javac cannot run the transformer, so a separately hashed
**compiler-only** view removes the same annotation-declared interface and its
generic-signature entry. Runtime artifacts retain the original bytes and never
include this view. No placeholder interface or substitute executable body is
introduced. Existing uncomposed UI ports still throw; metadata assignment is not
rendering support.

Admission covers complete exact public descriptor families, including inherited
Forge registry-name bridge methods and both native Groovy `with` overloads.
The original DELEGATE_FIRST closure dispatch selects the method and performs
coercion; Axiom does not implement overload resolution or forward calls itself.
Only already-admitted native delegate methods are allowed. Baubles component
admission belongs to the pack context; the common GT item declarations are also
available in the bounded GT-base context.

## Important native outcomes

| Saved edit | Selected native behavior |
| --- | --- |
| Duplicate numeric metadata ID | Throws; previously registered variant remains. |
| Duplicate item name with different IDs | Both variants remain; name lookup resolves to the later variant. |
| Metadata outside the allowed offset-adjusted range | Throws before adding a variant. |
| Nonpositive maximum stack size or model amount | Throws; the old field value remains. |
| Stack-size setting greater than 64 | Accepted by this setter; Axiom invents no upper limit. |
| Negative ElectricStats capacity/tier | Factory retains these unchecked values; this is not a gameplay-validity guarantee. |
| Null component entries or null Baubles type | Native declarations retain null; the observer preserves it without throwing or inventing an error. |
| Assigning registry name a second time | Original Forge implementation throws. |
| Native error inside `with` | Groovy may log it and return; outer event/freeze can still complete. |

Consequently, a “finished” log or returned event alone cannot prove declaration
success. Native errors, partial membership, component state and the final entry
are checked together. Error highlighting comes from native saved-source frames.

## Coexistence and reporting

The bounded GT-base content lane retains existing StandardMetaItems, constructs
generated prefix items, and registers both through the original shared-item loop.
It preserves native list ordering and checks composition at phase boundaries;
it never clears a registry. Vocabulary/form observations filter generated items
instead of assuming every MetaItem is a prefix item. Fresh add/remove/restore
programs verify native Forge identity alongside completed generated content.
This does not qualify the full pack's additional block/item callbacks.

`execution.customMetaItems` reports declared variants, ordered components,
name-lookup identity and actual Forge-registration identity separately. An unused
item class remains `not-observed`; observations must not initialize it. Member
counts for guest fields are compacted by owner. Adjacent warning lines with
identical context share one diagnostic with `nativeLogEvents`; all message text,
order and locations remain, while thrown warnings and errors stay separate.
MVP transport and diagnostic size targets are temporarily suspended.

`tools/axiom_pack_meta_item_conformance.py` covers 24 complete fixture programs
plus the unchanged pack. `tools/axiom_material_runtime_smoke.py` includes three
custom/generated coexistence programs. These are regression observations sharing
the selected native dependencies, not an independent whole-pack parity oracle.
The installed workflow additionally tests saved errors without an expectations
request, developer corrections, and item additions/removals in disposable pack
copies. No automatic correction is offered.

Resource and timing targets are suspended until post-MVP optimization; see the
[MVP policy](initialization-mvp.md#performance-and-distribution). Temurin 25.0.4+7
is selected for Workbench; installed/native qualification remains separate.

Next: compose the remaining native addon initialization and generated block/item
registry prerequisites in source order, then test the same custom declarations
against the complete pack registry. Do not post registry events against
uninitialized Susy state or claim later recipe/fluid behavior from declarations.
