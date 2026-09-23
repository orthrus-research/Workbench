# Supersymmetry world generation profile

This directory contains the pack-specific inputs and bounded case studies used
by Workbench's world-generation tools. Markdown in this directory is context;
executable behavior is defined by the source, schemas, and tests linked below.

## Current authorities

- World Studio's active experimental implementation, build instructions,
  supported controls, and current limits are in the
  [Cleanroom prototype fixture](../../../platforms/cleanroom/candidates/0.6.8-alpha/worldgen-prototype-fixture/README.md).
  The pack-owned iteration, cockpit, and qualification JSON files in this
  directory select that fixture; its source and tests define the behavior.
- GTCEu world-generation inventory and overlay formats are defined by the
  [inventory contract](../../../../modules/crucible/contracts/gtceu-worldgen-inventory-v1.md),
  [impact contract](../../../../modules/crucible/contracts/gtceu-worldgen-impact-inventory-v2.md),
  and their schemas under
  [`modules/crucible/schemas`](../../../../modules/crucible/schemas/).
- Strata micro-region receipts are defined by the
  [receipt contract](../../../../modules/crucible/contracts/strata-micro-region-receipt-v1.md)
  and
  [schema](../../../../modules/crucible/schemas/strata-micro-region-receipt-v1.schema.json).

The Biomes O' Plenty and Recurrent Complex notes retained here are bounded
pack/runtime case studies. They do not override these executable contracts or
grant compatibility or release approval.
