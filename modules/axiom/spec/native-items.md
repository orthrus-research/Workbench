# Native items, ore membership and GT unification

Status: bounded native construction qualification on the selected Cleanroom JVM.
This slice extends the [native identity checkpoint](native-identities.md) through
the complete vanilla item-registration method, then joins actual native ore
membership to the [complete frozen GT material catalog](material-catalog.md).
It does **not** certify complete Supersymmetry recipe registration or machine parity.

## Execution and ownership

One fresh restricted JVM owns one isolated class space. Original patched
Minecraft `Item`, `ItemBlock`, `ItemStack`, registry entries/delegates, NBT,
Cleanroom capabilities, `OreDictionary`, `OreIngredient` and event bus execute
from independently supplied, hash-pinned native inputs. There is no replacement
item class, name-only item registry, fake world, Minecraft launch or Workbench
runtime harness.

The checkpoint retains the original straight-line bootstrap prefix:

`guard → set flag → sounds → blocks → fire → potions → enchantments → items`

The full original bootstrap remains unchanged. This worker cannot resume it:
the original guard is already set. Subsequent potion-type/entity/biome/recipe
registration, vanilla snapshot and complete loader lifecycle remain outside scope.

The complete retained GT `OreDictUnifier`, `CustomModPriorityComparator`,
`ItemAndMetadata`, `ItemMaterialInfo`, `UnificationEntry` and five variant-map
classes are native-only source templates. Their full bodies are reconstructed
from locked source. Bindings substitute namespace, existing material/configuration
owners and verified native SRG symbols; compile-time JetBrains annotations are
omitted. The GT wildcard declaration is retained separately. No legacy namespace
adapter or second item implementation is supplied.

GT's `getItemDamage` is native `func_77952_i`, whereas `getMetadata` is
`func_77960_j`. Those methods can dispatch differently on the same ItemStack.
The immutable owner-qualified mappings and a native item overriding both methods
guard against accidentally conflating them.

## Actual behavior retained

- Native ItemStack initialization installs its item's delegate, calls
  `Item.initCapabilities`, then posts a typed `AttachCapabilitiesEvent`. Copy and
  NBT reconstruction create fresh parent/event providers and restore their
  serialized state. Generic listeners for another type must not receive the event.
- Ore registration updates native IDs/maps, copies the input stack into its
  list, then posts `OreRegisterEvent`. Duplicate item/damage/name registrations
  ignore count and NBT differences and do not post another event.
- GT initialization directly scans existing ore entries, then subscribes its
  static listener. The initial scan does not repost native events. Later native
  events update the same retained GT caches and prefix pending-material sets.
- Exact and wildcard metadata, single-variant versus multi-variant caches,
  marker prefixes, self-referencing defaults and missing forms retain their
  original distinctions. Prefix material handlers are not executed here.
- Unification constructs the selected item/metadata stack with the requested
  count; it does not promise to preserve arbitrary input NBT. Name-based GT
  lookup returns copies. Native `getOres` behavior must not be inferred from its
  stale "unmodifiable" comment: the pinned implementation exposes its list.
- A highest-priority throwing ore listener leaves native registration in place
  while preventing the lower-priority GT listener from seeing it. Retrying the
  same registration deduplicates before dispatch and does not repair the GT
  cache. The test records this partial state without resetting it.

The original Cleanroom parser and loading `ConfigManager.sync` project
`compat.modPriorities` alongside the two existing catalog options. Original
annotations, string-array defaults, category names and list parsing are retained.
The input file is never rewritten; parsing uses a disposable host-owned copy.

GT caches its comparator. With default `[minecraft, gregtech]`, those namespaces
precede unspecified namespaces, whose alphabetical order is **reversed** by the
outer `reverseOrder`. An empty priorities list instead selects ordinary ascending
namespace order. Equal-priority items retain insertion order. Duplicate priorities,
empty lists, missing options and original-default source edits are covered.

## Recipe rewrite boundary

The complete native OreDictionary static initializer also rewrites ingredients
of existing native ShapedRecipes and ShapelessRecipes. Axiom does not remove this
loop, clear the recipe registry, or claim it is always empty in the game.

Qualification uses two explicitly recorded universes:

1. The naturally empty crafting registry at this item checkpoint.
2. Six registered native recipe fixtures covering shaped and shapeless
   replacement, an excluded output, two different ore alternatives, and both
   orders of a partially matching alternative list.

The latter ordering matters: the original loop can replace an ingredient whose
unmatched alternative appears before its first matching alternative, while it
skips the reverse order. This behavior is retained, not corrected as an Axiom
optimization. A rewritten OreIngredient is also checked against later native ore
membership. These fixtures do not recover the vanilla or pack recipe universe.

## Qualification evidence

The source lock is [native-items.lock.json](../sources/native-items.lock.json).
It pins GT classes/lifecycle/config, reached Cleanroom item patches/capabilities/
ore code and GroovyScript's owner-qualified mappings. Existing locks bind the
catalog, patch pipeline and exact profile-selected Temurin HotSpot 25.0.4+7
Linux x86_64 runtime. This is a pinned target, not a floating latest-upstream claim.

The baseline contains **411 vanilla items**, **202 ItemBlocks**, **193 ore
registrations across 155 names**, and the existing **602 GT materials**. Directed
test items and recipes are explicitly labeled fixtures, not installed pack content.

Acceptance requires:

- Byte-identical installed-source and immutable-source native kernel rebuilds.
- Native/rebuilt GT traces and original-source/release Cleanroom ore traces agree.
- Original-source and release event transformers produce identical reached event
  transformations, including capability and ore events.
- Ninety native stack/copy/NBT vectors, fifty directional wildcard vectors,
  capability-provider roundtrips, registration/cache/failure witnesses and the
  six recipe-rewrite fixtures pass.
- Changed original configuration defaults, GT comparator code and Cleanroom ore
  registration source each produce exactly the expected result change.
- Input custody, earlier native material/catalog tests, publication validators
  and frozen canonical validation remain green.

The separately compiled original Cleanroom classes are test-only overrides in an
independent oracle process. They are never accepted by the production program's
native-namespace admission gate or included in distributed artifacts.

## Reproduce offline

Build the engine, qualify native identity images and build the native material
program first. Then run from the repository root:

```sh
python3 tools/axiom_item_conformance.py \
  --java-home SELECTED_JDK --engine-home INSTALLED_ENGINE \
  --images QUALIFIED_NATIVE_IMAGES --library-root ORIGINAL_LIBRARIES \
  --program NATIVE_MATERIAL_PROGRAM --gtceu GTCEU_SOURCE \
  --susy-core SUSY_SOURCE --cleanroom CLEANROOM_SOURCE \
  --supersymmetry PACK_SOURCE --groovyscript GROOVYSCRIPT_SOURCE \
  --report NEW_RECEIPT.json
```

The receipt binds exact sources, JDK, libraries, images, program, engine and
qualification code. CI executes the same check. Images and generated programs
remain ignored local artifacts; no Minecraft or original mod binaries are added
to the public tree.

## Next boundary

The first generated family now has a separate
[material-prefix item qualification](material-items.md). Other GT-generated
material block/item construction and native registration remain later boundaries:
audit the full `MetaBlocks.init → MetaItems.init → ToolItems.init` dependency
closure and distinguish constructing objects from Forge registration and later
`CommonProxy` ore-registration callbacks. Preserve deferred fluid-block requests
and determine their actual constructor dependencies before executing them.

Susy/addon catalog composition, full transformer/listener composition, material
recipe handlers, Groovy recipe registration and machine execution remain later
gates. `wholePackParity`, `installedCompositionQualified`, `generatedGTContent`
and `fullRecipeRegistration` remain false. Developer IDE edits remain the intended
workflow; this foundation does not add guided generation or a new public UI.
