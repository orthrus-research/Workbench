# Native ore-prefix and marker types

This is a bootstrap dependency, **not a recovered item/ore registry or recipe
validity verdict**. Prefix state determines which forms *may* generate; it does
not prove that a generated item exists or belongs to `dyeCyan`.

## Retained source and ownership

The source lock [native-prefixes.lock.json](../sources/native-prefixes.lock.json)
binds six GTCEu files at `9fe140febe8747bbe2f06dfd570421331ec06f4b`.
`tools/axiom_prefix_sources.py` retains all 101 prefix declarations, their
constructors, generation predicates, `init()` effects, amount overrides,
secondary materials and handler execution; marker declarations/color mapping;
and all 65 icon-type declarations with native naming/ID behavior.
The two native handler interfaces and material-unit/tier constants are retained.

This replaces the fluid domain's former three-icon metadata carrier with the
native catalog. Its existing server-side texture identifier projection stays
available. Client localization, resource-pack fallback and renderer caches are
not implemented or exposed as qualified behavior.

Marker initialization uses the separately supplied, hash-verified original
Minecraft `EnumDyeColor` bytecode (`ahs`), its string interface (`ro`) and text
formatting dependency (`a`). `NativeDyeColor` holds the actual enum identity;
it does not copy a color table, invent constants or normalize names. No Minecraft
bytecode is included in Axiom's source or distribution. These are untransformed
selected utilities, not evidence of the installed pack's transformation state.

Prefix dependencies must be bound explicitly to live source-catalog field reads
and the two configuration values: `generateLowQualityGems` and
`allUniqueStoneTypes`. There are no default values, material-name lookup aliases
or manufactured catalog entries. A bound source field can legitimately be null;
the source's use of it retains native failure behavior. Binding fixtures is not
native Forge configuration loading or complete material producer execution.

Wood fluid-pipe verification now applies its four actual prefix exclusions when
these dependencies are present. Missing bindings stop `incomplete` before prefix
class initialization, preserving earlier material/property effects.

Native static catalogs still require a fresh disposable JVM for each evaluation.
Reopening a FluidEnvironment in the same process does not reset prefix IDs,
marker identities, ignored sets or handler queues.

## Important native behavior

- Minecraft's `SILVER` name is `silver`; the GT marker is `light_gray`. The
  original color map therefore contains the silver enum key with a **null value**,
  while `LightGray` remains in GT's marker array. The Guava inverse map retains
  that null association. This must not be silently repaired.
- Color-array order is declaration order. Tier marker names use the source's
  default-locale `toLowerCase()`. `Component` markers initialize lazily, separately
  from `register()`.
- Duplicate prefix names fail before consuming an ID. A self-referencing prefix
  with a null material fails after consuming an ID but before entering the map.
  The `values()` collection is a mutable live view; removing an entry permits
  registering that name again with a new ID.
  Wood verification still mutates its original static prefix objects after such
  a replacement; looking up the same name would target the wrong identity.
- A null generation predicate permits generation; self-referencing or ignored
  material cases prevent it first. Low-quality-gem configuration is read when
  its predicate executes, not captured once during catalog initialization.
- Amount overrides use the native Fastutil float map and JVM float multiplication
  followed by long conversion. Null-material queries return the base amount even
  if the map contains a null-key override. NaN/infinity retain JVM behavior.
- Handler registration deduplicates generated materials in a HashSet, not an
  ordered list. Prefix iteration retains the native HashMap. Neither is sorted
  by the engine. Typed handlers check the property and `NO_UNIFICATION` flag.
- Throwing handlers leave the current thread-local prefix/material and pending
  generated set intact; the next invocation can repeat earlier work. Successful
  completion clears them. There is no added rollback or `finally` cleanup.
- `init()` is not idempotent: another invocation appends secondary materials
  again. Ignored sets and amount overrides have their own native semantics.

## Qualification

```sh
python3 tools/axiom_prefix_conformance.py \
  --gtceu PATH_TO_GTCEU --java-home PATH_TO_SELECTED_JDK \
  --engine-home modules/axiom/jvm/build/install/workbench-axiom-engine \
  --registry-root PATH_TO_ORIGINAL_LIBRARIES --report NEW_RECEIPT.json
```

The comparison independently compiles the locked source extraction and runs it
and the installed engine in fresh selected JVMs under worker kernel restrictions.
Both use explicitly **synthetic** material-field/configuration inputs. They cover
every prefix/icon declaration, native markers, construction and handler failure
contracts, a 64-graph generation-predicate matrix (including rejected property
combinations), and 4,096 state/amount vectors for each unique-stone configuration.
A controlled `dustTiny` source edit changes its material unit from `M/9` to
`M/10`: the catalog digest must change while unrelated traces and markers do not.

A real Groovy 4.0.30 source fixture exercises prefix mutation, closure-to-predicate
dispatch and rejection of a string where a material is required. This is a test
of native Groovy/type interaction—not execution of the pack's material scripts,
a public arbitrary-code interface, or a lint-success verdict for those scripts.

Receipts bind source locks, extraction recipes, shared source, installed archives,
native libraries and the selected JDK. Shared dependency ports are named rather
than counted as independently verified behavior. Full GT/Susy/addon/pack
producers, native configuration loading, enchantment/vanilla-fluid identities,
applied mixins and generated item/fluid/ore membership remain open. `target`
stays non-executing; no `bootstrap` operation is admitted here.
