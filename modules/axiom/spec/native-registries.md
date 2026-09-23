# Native material registries and fluid registration queue

Status: implemented **internal bootstrap prerequisites**. These classes do not
execute the pack's material producers or create real fluids. The recipe CLI
still requires explicit registry context; no new native construction capability
is advertised. Minecraft is never started and no game runtime harness is used.

## Exact inputs and ownership

The source baseline remains Supersymmetry 0.1.16.15 with GTCEu v2.8.10 at
`9fe140febe8747bbe2f06dfd570421331ec06f4b`. Source file, Git blob and SHA-256
identities are in `sources/supersymmetry.lock.json`; class extraction is specified
by `tools/axiom_registry_sources.py`. This is an immutable selected baseline,
not a claim to have refreshed upstream heads.

Cleanroom owns `profiles/platforms/cleanroom/registry-runtime.json`, packaged
with that profile and embedded unchanged in Axiom. It pins the original
Minecraft 1.12.2 client JAR and selected Guava, Commons Lang and Log4j libraries,
plus the engine's Fastutil 8.5.18 identity. The policy records the original
Cleanroom bootstrap metadata digest. This is a candidate utility composition;
applied mixins and installed pack transformations remain unqualified.

The selected Temurin HotSpot 25.0.4+7 executes the code. `RegistryRuntime`
verifies the JARs and registry-class hashes, then uses a fresh URL classloader
whose only parent is the JDK platform loader. It invokes the original bytecode
for `RegistryNamespaced` (`fh`), `RegistrySimple` (`fo`) and
`IntIdentityHashBiMap` (`qz`). No remapping or Minecraft initialization is needed.
Missing or changed inputs fail admission; there is no fallback map.

The integer backing is **IntIdentityHashBiMap, not ObjectIntIdentityMap or
IdentityHashMap**. Running the original utility avoids approximating its identity
hashing, probing, resize and repeated-value behavior. Guava's original HashBiMap
still owns names; selected Fastutil maps still own manager and fluid queue order.

Core retains process/provisioning ownership. The build's explicit `--provision`
can obtain the pinned utility inputs into ignored local state; `--registry-root`
selects verified existing inputs for offline builds. Those downloads are not
packaged into the engine or public repository. Axiom's distribution includes
Fastutil with its license, and its own extracted GT source with LGPL notices.

## Deliberate source substitutions

- Relocate packages, narrow top-level visibility and remove annotations/imports.
- Replace game-dependent Material with the existing MaterialState property
  carrier. Registry calls require explicitly supplied numeric ID and namespace;
  property-only carriers throw if those missing identities are requested.
- Replace the global manager, active-mod container, network-ID counter and
  diagnostic logger with an evaluation-owned context. The active mod is an
  explicit input, not an inferred owner or simulated Forge event bus.
- Replace the Minecraft superclass with a narrow bridge to its original native
  instance. Its actual name map and integer map remain the backing state.
- Replace the single Guava Preconditions duplicate-registry guard with the same
  IllegalArgumentException and formatted message. Retain the rest of the
  registry class bodies, including unexpected order and partial failures.
- For FluidStorageImpl only: expose key priority, builder invocation, default
  builder factory and collision reporting as typed dependency ports. The
  `FluidRegistration` extraction retains its queue/association algorithm, not
  the original FluidBuilder, FluidStorageKey catalog or Forge FluidRegistry.

These are dependency seams for subsequent native construction, not compatibility
adapters in the upstream namespaces. Explicit test callbacks are fixtures and
cannot qualify real fluid generation.

## Actual lifecycle behavior

The manager starts in PRE; registries can only be created then. GT-controlled
unfreeze/freeze changes each registry only for an active `gregtech` owner, but
the manager still advances its phase when those calls return normally. Thus
manager phase and registry frozen flags are not interchangeable. A thrown
transition can retain earlier registry changes.

Registration validates numeric bounds, writes the name, checks for an occupied
ID, then writes the integer map. An ID collision therefore leaves the name
write intact. Replacing a name can leave an earlier numeric entry. Registering
the same object twice can preserve its first numeric lookup. The numeric
iterator and live name-value collection need not describe the same entries.
The frozen flag itself does not guard `register`; closing the material registry
does, by logging and skipping the registration before ID validation.

Closing builds a read-only snapshot of current materials. Per-registry material
views remain live and read-only. Unknown namespaces/network IDs fall back to
the GregTech registry, but missing materials return null. Explicit fallback
material lookup lazily caches the default; it does not change normal lookup.
The upstream default fallback must be initialized before asking for it; Axiom
does not invent one to conceal an incomplete bootstrap.

Fluid registration inserts a default liquid builder only when both the queue
and associations are empty. A manually stored null association still suppresses
that default. Builders are sorted by negated integer priority using native map
encounter order for ties; Integer.MIN_VALUE retains upstream overflow behavior.
Builders run before association collisions are checked. Queue disposal and the
registered flag happen only after the complete pass succeeds. A failed pass
retains earlier effects and all queued builders, so retry can run earlier
builders again. Direct `store` remains writable even after queue finalization.

Closing RegistryRuntime releases JAR resources and rejects further bridge calls.
It is not revocation of already exposed collection views or all loaded Java
objects. Actual evaluations must use the disposable worker; in-process callers
do not acquire host isolation by constructing this context.

## Verification and limits

Directed Java tests cover lifecycle transitions, fallback caching, duplicate
name/value/ID behavior, bounds, snapshots, failure effects and independent
contexts. Two disposable-worker probes execute native registration under the
production bubblewrap and thread-synchronized syscall policy. Fluid tests cover
ordering, default generation requests, nulls, collisions, retries and fail-closed
unavailable builder ports.

`tools/axiom_registry_conformance.py` independently reads locked original source,
checks retained class extraction, compiles separate Original classes and compares
24,000 registry operations and 48,000 fluid-queue operations. It binds source,
engine, JVM, policy, extraction recipe, driver and shared dependency seams into
a no-clobber receipt. The comparison establishes agreement for the admitted
extraction; shared native utilities and carriers are not an independent proof
of every game dependency. Directed bridge tests cover their intended binding.

The subsequent [native fluid domain](native-fluids.md) now supplies selected
Material.Builder operations, FluidProperty, FluidBuilder, fluid unification
and isolated Forge registration. It executes the pinned Susy-Core
unknown-composition initializer as a source acceptance test. These additions
have their own scope and source comparison; the queue-only tests above still
do not qualify them.

Still open: full property/flag catalogs, native material events and complete
GT/Susy/pack bootstrap, vanilla/default fluid baseline, generated items/ores,
actual GroovyScript bindings and native Coolants recipe construction. Applied
transformations, source/artifact equivalence and whole-pack parity remain open.
No recipe should be declared fully valid merely because its queue ran.
