# Cleanroom 0.6.8-alpha worldgen observatory fixture

This is a source-only exact-candidate fixture for Minecraft 1.12.2 on
Cleanroom `0.6.8-alpha` (the Forge `14.23.5.2864` compatibility line). It is
not an admitted platform profile, production mod, Blueprint standard, or
runtime proof.

The fixture asks one bounded question: can an opt-in chunk generator emit
stable observations while an unrelated decorator continues to participate
only through the standard Forge lifecycle?

## Separation invariant

The jar contains two independent FML mod containers:

- `workbench_worldgen_observatory` supplies the `wb_observe` world type, a
  read-only wrapper around `ChunkGeneratorOverworld`, and a late read-only
  `IWorldGenerator` checkpoint.
- `workbench_synthetic_worldgen` supplies a synthetic marker-block
  `IWorldGenerator` registered with `GameRegistry.registerWorldGenerator`.

Neither package imports the other. The synthetic mod does not call an
observatory API, implement an observatory interface, post a custom event, or
know that an observer exists. It has no observer mixin, Recurrent Complex
dependency, source reference, adapter, bridge, reflective hook, or
structure-format knowledge. Compatibility with any external decorator must
arise from the same standard lifecycle used by this synthetic mod. The
observatory jar now contains generic Minecraft/Forge probes, but none targets
the synthetic producer or any particular external decorator.

The synthetic producer writes a gold marker on a sparse 8-by-8 chunk grid in
dimension `0`. Its own generic `workbench.worldgen-write.v1` log record names
the producer class, standard callback source, chunk, position, before/after
block identities, and result. That producer-owned record is attribution data;
it is not an observatory integration.

## Lifecycle ownership

`ObservatoryWorldType` constructs the normal Forge-patched
`ChunkGeneratorOverworld` and wraps it with `ObservingChunkGenerator`. The
wrapper delegates every `IChunkGenerator` method. In particular, it calls
`delegate.populate` and does not replay `PopulateChunkEvent`, biome decoration
events, `TerrainGen` decisions, or map-generator hooks. Those calls remain the
responsibility of the delegate that owns them.

After `IChunkGenerator.populate` returns, Forge's patched `Chunk` invokes
registered `IWorldGenerator` instances. The synthetic producer uses weight
`100`; the read-only final checkpoint uses `Integer.MAX_VALUE`. This gives the
fixture an ordinary Forge callback window in which the external write should
precede the final checkpoint. The high weight is an experiment fixture, not a
claim that no other mod can choose the same weight.

## Trace contract

Tracing defaults to disabled in `workbench_worldgen_observatory.cfg`. Disabled
calls select `NoOpTraceSink`; callers do not allocate records or hash chunks.
When enabled, the logger emits lines prefixed with `WORLDGEN_OBSERVATORY`.
Records use schema `workbench.worldgen-observatory.trace.v1` and have four
stable kinds:

- `stage`: deterministic enter/exit or callback-stage identity;
- `decision`: the selected delegate or lifecycle-owner statement;
- `rng`: an observation-only named seed lane that never consumes or replaces
  a runtime `Random`;
- `checkpoint`: a full chunk block-state FNV-1a digest based on registry names
  and metadata.

The fixed field order contains no timestamp, UUID, object identity, thread
identity, or output path. `terrain`, `population`, and `final_checkpoint` use
distinct fixed salts. These lanes describe observations; they do not change
Minecraft, Forge, the delegate, or external generator RNG state.

The independent producer emits `WORLDGEN_WRITE` records. Comparing that record
with the observatory's final block-state checkpoint demonstrates generic
external-write visibility without linking the two mods.

Those Log4j records remain an optional human-readable trace and are not safe
input to canonical normalization. When the raw probe is active, the same
cooperative stage, decision, named-RNG, and checkpoint observations are also
written as typed raw-v1 records in raw ordinal order. Raw checkpoints use true
SHA-256 over a fixed y/z/x stream of length-prefixed UTF-8 block registry names
and one metadata byte; they do not promote the legacy FNV-1a trace digest.
Named RNG records derive the existing observation-only lane seed and never
consume, replace, or advance a runtime `Random`. Raw observation is independent
of the optional human trace, and both paths are no-ops when disabled.

## Exact-candidate low-level probe

The same jar is an early Cleanroom coremod. `ObservatoryLoadingPlugin` uses the
`IEarlyMixinLoader` built into Cleanroom `0.6.8-alpha`; the manifest also names
the required mixin config. The config targets MCP stable `39-1.12` at compile
time and carries an explicit MCP-to-SRG refmap for the production jar. Its
pinned observer dependency is Cleanroom's embedded MixinExtras `0.5.5`; that
dependency is compile-only and is not bundled a second time.

The exact static probe surface is:

- `ChunkProviderServer.provideChunk`, both `loadChunk` forms, and the
  `IChunkGenerator.generateChunk` invocation in `provideChunk`;
- both `Chunk.populate` forms and the owned `IChunkGenerator.populate`
  invocation;
- `EventBus.post` and each `IEventListener.invoke` for Forge's `EVENT_BUS`,
  `TERRAIN_GEN_BUS`, and `ORE_GEN_BUS` while a captured worldgen span is active;
- `ChunkPrimer.setBlockState`, `World.setBlockState(BlockPos, IBlockState,
  int)`, and `Chunk.setBlockState` while a captured worldgen span is active.

Each wrapper calls the original operation directly when observation is
disabled. When enabled, every method, generator call, event post, and listener
invocation has enter plus return-or-throw records. The catch path records the
original `Throwable` and rethrows that same object through a type-erasure
helper. Observer failures are caught separately and cannot replace a worldgen
failure.

Per-listener observation is specifically the
`EventBus.post(Event) -> IEventListener.invoke(Event)` callsite. It is not a
wrapper around `ASMEventHandler.invoke`: the EventBus callsite also sees
non-ASM listeners and Cleanroom's context-setting listener wrapper. The
`cleanroom-worldgen:event_bus.listener_invoke` hook ID therefore binds to
`EventBus.post` plus its exact descriptor; `ASMEventHandlerAccess` is used only
to recover stable owner and registered method identity when that listener form
is present.

`World.setBlockState` and its nested `Chunk.setBlockState` share one
`write_chain_id` only when their exact X/Y/Z position matches; the deepest
observed channel is marked terminal. A reentrant write to another position
starts a separate chain even while the first call remains on the stack. Primer
writes form their own chains and derive absolute X/Z from the active generation
chunk. Caller attribution is generic stack evidence gathered at the write
boundary. An external decorator therefore needs no observatory API, marker
interface, event, adapter, or package-specific target.

For the logical `world_api` and `chunk_primer` boundaries, caller attribution
matches the stack class code source to a mod ID only when exactly one loaded
`ModContainer` has that source. It records a JVM descriptor only when the stack
method name has exactly one declared-method match; ambiguity remains null
instead of being guessed. The terminal `chunk_storage` actor is different by
design: it is the exact instrumented `Chunk#setBlockState` target and carries
the probe-stage transformed digest from the installed binding table. That raw
digest proves the installed hook stage; it is not the final actor digest.
Normalization binds the target to Foundation's separately archived final
LaunchWrapper class bytes and can therefore keep the best available logical
initiator separate from the exact low-level storage target. A regular-file
code source is hashed over its unchanged bytes. An Unimined source-set
directory uses a shared stable tree digest: regular files are sorted by
relative UTF-8 path, then each path length and bytes plus file length and bytes
enter SHA-256. Absolute paths never enter the digest; unsupported or
unavailable sources remain null.

The raw writer records the caller class/method and code-source digest; it does
not guess a mod ID when a jar contains multiple FML containers. The normalizer
must match that source and class against the captured mod inventory and retain
an ambiguous binding when more than one candidate remains.

Nested calls unwind inside-out, so a terminal `Chunk` record naturally appears
before its outer `World` record. `terminal` identifies the deepest observed
channel; it does not mean “last record in the write chain,” and normalization
must preserve the recorded order.

### Exact CleanMix conformance agent

The fixture build also produces one opt-in Java agent for the candidate-bound
CleanMix conformance slices. `runServer` enables that single agent when any of
these absolute, initially absent outputs is supplied:

```text
-PworkbenchCleanMixTraceOutput=<ignored-target>/discovery.ndjson
-PworkbenchCleanMixComponentOutput=<ignored-target>/components.ndjson
-PworkbenchCleanMixConfigLifecycleOutput=<ignored-target>/config-lifecycle.ndjson
-PworkbenchCleanMixTransformerChainOutput=<ignored-target>/transformer-chain.ndjson
-PworkbenchCleanMixFinalDefinitionOutput=<ignored-target>/final-definition.ndjson
```

Each output has a matching `...CaptureId` property. All five may be enabled
for the same launch. Discovery and selected components reuse an exact guarded
`MixinService` substitution. Config lifecycle uses exact SHA-256 guards and
ASM hooks for the candidate's `Mixins`, `Config`, `MixinConfig`, and
`MixinProcessor` bytes; hook counts are also exact. The callbacks observe
resource lookup, required-feature checks, admission, phase eligibility,
`onSelect`/`prepare`/`post_initialise`, batch promotion, duplicates, and
terminal state. They do not install a provider or claim transformation/final
class completion.

Transformer-chain capture additionally guards the exact candidate
`CleanMixService` and `FoundationTransformerProvider` bytes. It observes
provider refresh and delegation-cache rebuilds without requesting
retransformation, retaining ordered live/delegated chains, implementation and
wrapper classes, exact code-source custody, priorities, refresh outcomes, and
the distinct provider/Foundation exclusion layers. Generated `$wrapper.*`
shells are retained while their parent implementations are bound to the real
artifact rather than the transient `asmgen:` URL.

Final-definition capture additionally requires
`workbenchCleanMixFinalDefinitionTargets` as a comma-separated nonempty target
set and enables Foundation's class dump. It instruments the exact guarded
`ActualClassLoader.findClass` bytes, records only successful returned classes
as definitions, and joins them to exact dump entries. AppClassLoader copies of
CleanMix lifecycle classes are not woven: lifecycle state belongs to the
candidate's LaunchClassLoader definitions, and crossing those loader owners
would invalidate exact target cardinality.

Import raw config evidence only through
`profiles/platforms/cleanroom/tools/import_cleanmix_config_lifecycle.py`; it
verifies the candidate/toolchain locks, launch and fixture result, agent,
installed owner archive and config entry before producing Crucible's closed
configuration-lifecycle receipt.

Import raw transformer-chain evidence only through
`profiles/platforms/cleanroom/tools/import_cleanmix_transformer_chain.py`; it
verifies the same launch custody plus every chain artifact, exact provider and
Foundation identities, ordered delegation, required exclusion layers, and
refresh-cause coverage before producing Crucible's closed epoch receipt.

Import final-definition evidence only through
`profiles/platforms/cleanroom/tools/import_foundation_final_definitions.py`;
it verifies successful definition returns, the Foundation artifact, dump
manifest and bytes, every requested target, and the candidate/toolchain launch
custody before producing the closed final-byte receipt.

### Raw capture transport

Enable the probe only for a disposable experiment, preferably with an explicit
capture identity and output under Workbench's ignored evidence store:

```text
-Dworkbench.worldgen.observatory.probe.enabled=true
-Dworkbench.worldgen.observatory.probe.capture_id=<experiment-local-id>
-Dworkbench.worldgen.observatory.probe.mode=lossless-fixture
-Dworkbench.worldgen.observatory.probe.output=<target>/.workbench/evidence/worldgen-observatory.raw.ndjson
```

Without an explicit output, the default is
`logs/worldgen-observatory.raw.ndjson`. The writer opens with create/write/
append, writes one JSON object per line, and flushes synchronously after every
record. There is no sampling queue and the probe intentionally reports zero
dropped records. A writer failure latches the observer off and emits a
best-effort stderr diagnostic; such a run is incomplete, never successful.
Use a unique capture ID and output file for every process start; ordinal
counters are process-local and intentionally restart at zero.

The raw format is
`workbench-cleanroom-worldgen-observatory-raw-v1`. It is deliberately **not**
`workbench-crucible-worldgen-observatory-record-v1`: transform-time code cannot
authoritatively bind every run, platform, pack, snapshot, world-instance, and
actor-artifact identity required by the admitted capture contract. Crucible's
normalizer must bind those identities, validate exact candidate/source
digests, map the raw payloads into
`WORKBENCH-CRUCIBLE-WORLDGEN-OBSERVATORY-CAPTURE-V1`, and only then may its
bundle sealer publish evidence. Raw residue has no success seal. A crash or
missing normalizer/sealer result is incomplete evidence.

The jar carries `workbench-worldgen-observatory-raw-v1.schema.json` for this
pre-contract transport. That schema validates transport shape only; it grants
no Atlas authority and is not a substitute for canonical normalization.

Its closed record-type set includes low-level span/event/write/chunk records,
probe health and diagnostics, plus `capture_control`, `fixture_driver`,
`cooperative_stage`, `decision`, `rng_observation`, and `checkpoint`. Payloads
remain raw evidence. In particular, a configured `capture_id` is an
experiment-local pairing value, not an admitted run identity or a generated
nonce.

### Non-interactive dedicated-server driver

The jar also contains an opt-in driver for disposable, newly created dedicated
servers. Configure `server.properties` with the same explicit numeric seed used
below and `level-type=wb_observe`, then add JVM properties such as:

```text
-Dworkbench.worldgen.observatory.fixture_driver.enabled=true
-Dworkbench.worldgen.observatory.fixture_driver.expected_seed=<exact-long-seed>
-Dworkbench.worldgen.observatory.fixture_driver.order=forward
-Dworkbench.worldgen.observatory.fixture_driver.result=<target>/.workbench/evidence/worldgen-observatory.fixture-result.json
```

`order` accepts only `forward` or `reverse`. At `FMLServerStartedEvent`, after
ordinary spawn preparation has finished, the driver verifies the physical
dedicated server, seed, Overworld, and `ObservingChunkGenerator`. It then uses
one explicit 2-by-2 route, `(64,64)`, `(65,64)`, `(64,65)`, and `(65,65)`, far
from spawn. It first
calls `ChunkProviderServer.provideChunk` for the route and then calls each
returned `Chunk.populate(provider, provider.chunkGenerator)`. That public
Minecraft/Forge lifecycle decides which loaded chunks are eligible to
populate; the driver does not call any `IWorldGenerator`, decorator, listener,
or event bus directly. Reverse mode reverses the same list rather than changing
the selected region. In exact 1.12.2 semantics, the loaded east, south, and
southeast neighbors make northwest `(64,64)` eligible for its owned populate
path; that chunk is also eligible for the independent synthetic marker.

The absent-selection default above remains the identity-bearing V1 fixture
verbatim. A bounded V2 rectangular selector is available for deterministic
fixture discovery and for replaying a selected external-decorator occurrence:

```text
-Dworkbench.worldgen.observatory.fixture_driver.route_anchor_x=<chunk-x>
-Dworkbench.worldgen.observatory.fixture_driver.route_anchor_z=<chunk-z>
-Dworkbench.worldgen.observatory.fixture_driver.route_size=<2-through-32>
```

All three properties are required together. V2 visits the declared square in
z-major, then x-major order (or its exact reverse), emits
`workbench.worldgen-observatory.fixture-result.v2`, and binds the anchor and
size in a `selection` object. It still calls only public Minecraft chunk
provision and population entry points. A large observer-off route may locate a
candidate occurrence, but acceptance evidence should replay the minimum 2-by-2
window in fresh worlds under full observation. V1 parsers continue to reject
V2 rather than silently changing the fixed-region contract.

Driver mode automatically keeps the raw probe disarmed through spawn
preparation even when the raw-probe JVM property is enabled. Immediately before
the explicit route it emits a typed `capture_control` start, then the retained
install-time `applied_not_reached` health table in sorted hook-ID order. New
health bindings loaded during the route are emitted when registered. After the
route, the driver synchronously calls `saveAllWorlds(true)` and `World.flush`,
computes final SHA-256 semantic checkpoints for every selected chunk, requests
server shutdown, writes the deterministic result artifact, emits a raw
`fixture_driver` completion marker, and finally emits `capture_control` stop
and disarms. The start and stop share a `capture_control_id` derived from the
configured capture ID; the raw global sequence and per-thread sequence remain
zero-based process-local ordinals. A missing stop, completion marker, balanced
span/write stack, or available raw writer makes the raw run incomplete.

The result artifact is written whether the raw probe is enabled or disabled.
Its stable JSON has no time, UUID, object identity, output path, or probe flag;
it contains the seed hash, route/selector hashes, order, completion/save/
shutdown state, each selected chunk's final semantic block-state SHA-256, and a
mod-ID-sorted runtime inventory. Each inventory row contains the loaded
`ModContainer` ID, the same regular-file-or-source-tree SHA-256 used by raw
actors, and concrete mod-instance class name. An unsupported source or
unavailable instance is written explicitly as `unavailable`; no path or guessed
identity is substituted. This gives generic
normalization enough evidence to distinguish class-owning packages even when
multiple FML containers share one jar, without naming the synthetic producer.
This makes observer-off/on comparison independent of raw logging. It is fixture
evidence, not a canonical Crucible capture or success seal. Use a distinct
result path per experiment. The driver defaults to off, requires the expected
seed property when enabled, and initiates shutdown on failure without replacing
the original throwable.

When the fixture driver is off, raw probing retains its original manual mode:
it is active from process start. The optional
`probe.defer_until_fixture_driver=true` property exists for controlled
experiments, but setting it without the driver leaves the probe disarmed.

### Probe health

`ProbeMixinPlugin` hashes each target class immediately before this config is
applied and again after application. It counts the exact merged handler method
for every hook and requires exactly one. A zero or duplicate count aborts mixin
application instead of silently weakening coverage. With capture enabled it
emits `applied_not_reached` health records containing the pre-probe and
transformed SHA-256 values and expected/observed counts; the first invocation
of each hook emits `reached`.

In driver-managed capture, `postApply` retains that binding/health table while
capture is disarmed and the arm boundary emits it after `capture_control`
start. Manual active-from-start capture emits each health record at `postApply`.

The pre-apply digest is the bytecode image visible to this mixin config, which
may already include an earlier transformer. During normalization, it must not
be substituted for the pristine candidate artifact digest. Source compilation
and remapping prove neither that the early loader ran nor that any hook was
reached. The post-apply digest is likewise intermediate: later transformers
may change the final definition. Foundation archives that exact final class
separately. Across observed equivalent sessions, final bytes differed because
`MixinMerged.sessionId` held a random UUID even though `javap` disassembly was
identical. Cross-run comparison therefore uses an explicitly normalized
semantic bytecode fingerprint, while actor binding and custody continue to use
the exact per-run final-byte SHA-256.

## Source checks

Run the Java-independent contract suite from this directory:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

It verifies the exact candidate binding, independent mod metadata and package
imports, standard Forge registration, delegation of owned lifecycle methods,
absence of duplicate event posting, stable trace fields, no-op behavior,
fixed RNG vectors, generic write attribution, early-coremod manifest, exact
MCP-to-SRG selectors, required injection counts, fail-open throwable paths,
three-bus listener boundaries, exact-position nested write chains, unique
source/method attribution rules, SHA-256 checkpoints, dedicated driver order,
deferred capture controls, deterministic result evidence, and append-only raw
transport.

## Candidate build and runtime experiment

This directory intentionally does not carry a Gradle wrapper. With a suitable
Gradle installation and network access, use Java 25 and run:

```bash
gradle \
  --project-cache-dir ../../../../../../.workbench/gradle-project-cache/worldgen-observatory-fixture \
  clean build
```

The command and build script place Gradle project state and build output under
ignored `.workbench/gradle-project-cache/` and `.workbench/build/`, not inside
this source fixture.

The Unimined build pins Minecraft `1.12.2`, MCP stable `39-1.12`, and Cleanroom
`0.6.8-alpha`. The remapped jar must contain the early mixin config, explicit
refmap, coremod manifest attributes, and no embedded Mixin/MixinExtras copy.
Unimined supplies `runServer`, but this fixture refuses to run it without an
explicit absolute external working directory so game state cannot silently
land under the profile. That task also disables refmap lookup for its MCP-named
development target only; the production jar retains and requires the pinned
MCP-to-SRG refmap. It suppresses Cleanroom's automatic development-output mod
admission so the installed remapped jar is the sole fixture source and a
duplicate-mod crash cannot masquerade as a successful experiment:

```bash
gradle -PworkbenchServerRunDir=/absolute/path/to/.workbench/targets/<run> runServer
```

Install it into a disposable exact-candidate client or dedicated server.
Enable cooperative tracing in `config/workbench_worldgen_observatory.cfg` and,
independently, enable the raw probe with the JVM properties above. Create a new
world with the `wb_observe` world type and use either a manual route or the
dedicated-server driver above.

Expected evidence is:

1. deterministic generate/populate stage and RNG-lane records;
2. a synthetic `WORLDGEN_WRITE` record for eligible Overworld chunks;
3. a later `forge.world_generators.final` checkpoint;
4. a closed raw capture-control pair and fixture completion marker in driver
   mode;
5. byte-identical deterministic result JSON for observer-off/on runs with the
   same seed and order.

## Still requiring a completed Cleanroom runtime matrix

Source checks, a successful exact Gradle build, and partial bytecode findings
do not establish the complete runtime behavior. The following remain required
as retained matrix evidence:

- launch disposable Cleanroom `0.6.8-alpha` client and dedicated-server
  fixtures on the exact Java/toolchain identity;
- validate install-time `applied_not_reached` health for every required hook,
  then runtime `reached` health for the hooks exercised by the fixture;
- validate balanced enter/return/throw spans and zero writer failure or dropped
  detail in normalized capture evidence;
- confirm both FML containers load and `wb_observe` is selectable;
- confirm Forge event and `IWorldGenerator` order from runtime evidence;
- verify the marker write appears before the final checkpoint without
  recursive chunk generation or crashes;
- run same-seed A/A trials across launches, chunk visitation orders, and
  client/server sides;
- repeat with real pack decorators as unmodified external mods, judging them
  only through standard lifecycle evidence.
