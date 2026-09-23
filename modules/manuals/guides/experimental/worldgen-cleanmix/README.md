# Experimental Worldgen and CleanMix guides

Status: experimental working guides

These guides document the current local workflows for World Studio, the
Worldgen Observatory, Strata, GTCEu worldgen, and CleanMix diagnostics. They
are editable field guides, not generated Manuals V1 artifacts.

## Authority boundary

Manuals owns the explanation only. The Cleanroom profile owns platform facts,
Crucible owns runtime capture and evidence custody, Atlas owns bounded causal
interpretation, and Blueprints owns construction from registered patterns.
Nothing in these pages approves an implementation or establishes release
readiness.

Worlds, logs, captures, receipts, class dumps, and rendered packages remain
under ignored `.workbench/` storage.

## Guides

1. [Run one World Studio iteration](run-worldgen-iteration.md) for the normal
   edit-build-fresh-world-observe loop.
2. [Iterate the first playable replacement](first-playable-worldgen.md) when
   changing or debugging the underlying fixture directly.
3. [Tune mega-regions and hydrology](iterate-mega-regions-hydrology.md) for
   plan controls, cache bounds, comparison, and profiling.
4. [Observe an exact Cleanroom run](observe-exact-cleanroom-worldgen-run.md)
   when a question needs a closed capture rather than development logs.
5. [Inspect World Studio output with Strata](observe-world-studio-with-strata.md)
   for bounded final chunk state and local visualization.
6. [Inspect and stage GTCEu worldgen](inspect-gtceu-worldgen.md) for definition,
   runtime-impact, and overlay work.
7. [Read an Atlas listener-to-write chain](read-atlas-listener-write-chain.md)
   without extending the answer beyond its captured scope.
8. [Localize a CleanMix failure](localize-cleanmix-failure.md) across static,
   discovery, configuration, application, and final-byte boundaries.
9. [Prototype a total replacement](prototype-total-replacement.md) when a
   deeper generator-ownership comparison is warranted.

Use these status terms consistently:

- **Observed**: present in the exact cited capture.
- **Expected**: required by a contract or test oracle, but not necessarily
  observed.
- **Unavailable**: the required observation surface was absent.
- **Incomplete**: the process did not reach its required terminal seal.
- **Failed**: a required check returned a negative result.

A lower-stage observation never proves a later stage. Preserve a failed or
incomplete run and start a new capture after correcting its cause.
