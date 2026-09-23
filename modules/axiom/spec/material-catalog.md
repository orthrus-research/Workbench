# Complete GT catalog in the native Cleanroom context

Scope: an internal, source-qualified **material FROZEN checkpoint**, not generated
content, a full pack registry or a new public recipe-validity capability.

## Registration and ownership

The complete pinned `Materials.register()` body executes without declaration
filtering: markers, elements, first-degree, organic, unknown-composition,
second-degree, higher-degree, flag/property additions, chemical dyes and ore-prefix
initialization. Producer sources are separately acquired qualification inputs,
not a second handwritten catalog or bundled mod implementation.

The retained `CoreModule` material-registration block supplies ordering:

1. Initialize markers and post `MaterialRegistryEvent` in PRE.
2. Open registries, construct `MaterialEvent`, run the complete GT catalog and
   assign the actual Aluminium fallback, then post `MaterialEvent` in OPEN.
3. Close registries and post `PostMaterialEvent` in CLOSED.
4. Freeze materials and stop. `OreDictUnifier.init`, generated blocks, meta-items,
   tool items and GT fluid registration have **not** executed.

Events use the original Cleanroom `MinecraftForge.EVENT_BUS`, `GenericEvent` and
listener machinery, in the same restricted class loader as native material types.
Only `GenericEvent` and the three relocated GT material events receive the original
`EventSubscriptionTransformer` at load time. Input/output class digests are recorded;
no global launch transformer pipeline is claimed. Original `DummyModContainer`
owners and controlled listeners are explicit fixtures, not discovered pack members.
Exceptions preserve the native partial registration state. Lifecycle and context
attempts are one-shot, including failures.

## Live dependencies and configuration

`CatalogInputs` resolves actual public static material fields lazily. A declared
field that is null during class initialization remains null; an absent field
rejects. A directed binding fixture tests null-to-assigned transitions. Wood property verification initializes
`OrePrefix` during the unknown-composition group, before later material groups
have executed. Its reached fields are already assigned in the pinned run; the
receipt records actual reads rather than requiring a fabricated null. Eagerly
pre-populating the entire catalog would still change original initialization order.

The reached configuration options are projected from GT source:
`recipes.generateLowQualityGems`, `worldgen.allUniqueStoneTypes`, and
`compat.modPriorities` for the subsequent [native item stage](native-items.md). Original
annotations, nested category names, field types and default initializers are
retained. The actual Cleanroom `Configuration` parser and private loading
`ConfigManager.sync` traverse that projection. No boolean parser or default value
is reimplemented. This is not full `ConfigHolder` discovery or synchronization.

The native constructor needs `FMLInjectionData.minecraftHome` to relativize its
filename. The fixture explicitly supplies a disposable directory, without calling
the broader early-config registration path. Configuration inputs are bounded,
ordinary files. Parsing uses a host-owned disposable copy; native recovery that
rewrites that copy rejects qualification without changing the caller's input.
No configuration is saved to the source checkout. Missing options retain original
defaults; malformed boolean values follow native fallback behavior.

## Evidence and limitations

The catalog lock binds 26 immutable source/configuration files to the existing
Supersymmetry 0.1.16.15, GTCEu 2.8.10-beta and Cleanroom 0.6.12-alpha pins. Execution
uses the profile-selected Temurin HotSpot 25.0.4+7 Linux x86_64 JDK and existing
qualified native images/libraries. A receipt records exact identities, not a claim
to track latest upstream or to qualify the installed mod/mixin composition.

Qualification reconstructs the shared kernel from locked upstream source and
requires byte-identical installed-source builds. It captures every material's
properties, flags, components and catalog references; all prefix/material
eligibility decisions; markers, dyes and queued fluid builders. No item identity
or ore dictionary membership is inferred from an eligibility decision.

The pinned English catalog registers **602 materials**, with **101 prefixes** and
**16 chemical dyes**. Those counts are checks on this exact source universe, not
claims about Supersymmetry's completed material or recipe universe.

Required witnesses include the four boolean configurations, source-edit propagation
through a cross-material property, native generic listener dispatch, failure at
each material event, live field/null binding behavior, and unchanged
native WATER/LAVA registry membership. The five queued world-block requests for
Oil, OilHeavy, RawOil, OilLight and NaturalGas remain intact and unexecuted.

Default-locale behavior is retained, not normalized away: Turkish produces a
dotless `ı` in tier names via the original `toLowerCase()` call. Native material
name validation rejects that marker during initialization, before the first
material event. Source-rebuilt and installed kernels must retain the same PRE
failure and partial marker state. This is a discovered upstream boundary, not
a reason to claim successful catalog registration under every locale.

This path still excludes Susy/addon producer composition, generated content,
ore registration, full loader transformations/listeners and Groovy registration.
The earlier isolated kernels remain comparison fixtures, not another runtime
registry authority. `wholePackParity` and `installedCompositionQualified` stay false.

## Offline qualification

Build the engine and [native material program](native-material-context.md) first:

```sh
python3 tools/axiom_catalog_conformance.py \
  --java-home SELECTED_JDK --engine-home INSTALLED_ENGINE \
  --images QUALIFIED_NATIVE_IMAGES --library-root ORIGINAL_LIBRARIES \
  --program NATIVE_MATERIAL_PROGRAM --gtceu GTCEU_SOURCE \
  --susy-core SUSY_SOURCE --cleanroom CLEANROOM_SOURCE \
  --supersymmetry PACK_SOURCE --report NEW_RECEIPT.json
```

The tool is offline, uses fresh processes and refuses receipt overwrite. It does
not launch Minecraft or use Workbench's runtime harness. The subsequent
[item stage](native-items.md) now supplies native vanilla item and ore membership.
Next, extend the dependency closure for generated blocks/items/fluids;
keep addon composition and recipe registration as explicit subsequent gates.
