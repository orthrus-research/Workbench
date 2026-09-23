# Prototype a total worldgen replacement

Status: experimental working guide

Use this only when the intended design owns the complete overworld generator
boundary. Smaller terrain or feature experiments should use the ordinary
[World Studio iteration](run-worldgen-iteration.md).

## Authority boundary

Source completeness, a successful local launch, captured runtime behavior,
and causal interpretation are separate checks. This guide does not approve a
replacement or claim compatibility.

## Replacement boundary

A complete prototype must make these surfaces coherent:

- `WorldType`: explicit selection and provider ownership.
- `BiomeProvider`: generation, viable-biome, search, cache, and spawn answers.
- `IChunkGenerator`: chunk generation, population, structures, creature
  queries, recreation, and nearest-structure behavior.
- Forge lifecycle: terrain hooks, biome decoration, animals, ice, and the
  platform's normal world-generator callback path.

Returning empty data from an unimplemented method is not completeness. Every
unsupported surface must fail explicitly or have a documented safe behavior.

## Comparison procedure

1. Declare one change, its oracle, and exact comparison controls.
2. Run source and fixture tests, then build a production-remapped jar.
3. Install it only into a disposable target with a new world.
4. Run an unchanged control twice before interpreting a candidate difference.
5. Bind manifest, artifact, launch, log, completion, and observer-health
   records.
6. Normalize each closed run through Crucible.
7. Ask Atlas for the first supported divergence.
8. Follow the divergence to its lifecycle actor and terminal write.
9. Change one rule and repeat with fresh worlds.

```bash
python3 modules/crucible/tools/assemble_exact_runtime_manifests.py --help
python3 modules/crucible/tools/audit_exact_runtime_session.py --help
python3 modules/crucible/tools/run_cleanroom_worldgen_case.py --help
python3 modules/atlas/tools/query_worldgen_observatory.py --help
```

The first divergent record localizes a boundary; it does not necessarily name
the root cause. Compare source ownership, RNG inputs, lifecycle order, and
final state before attributing it.

## Review checklist

- The world type selects the intended provider and generator.
- Biome answers agree across generation and lookup APIs.
- Every generator method has deliberate semantics.
- Population invokes each owned stage exactly once.
- Normal Forge callbacks remain owned by the platform lifecycle.
- Controls use identical source, artifacts, seed, plan, and chunk requests.
- Every run has a fresh identity and a normal terminal record.
- Conclusions include bounds, unknowns, and unavailable evidence.
