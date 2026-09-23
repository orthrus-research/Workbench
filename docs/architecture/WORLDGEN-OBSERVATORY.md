# Worldgen Observatory

Status: experimental, dedicated-server implementation

Worldgen Observatory explains how a bounded region was generated. It captures
phase, event-listener, decision, RNG-observation, chunk-access, and block-write
records from one exact runtime, then lets Atlas answer bounded causal questions
over the admitted capture.

It is a diagnostic path, not a construction API or release gate.

## Authority

- The selected platform profile owns exact hook targets, mappings, candidate
  applicability, and probe policy.
- Foundation and Project Intelligence own runtime, artifact, and transformed
  bytecode identity.
- Crucible owns the experiment, raw admission, capture health, canonical
  bundle, completion state, and immutable publication.
- Atlas owns interpretation, including writer attribution, event-listener
  changes, bounded equality, and first-divergence answers.
- Strata owns admitted final physical state for a captured region.
- World Studio presents owner-backed answers without copying or strengthening
  them.
- The selected pack profile owns pack-specific action and acceptance policy.

Blueprints may consume an explicit owner-backed gate. A raw log, live view,
Strata image, or Atlas projection is not construction approval by itself.

## Exact profile boundary

The current Cleanroom candidate owns its
[hook catalog](../../profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-hook-catalog-v1.json)
and
[probe plan](../../profiles/platforms/cleanroom/candidates/0.6.8-alpha/worldgen-observatory-probe-plan-v1.json).
Those files describe a bounded inventory for one transformed runtime; they do
not assert complete coverage of every world-generation path.

The profile distinguishes:

- supported modification seams, such as Forge events, `WorldType`,
  `WorldProvider`, `BiomeProvider`, `IChunkGenerator`, and registered
  `IWorldGenerator` implementations; and
- invasive observation-only probes around generation, population, event
  dispatch, decoration, chunk access, and block storage.

Observation-only probes must not be presented as supported extension points.
A different Cleanroom candidate, Forge patch line, mapping set, physical side,
or final class definition requires a separately bound profile and new runtime
evidence. Historical Forge evidence is not an automatic fallback.

## Capture and custody

```text
exact profile + exact runtime identity
                 |
                 v
       disposable runtime process
          |                 |
          | raw records     | final class bytes
          v                 v
      Crucible admission and closure
                 |
                 v
       sealed capture or incomplete residue
                 |
                 v
          Atlas interpretation
                 |
                 v
         World Studio presentation
```

One run binds the candidate, Java runtime, transformed classes, installed
artifacts, configuration, side, world, seed, world type, dimensions, selector,
route, capture mode, and output paths. Two process starts never append to the
same raw capture identity.

Raw output is append-only. Normalization creates a separately versioned
Crucible bundle and never rewrites the raw file. Worlds, logs, raw records,
class dumps, bundles, reports, and rebuildable indexes remain under ignored
`.workbench/` storage.

Human-readable logs are companion diagnostics. They cannot replace typed
probe health, spans, event transitions, decisions, writes, checkpoints, or
completion records.

## Closure, causality, and coverage

The generic
[capture contract](../../modules/crucible/contracts/worldgen-observatory-capture-v1.md)
defines the record envelope, capture modes, ordering, budgets, and completion
seal. Important invariants are:

- every required hook has an exact target tuple, expected injection count,
  observed count, and reachability state;
- an entered span returns or throws exactly once in a completed capture;
- nested event posts and listener calls retain their own spans and before/after
  state;
- a block-write chain joins only one dimension, position, generating chunk,
  and target chunk, while keeping the initiating actor separate from terminal
  storage code;
- exact actor attribution requires a unique runtime inventory, code-source,
  class, method, descriptor, mapping, and final-class match; and
- ambiguous or unbound evidence remains unresolved rather than inheriting an
  owner from a stack label or package name.

Coverage is explicit. Missing required hooks, dropped detail, writer failure,
an unclean stop, open spans, corrupt input, or a failed identity binding
prevents a completed seal. Crash residue stays incomplete even when all rows
written before the crash were valid.

Foundation retains exact final-class hashes for custody. A declared semantic
bytecode fingerprint may support cross-run comparison, but it never replaces
the exact digest or authorizes actor attribution. See the
[semantic-bytecode contract](../../modules/crucible/contracts/foundation-semantic-bytecode-v1.md).

## Comparison and physical state

Independent A/A processes, observer-on/off runs, route variation, restarts,
and forced incomplete runs can bound neutrality and repeatability. Equality is
always limited to the declared canonicalizer, semantic domains, stages,
chunks, profiles, and observed coverage. RNG observation digests are not a
trace of every runtime `Random` call, and one equal checkpoint is not proof of
whole-world determinism.

Strata is the final-state companion. Its exact block, biome, heightmap, cave,
ore, lithology, and fluid observations answer what exists after the bounded
request; Observatory answers what was causally observed. The
[Strata receipt](../../modules/crucible/contracts/strata-observation-receipt-v1.md)
joins exact inputs without converting final state into causal attribution or
visual-quality approval.

Physical review may generate or populate chunks. Use a disposable world unless
the user explicitly authorizes inspection of a stored world.

## Forge compatibility

Forge retains ownership of its standard population lifecycle. A replacement
generator must not invoke `GameRegistry.generateWorld` a second time, and an
observer must not run decoration merely to manufacture evidence. Independent
decorators participate through normal Forge registration and are observed
through generic event and write boundaries; Workbench does not require a
package-specific adapter.

## Executable surfaces

```bash
python3 modules/crucible/tools/run_cleanroom_worldgen_case.py --help
python3 modules/atlas/tools/query_worldgen_observatory.py --help
python3 modules/atlas/tools/query_worldgen_observatory_exact.py --help
python3 tools/workbench.py worldgen dev --help
```

`workbench worldgen dev --observatory-artifact ...` adds exact same-run
observation to a fresh development iteration. The operational iteration report
does not become Atlas authority simply because the observer was installed.

Atlas query semantics are defined by the
[Worldgen Observatory query contract](../../modules/atlas/contracts/atlas-worldgen-observatory-query-v1.md).
The implemented path is dedicated-server scoped and makes no claim of
integrated-client coverage, exhaustive hooks, production-scale telemetry,
whole-generator determinism, stable profile support, or release approval.
