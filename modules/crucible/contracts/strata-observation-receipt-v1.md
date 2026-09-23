# Crucible Strata observation receipt V1

Status: experimental generic external-observation contract; a passing receipt
does not admit Atlas evidence or approve visual quality

## Purpose

This contract binds one bounded, successful Strata capture of exact populated
Minecraft chunk state to the Workbench runtime and external Strata interface
that produced it. It gives World Studio a repeatable way to move from a
generated Cleanroom world to a validated `.strataview` package, a lossless
sharded manifest, and an optional renderer screenshot.

Strata remains an external fidelity-first analyzer. Workbench does not copy its
capture or rendering implementation and Strata does not become a second source
of world-generation rules.

## Custody

The adapter must keep the runtime, world, scan, package, shards, logs,
screenshot, and receipt under ignored `.workbench/` storage. The receipt binds:

- every selected Strata interface source file by relative path, size, and
  SHA-256;
- the exact server and observer jars and complete installed mod set;
- dimension, seed, world type, provider, generator, chunk window, population
  state, and World API cross-check counts from the dense scan;
- the raw dense scan, monolithic package, sharded manifest, capture report,
  launch log, driver log, renderer build log, and optional screenshot; and
- successful extraction, package, shard, renderer-build, and optional renderer
  smoke states.

The external project path itself is deliberately absent from the identity.
The selected interface file bindings identify what was used without treating a
mutable workstation location as authority.

## Completion rules

A V1 receipt is complete only when:

1. the runtime and every generated artifact are inside Workbench's ignored
   operational storage;
2. exactly one Strata observer is installed;
3. the scan uses `dense-section-paletted-states` and every selected chunk is
   terrain-populated;
4. dense and sparse World API comparisons report zero mismatches;
5. raw extraction, monolithic package, and lossless shard validation all pass;
6. the renderer TypeScript/Vite build passes; and
7. when renderer smoke is requested, its screenshot exists and is bound.

A failed command leaves ordinary ignored diagnostic residue but produces no
complete receipt. It must not be presented as a partial pass.

## Interpretation boundary

The dense scan and packages describe physical chunk state after the observer's
bounded generation/population request. They can show terrain, biomes, water,
caves, placed blocks, and exact local neighborhood context. They do not prove
which generator phase or listener caused a block; causal attribution remains an
Atlas query over admitted Worldgen Observatory evidence.

Strata's shell/merged geometry and visual treatments are display-only. Exact
dense sections and exposed source faces remain available, but a screenshot is a
review surface rather than a visual-quality approval. Capturing a window may
generate or populate chunks, so the adapter must use a disposable or explicitly
authorized world.

Recurrent Complex remains independent. This adapter adds no RC dependency,
callback, registry knowledge, or structure-format path.

## Executable surface

The generic entry point is:

```bash
python3 modules/crucible/tools/run_strata_observation.py \
  --runtime .workbench/<runtime> \
  --java-cmd /path/to/runtime/java \
  --label <fresh-label>
```

The Strata source root defaults to a sibling `strata` checkout and may be
overridden with `--strata-root` or `WORKBENCH_STRATA_ROOT`. The tool builds the
renderer, invokes Strata's own capture driver, installs Strata's observer,
validates and shards the result, writes the receipt, and emits a viewer handoff.

The machine-readable form is
[`strata-observation-receipt-v1.schema.json`](../schemas/strata-observation-receipt-v1.schema.json).
