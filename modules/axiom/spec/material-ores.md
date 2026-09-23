# Native ore content and host-stone identity

This boundary adds GT/Susy ore generation, native block-state and ItemBlock
identity, registration, ore membership, and world-independent drop selection to
the existing material-content checkpoint. It does not qualify complete pack
startup, world generation, harvesting events, processing recipes or Groovy scripts.

## Source and runtime ownership

`material-ores.lock.json` pins GTCEu, Susy-Core, Cleanroom and GroovyScript mapping
inputs at the existing selected target. `axiom_material_ore_sources.py` retains
source methods and explicitly named declarations/registration excerpts. Susy-Core
GPL implementations are **not distributed in the engine**: their five source
projections are generated from separately supplied, hash-verified source into the
temporary addon program. A mandatory same-class-space `OreAddon` dependency port
wires that program to core content ownership. No compatibility layer or stand-in
Susy implementation is shipped. The
native program runs on the selected Cleanroom JVM, using the separately supplied
patched/remapped Minecraft and Forge classes. There is no synthetic world or
replacement block-state, ItemStack, registry, or metadata implementation.

Retained classes include `StoneType`, `StoneTypes`, `PropertyStoneType`,
`BlockOre`, `OreItemBlock`, `VariantBlock` and native GT/Susy host-stone subclasses.
`PropertyStoneType` and the stone declarations retain complete bodies, subject
to documented namespace/native-name bindings. Other classes have explicit
member boundaries. `StoneType.computeStoneType`, world fire behavior, rendering,
localized item names, host mining/entity behavior and unqualified decorative
material lookup reject. Inherited world behavior is not an admitted entry point.

The smooth GT/Susy host blocks are actual source constructors and native state
containers. Complete host enums preserve their original ordinals; constructing
these dependencies does not qualify a decorative-block family. Their ItemBlocks
and other shapes are not registered. Creative tab declarations retain source
names/settings; unqualified icon suppliers reject.

Host harvest-method observations are relative to this constructor context.
Vanilla `ForgeHooks.initTools` normally runs through `MinecraftForge.initialize`;
this boundary does not execute that enclosing initialization. A native vanilla
host can consequently report a null harvest tool. The reached ore method preserves
that value; these observations are not a qualification of installed vanilla
harvest defaults or player tool eligibility.

The separately supplied bounded Susy material dependency set consists of eleven first-degree mineral
assignments and eight second-degree host-rock assignments, selected by name from
the original producer source. They retain IDs, namespace, builder calls,
components, flags and formulas, including source spelling. They execute during
the native material event before freeze. These are **statement excerpts**, not
the complete `SuSyFirstDegreeMaterials.init`, `SuSySecondDegreeMaterials.init` or
`SusyMaterials.init` methods. Unrelated producers are neither executed nor
replaced by no-op callbacks. Ten original Susy stone-prefix declarations form
the matching bounded prefix subset.

Explicit `DummyModContainer` owners and a named-mod map are qualification inputs,
not recovered installed mod discovery. The native loader is queried for JEI;
this qualified context has no JEI. Reaching its integration branch rejects rather
than silently accepting missing JEI behavior. This binding does not recover the
whole `Mods` enum or qualify changes to the installed mod list.

## Required GT access transformation

`BlockOre.initBlockState` assigns Minecraft's ordinarily-final `blockState` field.
The pinned `gregtech_at.cfg` changes its visibility/finality. Replacing that
constructor logic with reflection or a different block factory would miss a
real loader dependency.

The original Cleanroom `AccessTransformer` applies the two source rules targeting
`net.minecraft.block.Block`. A temporary compiler-classpath overlay and the
native class loader use the same transformer/rules. Raw images remain unchanged;
no modified Minecraft binary is distributed. Receipts bind input/output/rule
digests and compare the original-source transformer against the selected release
binary, as well as compiler versus execution output. Other GT access rules and
the full LaunchWrapper transformation pipeline are outside this boundary.
The access rules apply only to an explicitly opened ore context, not merely
because the program artifact contains ore classes. Other content contexts keep
their previous native transformation boundary.

## Lifecycle and identity

An immutable `MaterialContentFamily.Universe` chooses GT-only ore content or
GT content plus the bounded Susy stone dependency set. Prefix-only and
compressed/frame-only universes remain independently qualified.

`CONSTRUCTED → ORE_HOSTS_PREPARED → BLOCKS_REGISTERED → ITEMS_REGISTERED → COMPLETE`

Host preparation retains the important Susy order: stone declaration registration
precedes Susy host-block construction. Stone suppliers remain lazy. GT stone
initialization and ore construction occur inside the selected block-registration
callback, before generated blocks are registered. Prefix items precede material
ItemBlocks and ore ItemBlocks; prefix ore membership precedes compressed/frame
and ore membership. These are named portions of the callbacks, not full callbacks.

Native partial mutations survive failures. A failed checkpoint cannot reset,
retry, advance, or answer qualified queries. Block/item registration identities,
stone names/IDs, host-state ownership and the material-to-stone ore table must
remain consistent; unexpected mutation invalidates the composition.

### Original behaviors that must not be normalized

- GT declares stone IDs 0–11; the selected Susy declarations add IDs 12–21.
- Ore generation requires `PropertyKey.ORE` and no `DISABLE_ORE_BLOCK` flag.
- Stone IDs group by `id / 16`, using `id % 16` slots. The original `copyNotNull`
  truncates at the **first null**. It does not compact gaps or preserve compressed
  block sentinels. Sparse IDs can omit later entries or produce native failures.
- Metadata at or above the allowed-value count falls back to zero. Negative
  metadata reaches the original list failure. Item metadata passes through.
- Property parsing returns absent for unknown or disallowed stone names, unlike
  `PropertyMaterial`'s sentinel behavior.
- All allowed ore variants are registered in the ore dictionary. Creative
  visibility and ordinary drops are separate decisions controlled by the stone's
  `shouldBeDroppedAsItem`, including `allUniqueStoneTypes` at construction.
- For non-unique types with IDs >=16, `getItemDropped` selects the material's
  vanilla-stone ore block; `damageDropped` returns zero. Non-unique types in the
  first group keep their current ItemBlock, still with damage zero.
- `getSilkTouchDrop` instead uses the **current block's default state** for
  non-unique types. It must not be rewritten as ordinary-drop selection.
- `getPickBlock` retains the exact selected state regardless of ordinary drops.
- When unique stone types are enabled, Susy initialization also attaches original
  secondary material entries to its prefixes. This is not recipe processing.
- A whole ore-dictionary name can also be a declared prefix (`oreQuartzite` in
  the bounded Susy context). The native unifier then skips material-name splitting:
  a real ore variant and raw membership can lack a unification entry. Inventories
  expose the reverse entry and selected unification stack separately.

## Queries and qualification

The bounded baseline contains 602 GT materials plus nineteen selected Susy
dependencies. Its 22 stone types produce 222 ore blocks with 2,442 variants.
The GT-only comparison produces 110 ore blocks with 1,320 variants. These are
qualified-context counts, not the full pack's material or ore registry.

With the selected pack configuration, 666 variants have ordinary-drop redirects
that differ from their silk selection. Two generated stone ores (`oreQuartzite`
and `oreSoapstone`) have raw membership but no parsed unification entry after
the matching Susy prefixes are admitted.

The internal completed checkpoint distinguishes an exact generated ore form from
`OreDictUnifier` selection. A known ineligible form is empty; an unadmitted family,
stone, material context or incomplete checkpoint rejects. Inventories expose
construction, registration, membership and direct drop-selection results
separately. Numeric IDs belong to the bounded composition, not a full pack save.

`tools/axiom_material_ore_conformance.py` rebuilds immutable-source and installed
programs independently, compares native execution traces, and exercises source
edits, both stone configuration branches, GT-only/Susy contexts, disabled
generation, sparse/group-boundary IDs, collisions, event failures, metadata,
copy/NBT, host state and identity drift. Existing content qualifiers remain
separate regressions. There is no claim of an independent algorithmic oracle for
shared native host dependencies.

## Developer-value checkpoint

Before extending another generated-content family, review actual pinned pack
material additions, fluid/property changes and custom item declarations. Choose
the shortest source-backed path to source-located authoring feedback inside
Review → Change → Diagnose. A larger native catalog is not itself a useful
developer check. Surface rocks, deferred fluid blocks and pending processing
handlers remain explicit gaps; they must not silently disappear.
The source-backed assessment and recommended authoring preflight are recorded
in [the developer-value checkpoint](authoring-value-checkpoint.md).
