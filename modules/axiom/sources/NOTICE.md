# Axiom upstream source notices

The distributed native ore/stone classes and named generation/registration
excerpts retain GTCEu LGPL-3.0 provenance. Susy-Core GPL source from commit
`2042c2e4f8b2e5fc9691b1b1e74f0ee11632513a` is separately supplied and projected
only into private temporary addon programs, not bundled in Axiom. Original
copyright remains with the upstream contributors. `material-ores.lock.json` and
`spec/material-ores.md` distinguish complete property/declaration bodies,
member-level projections, nineteen selected material assignments, and native
host bindings. The original Cleanroom access transformer and Minecraft images
are separately acquired qualification/runtime inputs, not redistributed binaries.

Axiom's matching, recipe-tree and ordinary overclock/start rules are adapted
from GregTechCEu/GregTech v2.8.10, commit
9fe140febe8747bbe2f06dfd570421331ec06f4b, licensed under LGPL-3.0. Copyright remains
with its upstream contributors. See `rules.json` for original paths, immutable
source identities and the boundaries of each adaptation.

`BuilderValidation.java` extracts the `validateGroovy` and `getRequiredString`
methods from that same locked `RecipeBuilder.java`. Conditional logic and
diagnostic text remain upstream code; the enclosing list, map-shape and logging
carriers are substituted. This is field validation, not a reproduced registry.

`MaterialProperties`, `PropertyKey`, `IMaterialProperty`, `DustProperty`,
`GemProperty` and `IngotProperty` retain class bodies from the same LGPL-3.0
GTCEu revision, relocated with an explicit
`MaterialState` carrier. `MaterialState.setProperty` and `MaterialPhase` retain
the original material mutation guard and phase predicate. Copyright remains
with GregTechCEu contributors. `spec/material-properties.md` and
`tools/axiom_material_sources.py` enumerate the substitutions; these classes
do not implement a game material registry. All extracted sources are included
in Axiom's sources JAR.

The additional material construction sources (Element, Elements, MaterialStack,
SmallDigits, wood/polymer/ore/tool/rotor/wire/pipe properties, EnchantmentLevel,
MarkerMaterial/MarkerMaterialRegistry and selected voltage declarations), the
complete Material builder/metadata and full property-key/flag catalogs retain
the same GTCEu LGPL-3.0 provenance. See `material-construction.lock.json`,
`spec/material-construction.md` and `tools/axiom_construction_sources.py` for
original paths and explicit dependency ports. Original producer sources are
compiled only in temporary qualification space, not bundled as a recovered pack.

`OrePrefix`, `IOreRegistrationHandler`, `TriConsumer`, `MarkerMaterials` and
`MaterialIconType` metadata retain the same GTCEu LGPL-3.0 provenance. Material
unit and tier-name declarations extend the retained GTValues subset. See
`native-prefixes.lock.json`, `spec/native-prefixes.md` and
`tools/axiom_prefix_sources.py` for exact scope and mandatory source-catalog/config
ports. Original Minecraft dye/formatting bytecode is provided separately and is
not redistributed. The Groovy prefix test is a Workbench-authored fixture, not
copied Susy-Core source or a recovered pack producer.

Commons Lang 3.20.0 is distributed unmodified under Apache-2.0, copyright the
Apache Software Foundation. Its original LICENSE.txt and NOTICE.txt are retained
in `third-party/commons-lang3/`. This is the selected platform's Pair library,
not a Minecraft or mod implementation.

The Coolants.groovy test source is from SymmetricDevs/Supersymmetry commit
3e83cd7bad57bd4c424de4e6cc707ab02fe32f54, licensed under LGPL-3.0. Its original
bytes and source path are recorded in supersymmetry.lock.json. Test registry
fixtures are synthetic, explicitly supplied context, not exports from a game.

Groovy 4.0.30 is distributed as an unmodified dependency under Apache-2.0; its
JAR retains the Apache license and notices. The admitted interpreter follows
source-observed GroovyScript 1.4.3 mapper/amount semantics; it does not load that
Minecraft mod or execute its loader or user bytecode.

Pack metadata is parsed by unmodified TomlJ 1.1.1 (Apache-2.0), by Chris
Leishman, Tobias Schmidt and its contributors: https://github.com/tomlj/tomlj.
Its runtime dependencies are ANTLR 4.11.1 (BSD-3-Clause; The ANTLR Project)
and Checker Qual 3.21.2 (MIT; Checker Framework developers). These are parser
dependencies, not Minecraft mods. The distribution retains the Apache license
and Groovy notices, the ANTLR license, and Checker Qual's embedded license in
`third-party/`. Every runtime JAR is hash-locked and part of engine identity.

SourcePlan's loader path admission and ordering adapt GroovyScript v1.4.3,
commit 6a3340814ce9527d23c2b88cdfd340b4f146b4c4, under LGPL-3.0. Original
copyright remains with the GroovyScript contributors. The adapted rules operate
on verified source-package members; original classes are not initialized.

Cleanroom, Foundation and Prism source locks retain references used in loader
and registration research. Their former modeled execution implementations and
comparison drivers are no longer distributed. Original upstream inputs remain
private, independently licensed research dependencies.

The selected Temurin JDK is provisioned separately, not bundled in Axiom.
Axiom uses its actual JVM and standard library; no HotSpot source, modeled VM
implementation, or copied JDK classes are distributed with the engine.
The Cleanroom profile owns the selected runtime identity.

`GTControlledRegistry`, `IMaterialRegistryManager`, `MaterialRegistry`,
`MaterialRegistryImpl`, `MaterialRegistryManager` and `FluidRegistration`
extract the locked GTCEu registry and `FluidStorageImpl` class bodies under
LGPL-3.0. Original copyright remains with GregTechCEu contributors. Explicit
dependency substitutions are enumerated in `spec/native-registries.md` and
`tools/axiom_registry_sources.py`; sources are included in the sources JAR.

Fastutil 8.5.18 is an unmodified Apache-2.0 dependency by Sebastiano Vigna and
contributors, https://github.com/vigna/fastutil. Its version and artifact hash
are pinned to the selected Cleanroom library inputs. The distribution includes
the Apache-2.0 license in `third-party/fastutil/LICENSE`.

Minecraft utility bytecode is loaded only from separately provided, hash-verified
original library inputs. No Minecraft JAR or extracted Minecraft class is
redistributed in Axiom's source, sources JAR or engine distribution. Downloaded
inputs retain their respective upstream terms; they remain local/ignored.

The native fluid construction extraction retains selected GTCEu FluidBuilder,
fluid attributes/storage keys/properties, material metadata/flags and registration
methods at the GTCEu revision above. See `native-fluids.lock.json`,
`spec/native-fluids.md` and `tools/axiom_fluid_sources.py` for exact scope and
dependency substitutions. These are isolated construction domains, not an
executed vanilla or modpack bootstrap.

`NativeFluid` and `FluidRegistryState` retain selected Minecraft Forge
scalar-fluid and registration methods from Cleanroom commit
fe78db8dc4fcee47df7549230858f4c064208d73. Original copyright is Minecraft Forge,
2016-2020, under LGPL-2.1, as retained in both source headers. The original
license is included in `sources/licenses/forge/LGPL-2.1.txt` and the engine's
`third-party/forge/` directory. Modified sources are included in the sources JAR.
World methods and vanilla bootstrap are not included. Native event dispatch is
now retained separately as described below; the fluid environment's probe callback
is still not a recovered pack listener universe.

The native Forge registry interfaces, entry/delegate types, registry builder,
namespaced/defaulted wrappers, registry construction/state and selected GameData naming and
factory methods retain that same Cleanroom LGPL-2.1 provenance. See
`native-forge-registries.lock.json`, `spec/native-forge-registries.md` and
`tools/axiom_forge_registry_sources.py` for the exact 15-file source closure and
explicit host ports. These sources are included in the sources JAR with original
headers. GameData initialization, vanilla entries, world persistence and missing
mapping recovery are not included. No additional mod or Minecraft binary is
distributed.

Native fluid-stack construction, comparison, copying and NBT methods; fluid default
rebinding/delegates and NBT default lists; the NBT constant catalog; and the selected
Cleanroom NBTTagCompound null-value patch retain the
same Cleanroom LGPL-2.1 provenance. GT material-to-stack methods retain the selected
GTCEu LGPL-3.0 provenance above. See `native-fluid-stacks.lock.json`,
`spec/native-fluid-stacks.md` and `tools/axiom_stack_sources.py`. Original headers
and modified source are included. NBT objects execute separately supplied original
Minecraft bytecode; neither Minecraft binaries nor decompiled Minecraft NBT source
are redistributed. This retained stack kernel includes no vanilla block or fluid
bootstrap; separate native constructor inputs are described below.

The native identity input builder calls the original Cleanroom patcher, remapper,
access transformer and reached fluid-event transformer on separately supplied
Minecraft/Cleanroom binaries. See `native-identities.lock.json` and
`spec/native-identities.md` for exact source references and the constructor-prefix
boundary. The generated class image and checkpoint overlay are local, separately
licensed inputs; neither is distributed with Axiom. Original transformation
sources are compiled only in temporary qualification space. Native enchantment
bindings feed five GTCEu tool-stat expressions read from the locked external
checkout, not bundled complete producer implementations.

The classes under `nativeevents/` retain the selected Cleanroom Event, GenericEvent,
event priority/listener lists, event bus, native listener factory, annotations,
interfaces and event transformers. Copyright remains with Minecraft Forge and
Cleanroom contributors under LGPL-2.1; original headers and the Forge license
are retained. `tools/axiom_event_sources.py` and `spec/native-events.md` enumerate
namespace and host-port substitutions. The three GT material event declarations
and CoreModule's material-registration block are retained under LGPL-3.0 with
explicit producer ports; see `material-lifecycle.lock.json`. Modified sources
are included in the engine's sources JAR, without original namespace adapters.

Native event dependencies include unmodified Guava 33.6.0-jre and failureaccess
1.0.2 (Google and contributors), JSpecify 1.0.0 (JSpecify contributors), and Log4j
API/Core 2.26.0 (Apache Software Foundation), under Apache-2.0. Their licenses and
Log4j notices are included under `third-party/`. ASM and ASM Tree 9.10.1 are under
BSD-3-Clause, copyright INRIA and France Telecom; their license is retained in
`sources/licenses/asm.txt` and the distribution. These are native libraries,
not bundled mod binaries. Artifact hashes are part of engine identity.

Susy-Core GPL source is read only from the user's separately acquired pinned
checkout and compiled into temporary test space by the source-conformance tool.
No Susy-Core producer implementation or compiled producer class is bundled in
Axiom. The test does not qualify full pack material or recipe registration.

The native material program is generated from the same retained GTCEu/Cleanroom
sources, preserving their headers, host/event bindings and native-only GT item/unifier sources under
`jvm/src/nativeMaterials/java`. These source templates are included as
`axiom/native-materials/` resources in the engine JAR. The separate generated
program and source JAR are local qualification outputs, not distributed engine
content; neither contains original Minecraft classes. They remain derivatives
subject to the upstream licenses described above. Complete GT and Susy producer
methods used by `tools/axiom_native_material_conformance.py` are compiled only
from separately acquired locked sources in temporary qualification space.
See `spec/native-material-context.md` for bindings and limits. This does not add
any original mod/Minecraft binaries to the Workbench distribution.

The complete GT catalog and selected configuration declarations used by
`tools/axiom_catalog_conformance.py` are also compiled only from separately
acquired locked sources into temporary qualification space. The three GT material
event templates are relocated LGPL-3.0 source derivatives, retaining their notices.
Native configuration parsing, synchronization and event transformation use the
separately acquired Cleanroom classes. `material-catalog.lock.json` binds the
catalog, configuration declarations, lifecycle, native dependencies and pack
configuration. See `spec/material-catalog.md`; no full GT catalog binary is bundled.

The native-only OreDictUnifier, CustomModPriorityComparator, ItemAndMetadata,
ItemMaterialInfo, UnificationEntry and five item-variant-map class bodies, plus
the selected GT wildcard declaration, retain the same GTCEu LGPL-3.0 provenance.
Copyright remains with upstream contributors. Their relocated sources are
embedded in `axiom/native-materials/` with explicit dependency bindings described
in `spec/native-items.md` and `tools/axiom_item_sources.py`. The item source lock
also references GroovyScript's mapping data; it is read from the separately
acquired LGPL-3.0 checkout, not copied into Axiom's distribution.

Original Cleanroom OreDictionary/OreIngredient sources are compiled only into
temporary differential-test space and retain their original Minecraft Forge
LGPL-2.1 headers. Native Item/ItemStack/capability/ore bytecode continues to come
from separately supplied pinned inputs. No Minecraft source, Minecraft-derived
class image, original mod binary or temporary original-source override is bundled.

Material-prefix item members and generated material-block/ItemBlock members retain
the same GTCEu LGPL-3.0 provenance and upstream copyright. See
`material-items.lock.json`, `material-blocks.lock.json`, `spec/material-items.md`
and `spec/material-blocks.md` for complete versus member-level retention and
explicit unqualified behavior guards. The source-owned generation/registration
excerpts are not whole loader callbacks. Modified sources are included in the
engine resources and source distribution; original game/mod binaries are not.
