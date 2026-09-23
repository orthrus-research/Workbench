# Generated material blocks and ItemBlocks

This boundary composes material-prefix items with GT compressed-material blocks
and frames. It qualifies generation, native block-state/ItemBlock identity,
registration and ore membership on the existing locked Cleanroom JVM and source
target. It does not qualify the full pack, all block behavior or recipe validity.

## Source ownership

`material-blocks.lock.json` pins the GT classes, generation/registration call
sites, reached utilities, Cleanroom patches and owner-qualified GroovyScript
native mappings. `tools/axiom_material_block_sources.py` retains complete reached
methods and state. `PropertyMaterial` retains its complete class body.
`BlockMaterialBase`, `BlockCompressed`, `BlockFrame` and `MaterialItemBlock` are
member-level projections, not whole-class binary equivalence claims.

The qualified GT catalog produces 33 compressed blocks containing 152 real
variants and 16 frame blocks containing 22 real variants. Together their
sixteen-slot arrays retain 610 sentinel positions. The accompanying prefix-item
baseline remains 39 items and 2,752 variants. These are bounded GT catalog counts,
not executed Supersymmetry/Groovy addon composition.

Native `Block`, `BlockStateContainer`, `IBlockState`, `ItemBlock`, `ItemStack` and
Forge registry objects come from the selected, separately supplied Cleanroom
images. No parallel string/state model or fake world is introduced. Construction
retains the original anonymous factory subclass: its material property is already
available when the native superclass constructs its state container.

The reached factory/constructor, property, metadata, material, harvest, sound,
constant shape and item-identity methods retain their original logic. World
mutation, frame/pipe interaction, entity movement, rendering registration,
tooltips and unqualified localized ItemBlock names reject explicitly. Native
override guards compile with `@Override` so MCP/SRG mistakes cannot silently
fall through to a different vanilla implementation.

The generation statements and block/item/ore registration loops are named
excerpts, not supposedly complete `MetaBlocks.init` or `CommonProxy` callbacks.
Tile entities, tools, other block families, recipe preload/load and processing
handlers are not no-oped inside those enclosing methods: they are not invoked.

## Identity details that must remain unchanged

- Frames require dust plus `GENERATE_FRAME`. Compressed blocks require an ingot,
  gem or `FORCE_GENERATE_BLOCK`, and must not be ignored by `OrePrefix.block`.
- Each material registry independently groups eligible materials by `id / 16`;
  `id % 16` is the slot. An AVL map preserves group order. Sixteen-element arrays
  retain `Materials.NULL` holes; there is no dense renumbering.
- The factory maps include the sentinel too. Repeated sentinel keys overwrite
  earlier block ownership. The ore loops contain no added sentinel filter.
- `getGtMaterial` maps metadata at least 16 to slot zero. Negative metadata
  reaches the original list failure. State-to-metadata uses `indexOf`, so repeated
  sentinel slots canonicalize to their first occurrence rather than round-trip
  to each original hole index.
- Property names encode `modid__material`; parsing uses the material manager and
  returns `Materials.NULL` for unknown/disallowed values, including its original
  behavior when the sentinel is not an allowed property value.
- ItemBlocks keep their block's registry name and pass item metadata through.
  The original state-to-stack helper and native registry block-to-item callback
  own the relationship. Registration order within this bounded composition is
  not a claim that its numeric registry IDs equal a full installed pack's IDs.
- Distinct material namespaces can share a bare ore name. The native unifier
  selects the first matching registry in its original enhanced-for traversal;
  an existing generated item can therefore lack a unification entry for its own
  material identity. Do not replace this traversal with a stream: the selected
  Fastutil iterator and spliterator need not visit entries in the same order.

## Explicit composition checkpoint

`MaterialContentFamily` requires an immutable universe at construction: prefix
items alone, or prefix items plus these material blocks. This replaces the
single-family checkpoint, without a legacy adapter or post-completion append API.

For the combined universe:

`NEW → CONSTRUCTED → BLOCKS_REGISTERED → ITEMS_REGISTERED → COMPLETE`

One unifier initialization precedes original block construction and then prefix
item construction. Typed native block registration precedes typed native item
registration. Prefix items register/populate before the compressed/frame
ItemBlocks. Prefix ore membership precedes block/frame ore membership.

In-progress states make event failures terminal. Native partial mutations remain
for diagnosis; retry, reset, phase advancement and qualified queries reject.
Captured family lists, material-to-block tables, registered identities and native
block-to-item bindings must remain consistent. Unexpected drift invalidates
qualification instead of silently broadening the admitted universe.

Inventories distinguish construction, block registration, item registration and
ore membership. Queries require completion and a material in the qualified
catalog. A known ineligible form yields the original empty stack; an unqualified
family remains incomplete. This is an internal native interface, not a new public
recipe-validation endpoint.

## Qualification

`tools/axiom_material_block_conformance.py` independently rebuilds installed
sources and pinned upstream projections and compares execution traces. It also
compares original-source and release event transformations. The shared runner
continues to qualify the prefix-only universe separately.

Witnesses cover native states/stacks, copy/NBT, every generated slot, absent
forms, sentinel aliases, invalid metadata, namespace-separated IDs at 15/16 and
31/32, phase ordering, native partial failures, post-completion drift, low-quality
gem configuration, and edits to the original item/frame generation and registry
name sources. Synthetic extra registries are explicitly fixtures, not executed
Susy-Core or GroovyScript addon composition.

Baseline source/installed traces must agree in full. Removing frame construction
also changes identity-hash assignment to shared materials, which can change the
original compressed-block map's ore traversal. That directed edit requires exact
inventory/phase results and ore-event membership, but permits reordering within
that native map segment only. Both unnormalized traces are retained; execution
is never sorted or rewritten to manufacture agreement.

Use the same inputs as `axiom_material_item_conformance.py` with the block command
and a new report destination. Receipts bind source, extraction, runtime, program,
native images and library identities. Source checkouts, generated programs,
original binaries and receipts remain private/ignored.

## Next boundary and developer-value checkpoint

Ore/stone-type variants are covered by the separate [ore boundary](material-ores.md).
Surface rocks and deferred fluid-block requests must not disappear merely because
compressed/frame content is qualified. Recipe processing handlers remain pending.

The [authoring-value checkpoint](authoring-value-checkpoint.md) reassesses near-term value against actual pack
authoring before expanding family coverage further. Start from the selected
pack's `groovy/material/`, `groovy/classes/ChangeFlags.groovy`,
`groovy/preInit/RegisterMetaItems.groovy` and material-registration entry points,
paired with the selected Susy-Core revision. Those sources contain material
builders, fluid changes and explicit custom item declarations; current GT-only
catalog receipts do not prove their execution.

The review should select representative developer edits and identify the smallest
remaining path to source-located preflight: invalid material/property/flag
combinations, missing expected generated forms, fluid identity/registration,
custom item metadata and recipe references. Preserve IDE-led editing and existing
Review → Change → Diagnose ownership. Do not prioritize all-family completeness
over a demonstrably useful bounded authoring check, and do not call a projected
declaration or registration count proof of machine validity.
