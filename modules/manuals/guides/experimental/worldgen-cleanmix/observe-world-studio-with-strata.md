# Inspect World Studio output with Strata

Status: experimental working guide

Use Strata when you need exact final chunk state and a local visual inspection
surface. Strata complements Worldgen Observatory causality; it does not replace
it.

## Authority boundary

Crucible owns the capture receipt, Strata owns its external capture/render
format, and Atlas owns causal interpretation. A rendered pattern is not proof
of which generator or listener produced it.

## Capture

Choose a bounded populated window large enough to show the feature without
creating an unnecessarily large world. The micro-region profile uses a
16-by-16 chunk window and emits an overview plus exact shards.

```bash
python3 modules/crucible/tools/run_strata_observation.py --help
```

Write the runtime, logs, packages, manifests, shards, and receipt below one
fresh `.workbench/evidence/strata/<label>/` root. The receipt must bind the
runtime mod set, requested region, completed scan, package hashes, toolchain,
and viewer handoff.

## Inspect

Validate the handoff before opening a renderer:

```bash
python3 modules/crucible/tools/serve_strata_observation.py \
  .workbench/evidence/strata/<label>/viewer-handoff.json \
  --check
```

Then run the same command without `--check` to start the local viewer. Use the
overview for landform, water, biome, and height patterns; open an exact shard
for block-level questions. Keep empty cells, absent chunks, and capture errors
distinct.

Pair the receipt with the structured World Studio log or a sealed Observatory
bundle when the question is causal. Matching coordinates alone do not join an
actor to final state.
