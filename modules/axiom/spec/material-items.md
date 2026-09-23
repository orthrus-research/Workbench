# Native material-prefix item family

This boundary qualifies **generation, native identity, registration and ore
membership** for GT's material-prefix items. It is not full GT item behavior,
full `CoreModule.preInit`, or a complete Supersymmetry content composition.
It extends [native items and unification](native-items.md) on the same locked
Cleanroom JVM and source target. There is no upstream-version refresh here.

## Source and implementation ownership

[material-items.lock.json](../sources/material-items.lock.json) pins the original
GT classes, their lifecycle call sites, Cleanroom registry events and the
owner-qualified GroovyScript native mappings. The native program contains real
Cleanroom `Item`/`ItemStack`/Forge registry values, not name-only replacements.

`MetaItem` and `MetaPrefixItem` are **member-level source projections**, not
whole-class retention. Complete reached methods and the state they access are
retained for construction, subtype indexing, capabilities, stack identity,
translation keys, material lookup, burn time, stack limits and beacon payment.
The default material-prefix values have no attached item components; their
original `initCapabilities` still constructs a real empty
`CombinedCapabilityProvider`, rather than returning null. Unknown metadata
instead follows the original null-provider branch.

`StandardMetaItem`, the component/capability interfaces, combined provider and
base creative-tab implementation retain complete bodies. GT-only UI interfaces,
unreached value behavior fields and behavior methods are not qualified. Native
Item overrides outside this boundary throw an explicit incomplete result; they
must not silently inherit different vanilla behavior. The main tab's LOGO
supplier rejects because `MetaItem1` is not yet qualified. Material-tab object
construction and its original ingot supplier are retained, not a GUI claim.

The exact prefix list and construction loop come from `MetaItems`; the native
registration/subtype loop comes from `CommonProxy`; the prefix ore loop comes
from `MetaItems.registerOreDict`. These are named source excerpts. Axiom does
not claim to execute their entire enclosing methods. In particular:

- `MetaItems.init` also constructs `MetaItem1` and armor.
- `CommonProxy.registerItems` also registers tools, preloads recipes and creates
  many ItemBlocks.
- The recipe-registry event also loads recipes, invokes addon callbacks and
  runs material handlers at later priorities.

None of those operations is replaced with a no-op inside a supposedly complete
callback. Axiom's `MaterialContentFamily` owns the explicitly bounded checkpoint;
the retained source owns content-generation decisions.

## Actual semantics

- The selected list contains 39 prefixes, not every declared OrePrefix.
- One native item is constructed per prefix and material registry. Each
  qualifying material becomes a subtype. The material's original registry ID
  is its metadata; holes are not compacted or sorted into new IDs.
- `OrePrefix.doGenerateItem` retains property/flag checks and ignored forms.
  The pack-selected baseline generates 2,752 variants from 602 GT materials.
  This is not a Susy/addon material-composition count.
- Native registration precedes `registerSubItems` for each item. Subtypes use
  the original linked short-key map, preserving insertion order.
- Duplicate metadata rejects after value construction but before index writes.
  Duplicate names with distinct metadata overwrite only the name lookup; both
  metadata entries remain. Metadata plus offset must be below `Short.MAX_VALUE`.
- Ore membership is a later operation. Alternative prefix names, plutonium and
  uranium aliases, and Saltpeter's duplicate registration retain original order
  and deduplication behavior.
- Source `getItemDamage` and `getMetadata` remain different native dispatches.
  Forge's stack-aware `getItemStackLimit`, `hasContainerItem`, `getContainerItem` and
  `getItemEnchantability` overloads are not vanilla's no-argument SRG methods.
- Native stack copy/NBT retains registry identity and capabilities. GT
  unification creates a selected item/count/metadata stack without arbitrary NBT.

## Checkpoint and query contract

`NEW → CONSTRUCTED → ITEMS_REGISTERED → COMPLETE`

Registration operations have explicit in-progress states. Any native exception
makes the family `FAILED`; it cannot retry, advance or answer qualified queries.
The original partial mutations remain visible for diagnosis. There is no reset,
rollback, or retry that could disguise a failed registration.
The checkpoint owns the original unifier initialization before construction.
Unexpected additions to the MetaItem universe invalidate the family; later
families must be admitted explicitly, not appended behind a completed receipt.

Construction, item registration and completed ore registration each capture an
inventory. A constructed object is not a registered subtype; a registered
subtype is not proof of ore membership. The item step dispatches the original
typed Cleanroom registry event with an explicit family-only callback universe.
This does not recover automatic loader discovery or all installed listeners.

`resolve` requires the completed family, a supported prefix and a material in
the supplied catalog. A known but absent form returns the original empty stack.
An unqualified family/prefix/catalog or incomplete phase returns incomplete,
not an assertion that the game lacks that item. This is an internal native
program interface, not a new public recipe-validity endpoint.

## Qualification

The conformance command rebuilds installed sources and immutable upstream-source
projections independently, checks byte-identical kernels and matching execution
traces, and compares original-source/release event transformations. This proves
the bounded retained members, **not unmodified whole-GT binary equivalence**.

Directed witnesses cover every generated stack, copy/NBT, subtype lookups,
virtual stack limits, capabilities, metadata boundaries, duplicate IDs/names,
unknown metadata, unavailable forms, native failure state, and unqualified
behavior rejection. Configuration changes must change low-quality gem
generation. Edits to original generation predicates and registry-name source
must produce exactly the expected inventory and event-trace changes.

After building the engine, qualified native images and native material program:

```sh
python3 tools/axiom_material_item_conformance.py \
  --java-home SELECTED_JDK --engine-home INSTALLED_ENGINE \
  --images QUALIFIED_NATIVE_IMAGES --library-root ORIGINAL_LIBRARIES \
  --program NATIVE_MATERIAL_PROGRAM --gtceu GTCEU_SOURCE \
  --susy-core SUSY_SOURCE --cleanroom CLEANROOM_SOURCE \
  --supersymmetry PACK_SOURCE --groovyscript GROOVYSCRIPT_SOURCE \
  --report NEW_RECEIPT.json
```

Receipts bind source, extraction, engine, program, native images, libraries and
JVM identities. Downloaded source, generated programs, receipts and private
working notes remain ignored. Original Minecraft/mod binaries are not published.

## Further families

All families remain intended future work. Each must explicitly establish its
generation rules, constructor dependencies, native registration, ore/material
membership, behavior coverage and source-qualified evidence. No family inherits
qualification merely by reusing `MetaItem` or sharing a registry.

Generated compressed blocks/frames and their ItemBlocks now have an explicitly
composed [checkpoint](material-blocks.md), including material-ID packing and
block/item metadata relationships. The prefix-only universe remains separately
qualified. Fixed
components, armor, finished tools, pipes, machines, addon composition and later
recipe handlers follow their own dependency boundaries. Preserve deferred fluid
block requests and pending prefix handlers until their real dependencies close.

Whole-pack parity, complete generated GT content, installed composition and full
recipe registration remain false. No game, fake world or Workbench runtime
harness is used; Groovy recipe execution and machine validity remain later gates.
