# Forge recipe observer

This experimental profile-owned observer reads the original Forge 1.12.2 GT
recipe and machine registries for the separately pinned circuits branch. It is
independent of the Cleanroom producer, its overlays and its shutdown shim.
Compilation does not establish that the branch initializes successfully.

## Build and preparation

Installed callers use the profile's `recipe_capture_runtime.build_observer`
adapter with Core's process and filesystem hosts bound. Its Java sources are
profile-owned package resources. The builder selects `javac.exe` on Windows and
`javac` on Linux. From a repository checkout, with API, Core and the profile
available:

```bash
PYTHONPATH=api/src:core/src:profiles/packs/supersymmetry/src python3 \
  profiles/packs/supersymmetry/atlas/probes/forge-recipe-observer/build.py \
  --java-home /path/to/java8-jdk \
  --classpath /path/to/original-forge.jar \
  --classpath /path/to/original-minecraft-server.jar \
  --output-dir /path/to/new-private-build
```

Repeat `--classpath` for the selected launch libraries. Inputs must be explicit
regular files. The builder uses Core's process port, retains compiler output,
rehashes inputs after compilation, checks Java 8 class versions, and writes a
reproducible JAR plus a build receipt. It shares only `CanonicalJson.java` and
`Hashing.java` with the existing producer. It does not remap or patch game code.
The builder retains original package resource identities, copies their exact
bytes into its private `sources` directory and passes relative ASCII source
names to Java 8. Its receipt binds both source locations and compiler arguments;
changed or added staged sources fail verification. Classpath arguments are
relative to the build directory. Windows dependencies with non-ASCII relative
names require a compatible Core execution-path strategy before compilation.
The [developer-local capture workflow](../../../../../../modules/workbench-shell/contracts/developer-recipe-capture-v1.md)
composes this builder with an explicitly selected server and saved pack checkout.
It replaces the declared source roots and excludes the original GroovyScript
`cache/groovy` compiled-script cache when preparing the isolated runtime.
Native game qualification, including legacy Java path behavior on Windows,
remains separate from successful compilation.

An execution owner must prepare a disposable original Forge server context,
verify the branch/source/dependency/JVM and observer bytes, bind runtime-only
settings and retain the Core process result. Minecraft EULA acceptance is a
separate user action. Neither the builder nor observer writes `eula.txt`.

The complete native matcher scan runs synchronously during a server tick.
The execution owner must explicitly record any changed watchdog setting, such
as `max-tick-time=-1`, and retain process cancellation through Core. Heap and
other execution settings belong to that prepared context.

## Arming and lifecycle

The observer is inert unless `workbench.runtimeGraph.enabled=true`. The armed
dedicated-server capture requires these system properties:

- `capture_id`, `launch_id`
- `input_manifest_path`, `input_manifest_sha256`
- `candidate_lock_sha256`, `adapter_profile_sha256`
- `minecraft_path`: the exact original server artifact
- `output`: an absent absolute directory beneath an existing real directory

Each name above uses the `workbench.runtimeGraph.` prefix. Compatibility-only
and experiment modes are refused. The input manifest binds the same launch,
capture and physical side, and must declare `observation_preparation` with
policy `forge-loli-original-capability-materialization-v1` and phase
`before-two-effective-samples`. Its exact bytes and declaration are verified,
and the output is reserved, before any capability initializer runs.

After ordinary `FMLServerStartedEvent`, the first END server tick performs one
explicit preparation pass over the original recipe item objects and ordinary
stored targets. It invokes the original LoliASM initializer for deferred objects;
this is a native initialization intervention. Callbacks may change visible NBT.
The sealed `capability-preparation.json` retains all original recipe records,
before/after recipe bindings, shared stack references, and local and overall
stack transitions. Changed identities remain distinct. The observer refuses
replaced recipe or stack references and does not repeat preparation until stable.

It then independently rebuilds two full effective-state samples. The publisher
requires byte-identical canonical records at the named `post-start-end-tick`
checkpoint, with the preparation policy also recorded. It publishes without
replacement, writes closed payloads, and creates `.capture-complete` last.
Crucible refuses incomplete directories. Original staging bytes and failure
records remain available. A normal server shutdown is requested after the
attempt; shutdown failure remains in process output.

## Meaning and limits

The seven categories are finite recipe occurrences, recipe maps, registered
machine prototypes, machine/map bindings, and optional finite item-matching
witnesses, original Groovy metaitem name bindings, and ordinary item-matching
witnesses. Name bindings record
actual resolver results so a source spelling can be connected to its exact
captured stack without guessing numeric metadata. Recipe activity uses the
actual lookup/category identity union.
The observer retains original slots, batch values, reusable inputs, recipe
properties and ordered chance entries. Registered prototypes do not establish
machine formation, inventory, operating gates or player access.

For exact original ore and configuration-circuit classes without custom NBT
predicates, it invokes `acceptsStack` on a copy of every candidate in the captured
recipe item domain. The profile independently checks those results against
source-backed predicates and original artifact/class pins before admitting
exact finite alternatives. It preserves typed NBT, including the stored element
type of an empty list. Unsupported matching remains explicit; the domain does
not enumerate every possible stack in the game.

The ordinary item adapter admits a narrower case: original stored targets with
null NBT and zero serializable capability writers after observed initialization.
It reads the original stored matching keys independently; preparation does not
rewrite those keys to match a changed stack. It checks candidate copies
for every captured occurrence before resource deduplication and compares native
predicate results with the independently interpreted finite relation. Tagged
targets, changed copy identities and undecided capability comparisons remain
unqualified. This does not establish compatibility with future inventory stacks.
Preparation does not waive the two samples' strict initialization, identity,
count, copy, native predicate or target-stability checks. The projected graph
describes the state after preparation, with the original state retained as
separate evidence.

Original artifact/class hashes identify original bytes, not transformed class
definitions. Seals establish retained content integrity. They do not alone
prove a clean native exit, profile applicability, source causation, whole-pack
coverage, quest completion or release qualification. See the
[finite projection contract](../../finite-recipe-projection-v1.md) and
[Crucible capture contract](../../../../../../modules/crucible/contracts/retained-runtime-capture-v1.md).
