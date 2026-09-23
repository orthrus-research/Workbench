# Cleanroom-native block, item, fluid and enchantment identities

Axiom constructs actual Cleanroom-patched vanilla blocks, enchantments and items, then
native block-backed WATER/LAVA, on the selected JVM. This is a bounded material
dependency, **not complete vanilla bootstrap, pack registration or recipe-validity
certification**. No client, server or world is started. Workbench's runtime harness
is not involved.

## Exact transformation inputs

[native-identities.lock.json](../sources/native-identities.lock.json) pins fifteen
source/patch references at Cleanroom
`fe78db8dc4fcee47df7549230858f4c064208d73` and GTCEu
`9fe140febe8747bbe2f06dfd570421331ec06f4b`, not floating upstream HEAD. The
Cleanroom profile owns
[native-identity-runtime.json](../../../profiles/platforms/cleanroom/native-identity-runtime.json)
and the existing exact Temurin 25.0.4+7 Linux x86_64 JVM policy. This platform
selection remains provisional, not whole-pack qualification.

The offline builder calls original selected Cleanroom implementations:

1. `ClassPatchManager.setup(Side.CLIENT)` and `applyPatch` apply original patches
   to separately supplied Minecraft 1.12.2 bytes.
2. `FMLDeobfuscatingRemapper` and `FMLRemappingAdapter` use the original embedded
   `deobf_data-1.12.2.tsrg`. Both Minecraft and Cleanroom classes need remapping:
   the selected Cleanroom release also contains obfuscated Minecraft descriptors.
3. Original `AccessTransformer` applies `forge_at.cfg`.
4. Original `EventSubscriptionTransformer` transforms the reached
   `FluidRegistry$FluidRegisterEvent` subtype, retaining its native listener list.
   This is not the full pack transformer chain.

The patch container has 1,187 client records; native setup excludes 22 zero-length
patches. The resulting 1,165 active patches include 22 new classes. The combined
image has 5,185 classes. Existence and Adler32 checks precede the native patcher;
changed inputs reject. There is no replacement patch algorithm or remapper.

Two deterministic external images are pinned: the transformed class image and a
single-class constructor-checkpoint overlay. They remain local/ignored, never
copied into the engine or source distribution. Original JAR services/manifests and
game assets are not added as resources; original `mcpmod.info` metadata is retained.
The external library inventory is the selected platform's remaining 113 artifacts:
a reproducible input inventory, **not yet a minimized dependency closure**.

## Initialization checkpoint

The overlay adds `Bootstrap.axiom$materialIdentities()` by retaining the exact
instruction prefix of patched native registration through items. The full
original method remains unchanged. Instructions, call order and guard destination
must match:

`alreadyRegistered guard → set flag → sounds → blocks → fire → potions → enchantments → items`

Original GameData initialization and block-registration callbacks run. No debug
flag suppresses GameData; no manufactured registries replace it. Native Enchantments
constants and FluidRegistry static initialization then execute against the actual
registries. WATER/LAVA bind stationary blocks, read their translation keys and post
original registration events.

The native already-registered flag is true at this intermediate point, exactly as
inside the original method. **Calling full bootstrap afterward would skip remaining
work, not resume it.** This worker is neither fully initialized nor reusable for
an independent evaluation. Complete recipes, vanilla snapshots and freezing
remain outside the checkpoint.

The [native item qualification](native-items.md) covers actual Item/ItemBlock and
ItemStack identities, capabilities, native ore events and retained GT unification.
It adds source references in a separate item lock and does not broaden the older
fluid/enchantment consumer fixture into full pack initialization.

Native Loader metadata/container construction reached here is not `loadMods`,
discovery or pack lifecycle execution. Its pre-mod-loading owner context and
reached listeners do not establish the installed listener universe. Each independent
evaluation still requires a fresh restricted process and external deadline.

## Typed consumers

`NativeVanillaIdentities` owns a platform-parent class loader containing verified
images and external libraries. Unpatched Minecraft, raw Cleanroom and the ambient
engine classpath are not parents.
Native constructor/method calls also use that class space as their thread context
loader, restoring the caller's loader afterward. The test supplies a failing
ambient resource loader to catch accidental initialization leakage.
Interned handles retain actual object identity. They neither reconstruct objects
from names nor copy entries into isolated fixture
registries. Premature access, failed/repeated initialization and closed handles
remain explicit boundaries.

Block bindings expose native keys, IDs, translation keys and state IDs. WATER/LAVA
bindings expose original properties and actual stack construction/copy/delegates.
Flowing-block lookup runs original normalization and block-cache logic. The existing
fluid-stack/NBT fixture kernel remains separately qualified, not merged into this
native class space.

The `EnchantmentIdentity` port now has actual native bindings. Six MCP producer
constant spellings map to their exact pinned native Enchantments fields. Five
complete `toolStats(...)` argument expressions from GT FirstDegreeMaterials and
SecondDegreeMaterials execute unmodified in Groovy against retained ToolProperty
and those actual constants. These are source-consumer witnesses, **not complete
material declarations or producer methods**.

## Qualification

From the repository root with separately acquired inputs:

```sh
python3 tools/axiom_native_identity_conformance.py \
  --cleanroom PATH_TO_CLEANROOM --gtceu PATH_TO_GTCEU \
  --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --library-root PATH_TO_ORIGINAL_LIBRARIES \
  --output NEW_PRIVATE_INPUT_DIRECTORY --report NEW_RECEIPT.json
```

Default is offline. Explicit `--provision-libraries` permits profile-pinned HTTPS
acquisition; changed cache files reject rather than being overwritten. Credentials
or an installed Minecraft instance are not required for this bounded run.

Qualification compiles six original transformation sources and requires byte-for-byte
agreement with release transformations, both images and per-class digest inventory.
Eleven changed bootstrap boundaries must reject before execution. Fresh English and
Turkish restricted JVMs compare 254 blocks, 30 enchantments, block-state callbacks,
8,192 fluid-stack/copy vectors and five GT tool-stat expressions. Damaged images,
indirect paths and missing libraries must reject against installed pins. Receipts
bind source, builder/consumer, engine, JVM, libraries, images and producer locations.
Changing inputs during qualification rejects the receipt. This is selected
transformation/constructor evidence, not proof of full installed modpack behavior.

## Next dependency boundary

The [native material context](native-material-context.md) now joins retained
producers to this class space without duplicating fluid registrations or converting
WATER/LAVA. Complete GT element and Susy unknown-composition qualification uses
explicit native owner/listener fixtures, not installed discovery. Advance remaining
complete GT/Susy/addon producers, configuration and applicable transformations,
then generated item/fluid/ore membership. Public `target` stays non-executing;
no arbitrary-code or `bootstrap` endpoint is admitted here.
