# Blueprints examples

The `*-v1.json` files in this directory are canonical fixtures for the
identity-bearing V1 contracts. They remain byte-stable and are not runnable
first-release examples.

Mutable developer-facing examples live with their selected pack profile. They
deliberately do not carry their own content identity or confer authority. A
current example records a useful request and the narrow result that was
actually observed; its IDs are references to retained evidence, not a bundled
proof.

The current Supersymmetry examples are:

- [`material-fluid-recipe-radon.json`](../../../profiles/packs/supersymmetry/blueprints/examples/material-fluid-recipe-radon.json),
  the collision-free request from the successful combined disposable client
  run;
- [`recipe-change-copper-sulfate-solution.json`](../../../profiles/packs/supersymmetry/blueprints/examples/recipe-change-copper-sulfate-solution.json),
  one exact MIXER recipe extracted from pull request 1451; and
- [`quest-for-process-gas-atomizer.json`](../../../profiles/packs/supersymmetry/blueprints/examples/quest-for-process-gas-atomizer.json),
  one exact prerequisite edge extracted from pull request 1978.

Only the radon example carries a retained current Cleanroom observation. The
recipe and quest examples are revision-bound source evidence for the new
transactional construction flows; their required client/server, BetterQuesting,
player-state, and retained-world observations remain open. Every target must
still recheck IDs, source anchors, existing recipe signatures, and quest-graph
conditions before applying a request.
