# Native Forge registry identities and state

This is a material-bootstrap dependency, **not actual enchantment construction,
vanilla initialization or recipe validity**. A name alone cannot stand in for a
native registry object, its delegate, ownership and lifecycle state.

## Source and executable scope

[native-forge-registries.lock.json](../sources/native-forge-registries.lock.json)
pins 15 source files at Cleanroom
`fe78db8dc4fcee47df7549230858f4c064208d73` (the selected 0.6.12-alpha revision).
Every file has a Git blob and SHA-256 identity. This is the selected source, not
a refresh of upstream HEAD or proof of the installed pack's transformation state.

`tools/axiom_forge_registry_sources.py` retains:

- Registry interfaces; `IForgeRegistryEntry.Impl` and its actual Guava TypeToken;
  native delegates, name/type resolution and equality.
- The complete RegistryBuilder, NamespacedWrapper and NamespacedDefaultedWrapper bodies; RegistryManager
  except snapshot creation; native ACTIVE, VANILLA and FROZEN stages.
- The contiguous ForgeRegistry construction/query/mutation region through
  `unfreeze()`, including definition copying, validation, sync, overrides and
  callbacks, plus its original OverrideOwner key.
- Exact GameData `checkPrefix`, all three wrapper-builder factories,
  `getWrapper` and `getWrapperDefaulted`; exact GameRegistry `findRegistry`; the native FML logger and
  `bigWarning` implementation.

Save/network snapshots, missing-world mapping recovery, registry-event creation,
and GameData initialization are not exposed by this isolated registry kernel.
Retained dummy/missing factory callback contracts do not imply execution of the
omitted world-recovery lifecycle. Omitted methods are not replaced with no-ops.

Types are relocated into Axiom's internal package. There is no legacy Forge
namespace or user-facing arbitrary Groovy execution endpoint. Sources retain
Forge's LGPL-2.1 headers and are distributed with the existing Forge license.

## Explicit dependencies

Resource locations and the wrapper's native utility superclass use the existing
separately supplied, hash-verified original Minecraft utility binding. Guava and
Commons Lang use the selected platform's actual JARs; native collection behavior
is not reimplemented. No Minecraft binaries are redistributed.

The existing RegistryRuntime active-owner port now carries both the mod ID and
an explicit `injectedFmlContainer` predicate. This replaces the exact Loader
query and injected-container type test, not ownership semantics. The predicate
is never inferred from a name. Fixtures explicitly supply it. The native event
owner class space and real Loader/configuration discovery are **not yet connected**
to this port. Opening an isolated registry environment is not vanilla bootstrap.

The native registry stages are process statics. Every independent evaluation must
run in a fresh restricted JVM; changing FluidEnvironment does not reset them.
Tests use `clean()` only to exercise separate fixture registries within one probe.
This is not a public reset protocol or a replacement for process isolation.

## Observed native contracts

- GameData splits names at the last colon and lowercases prefixes with ROOT
  locale. With `warnOverrides=false`, a foreign supplied prefix is discarded;
  with it true, that prefix is accepted after warning. The native ResourceLocation
  then lowercases both namespace and path. Registration-owner strings instead
  use the JVM's default locale. Null/injected-FML owners cannot authorize overrides.
- Setting an entry name does not name its delegate. Once present, a delegate name
  wins over the entry field. Delegate equality is by name, across types; even two
  unnamed delegates compare equal. Generic subclasses retain native TypeToken
  resolution rather than using their concrete class as the registry type.
- Requested nonnegative IDs below the configured minimum are accepted. Negative
  or occupied IDs allocate from the next clear bit at the minimum. Capacity checks
  precede duplicate identity checks; identical-object duplicates precede freeze
  rejection. Removing an entry does **not** free its allocation bit.
- ACTIVE overrides reuse IDs and redirect old delegates. Other stages retain old
  referents. Definition copies are initially empty, not snapshots of membership.
  Resetting delegates restores old overridden referents as well as current ones.
- Add callbacks run after map/ID/owner/delegate mutation. A callback exception
  preserves those changes. Clear callbacks run before clearing. Creation callbacks
  run before the manager inserts the registry. No rollback is added.
- A builder's single callback is captured directly; its aggregate callback reads
  the live callback list. Later builder changes therefore affect those cases
  differently. Parent-type conflict checks are directional, as in the source.
- Aliases resolve value lookup, but not numeric-ID lookup by name. Removal/clear
  can leave the default object available as fallback without a current ID. Clear
  preserves the default, slave maps and other state not cleared by native code.
- The iterator follows allocation IDs, skips removed holes, and returns null on
  exhaustion. Wrapper locking is separate from Forge registry freezing. An already
  named entry keeps its own key rather than the wrapper's supplied key.
- Alias cycles have no native guard. They are not generated by the comparison.
  Future source execution must retain process deadlines, not claim cycles are
  rejected by Forge or alter lookup behavior to make an evaluation finish.
- The defaulted wrapper retains default validation, fallback and locking semantics.
  Lookup fallback does not manufacture membership. Its factory ID equals the
  ordinary wrapper's ID in the selected source; requesting the wrong wrapper type
  can throw ClassCastException. The shared ID is not silently corrected.

## Qualification

```sh
python3 tools/axiom_forge_registry_conformance.py \
  --cleanroom PATH_TO_CLEANROOM --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --registry-root PATH_TO_ORIGINAL_LIBRARIES --report NEW_RECEIPT.json
```

The comparison independently compiles the pinned source extraction, then compares
it against the installed retained classes in fresh selected JVMs under kernel
restrictions. Both use controlled custom entry classes, **not substitute vanilla
or mod identities**. Eleven contract scenarios and 8,192 deterministic state/failure
operations run in each of English and Turkish locales. A real Groovy fixture
exercises registration, delegate identity, freezing and typed argument rejection.
Test-only inspection observes the native owner string without changing it.

A separately compiled edit changes only GameData's max-only builder factory from
`setMaxID(max)` to `setIDRange(1, max)`. Its first allocated ID must change from
zero to one while the fixture name remains unchanged. The edited input is never
accepted as the locked source. Receipts bind the upstream files, extraction
recipes, driver, all shared Java source, installed archives, runtime libraries and
actual selected JDK. Shared host ports are explicitly not independently proven
by the source comparison.

The [native identity checkpoint](native-identities.md) separately constructs actual
blocks, enchantments and WATER/LAVA. Next: join these with the material producer
context, then complete the Susy/addon property and producer closure with
configuration and applicable transformations. This checkpoint does not remove
the material producer, generated item/ore membership or recipe-lint blockers.
`target` remains non-executing and no `bootstrap` operation is admitted here.
