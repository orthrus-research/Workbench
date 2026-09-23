# Native fluid stacks, NBT and default identities

This checkpoint supplies fluid-stack semantics needed by material source and later
recipe registration. It is **not WATER/LAVA bootstrap, actual enchantments, complete
pack execution or a recipe-validity verdict**.

## Source and integration

[native-fluid-stacks.lock.json](../sources/native-fluid-stacks.lock.json) binds seven
files: Forge FluidStack, FluidRegistry, Fluid, NBT constants and the NBTTagCompound
patch at Cleanroom
`fe78db8dc4fcee47df7549230858f4c064208d73`; GT Material and FluidProperty at
`9fe140febe8747bbe2f06dfd570421331ec06f4b`. Git blobs and SHA-256 hashes are checked
against immutable source objects, not mutable checkout HEAD.

`tools/axiom_stack_sources.py` retains the native FluidStack class except client
localization and the ItemStack/container-capability equality overload. Constructors,
copying, NBT loading/writing, amount-independent equality/hash, containment, exact
comparison and translation-key dispatch retain upstream bodies. Removed methods
are not replaced with success stubs or compatibility namespaces.

The old registration-only FluidDelegate record is removed. FluidRegistryState now
retains the actual delegate class, in-memory default loading/ID rebinding, NBT
default-list loading/writing, validation, stack queries and read-only map views.
The block-lookup cache is not instantiated or exposed, so its invalidation assignment
is omitted explicitly. No file or network persistence is exposed by the NBT methods.

GT material `getFluid(amount)`, `getFluid(key, amount)`, `getPlasma(amount)` and
FluidProperty `solidifiesFrom(amount)` now call that native stack implementation.
There is one fluid identity store, not a separate stack registry.

## Native NBT boundary

NativeNbtValue/Compound/List are typed bindings to separately supplied original
Minecraft NBT bytecode on the actual selected JVM. They do not convert tags into
JSON, duplicate tag classes or implement their own equality, coercion or copying.
The profile's registry-runtime policy pins the NBT class identities as well as
the original library JAR. No Minecraft binary or decompiled NBT source is bundled.
The selected Cleanroom `setTag` null-value guard is retained separately from its
patch and runs before the native utility mutation. A null tag raises the original
IllegalArgumentException without inserting a key; vanilla's permissive null
insertion is not treated as Cleanroom behavior.

Runtime-local identity interning preserves aliases when a native compound/list
is retrieved more than once. Copies still invoke original NBT copy methods and
produce distinct native objects. The bindings expose compound scalar/array/tag
access, compound merge, homogeneous list mutation and string/compound reads.
They are not a general SNBT parser, NBT file loader, item-capability API or proof
that installed pack transformations were applied. The selected list Iterable
addition and binary-read size-accounting patches are outside this binding's
exposed methods. Future source evaluations still
require a fresh restricted JVM and an external deadline.

## Native behavior that matters

- Amounts may be zero or negative. Equality and hash exclude amount; containment
  compares amounts only after fluid identity and tag equality match. Exact stack
  comparison includes amount. Null NBT and an empty compound are different.
- A registered alternative fluid initially keeps its own delegate referent.
  Rebinding changes existing stacks to the selected default. Hashes can consequently
  change while a stack is held in a collection; Axiom does not freeze or repair them.
- The constructor checks registration by **name**, then retrieves the delegate by
  **object identity**. A never-registered object with a registered name can therefore
  construct a stack whose `getFluid()` later throws. It is not silently replaced
  by the default fluid.
- Re-registering the same fluid creates a new stored delegate before duplicate-name
  rejection. A stack holding the displaced delegate will not follow later rebinding.
- Constructors and `copy()` deep-copy NBT. `writeToNBT` aliases its tag into the
  destination, and loading aliases the nested compound back. Writing a stack with
  no tag does not remove an existing `Tag` from a reused destination.
- Loading requires a string `FluidName` and resolves the current default. Wrong-type
  `Amount` becomes native zero; numeric coercion retains Minecraft's behavior,
  including flooring doubles. An existing wrong-type `Tag` becomes a fresh empty
  compound, while an absent `Tag` stays null.
- `initFluidIDs` sets maxID to the supplied map's **size**, not its largest ID, before
  loading defaults. It mutates and retains the caller's map. An empty mutable default
  set receives local defaults; an immutable empty set can throw. Missing owners use
  the original local default when available; malformed names retain native failures.
- Missing old IDs can become null-valued native BiMap entries. Default loading is
  not transactional. Old read-only map views stay on their old map after replacement;
  existing bucket snapshots stay old while subsequent reads rebuild the cache.
- Fluid default-list methods operate on actual NBT lists. They do not establish
  saved-world or network protocol qualification. Registry validation rejects entries
  inserted without a master registration, not every conceivable invalid state.

## Qualification

```sh
python3 tools/axiom_stack_conformance.py \
  --cleanroom PATH_TO_CLEANROOM --gtceu PATH_TO_GTCEU \
  --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --registry-root PATH_TO_ORIGINAL_LIBRARIES --report NEW_RECEIPT.json
```

Six contract groups, 8,192 stack vectors and 4,096 native NBT vectors execute in
each of English and Turkish fresh JVMs under kernel restrictions. The NBT probe
also compares bindings against direct reflective calls to the original classes.
Real Groovy source exercises GT material stack creation, tag copying, amount
mutation and rejection of a string amount. Fixtures are explicitly custom fluids
and materials, not a recovered vanilla or pack registry.

The comparison compiles the pinned extraction independently and checks it against
the installed retained implementation. A controlled source edit changes only the
stack constructor's amount assignment; a seven-unit stack must become eight units
without changing its fluid name. Receipts bind source inputs, extraction recipes,
driver, shared Java source, installed archives, native libraries and selected JDK.
Shared host ports remain declared substitutions, not independently proven parity.

## Native identity checkpoint and remaining producer work

Forge WATER and LAVA declarations call `setBlock(Blocks.WATER/LAVA)` and read those
blocks' translation keys before registration events. Actual block identities and
the native registry/bootstrap order are required; names or placeholder blocks do
not complete this dependency. The [native identity checkpoint](native-identities.md)
now executes the original Cleanroom-patched construction prefix, actual enchantment
constants and WATER/LAVA registration in a separately verified class space. It does
not merge that registry into this isolated fixture kernel. Loader ownership and
the installed registration listener universe remain separate from fixture ports.

Joining native identities into complete GT/Susy/addon producers and generated item/fluid/
ore membership, precede full recipe-source linting. `target` stays non-executing;
this checkpoint admits no public arbitrary-code or `bootstrap` operation.
