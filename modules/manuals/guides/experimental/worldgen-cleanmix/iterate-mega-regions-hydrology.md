# Tune mega-regions, hydrology, and biome palettes

Status: experimental working guide

Use this after the basic fixture reaches server readiness. Change one plan
field at a time, generate a fresh fixed-seed world, and compare the same chunk
window.

## Authority boundary

The plan selects development behavior; it does not establish terrain quality,
causality, or profile support. Runtime logs and JFR are bounded observations.

## Plan controls

Keep these concerns separate:

- octave fields: seed salts, frequency, amplitude, and octave count;
- mega-regions: region scale, boundary blending, climate, and lithology;
- biome palettes: constraints over the installed BOP biome registry;
- hydrology: tile halo, precipitation, permeability, accumulation, channel
  threshold, and stream width/depth;
- carvers: Forge-selected cave and ravine generators; and
- caches: explicit maximum chunk, point, region, and watershed tile counts.

The Groovy plan must be validated and published before a world constructs.
Java owns the per-sample and per-block hot paths.

## Comparison loop

1. Record the source revision, plan bytes, mod set, seed, world type, and chunk
   window.
2. Build a production-remapped jar.
3. Generate a fresh world and stop it normally.
4. Retain the structured `WORLDGEN_PROTOTYPE` records.
5. Change exactly one plan or source variable.
6. Repeat with another fresh world and the same comparison controls.

The normal runner can automate the loop:

```bash
python3 tools/workbench.py worldgen dev \
  --profile supersymmetry \
  --mode debug \
  --seed 8675309 \
  --region=-24,-4,16,16 \
  --no-open
```

Use the fixture's log comparator for generator records. Timing and cache
telemetry may be excluded only when the comparator declares those fields;
terrain, biome, lithology, hydrology, carving, and failure records must remain
semantic inputs.

## Profile the sampling path

Use `--mode performance` to record only the bounded World Studio custom JFR
events. Inspect tile construction, waits, cache pressure, and chunk sampling
before changing cache limits. A faster sample is useful only when its semantic
records remain equivalent to the chosen baseline.

Final block-state inspection is a separate operation; use
[the Strata guide](observe-world-studio-with-strata.md) when logs alone cannot
answer the visual or physical-state question.
