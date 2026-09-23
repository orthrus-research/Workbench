# Captured recipe dead-end audit V1

`atlas recipes audit-dead-ends GRAPH` audits every `gt-recipe` in one admitted
categorical V2 graph. It has no default depth, recipe or result limit. The caller
selects the capture; Atlas neither launches Minecraft nor selects a pack release.
Source checkouts and initialization-observation V3 graphs are refused.

## API and exports

```python
from workbench_atlas_recipe_health import audit_recipe_dead_ends, open_recipe_health

with open_recipe_health(graph_path, check_cancelled=check_cancelled) as view:
    report = audit_recipe_dead_ends(view, check_cancelled=check_cancelled)
```

The callback is optional. Cancellation propagates without a successful report;
the caller owns the view. `--json` returns the complete audit. `--csv` returns one
row per recipe, with graph identity, exact recipe ID, lookup activity, maps,
findings, upstream/downstream statuses, input/output details and cycle IDs.
Nested fields are JSON encoded in CSV cells. JSON and CSV are mutually exclusive.
The default human display is explicitly a summary, never a truncated audit.

The record format is `workbench-atlas-recipe-dead-ends-v1`, schema version 1.
`context` identifies the original graph. `coverage` describes the finite scan and
unsupported domains. `recipes` includes every captured recipe, including inactive
and unknown lookup states. `resources` supplies shared producer/use references;
`cycles` supplies structural cycle witnesses; `summary` reconciles recipe states
and finding counts. Counts for findings overlap: a recipe may have several.
`policy` records the versioned analysis policy and SHA-256 of its evaluator
implementation. CSV also carries both values. This identifies the evaluator,
not a full installed dependency/environment manifest.

Every compact selection, selector, map and relationship reference belongs to the
same graph. Open it in Atlas to inspect the original values and evidence. The
report does not copy every canonical record into every finding. Ordering is
deterministic. Local context paths can differ after relocation; semantic finding
identities and graph references remain bound to the same capture.

## Meaning of findings

- **Missing producer candidate:** a required selector has no observed active
  producer for any accepted alternative in its completely described finite
  matching domain. Other acquisition remains unknown.
- **No output use candidate:** every captured output lacks an observed active
  recipe use. This does not establish that the product is useless.
- **Stranded output candidate:** one or more outputs lack such a use. A co-product
  with no use does not make all outputs dead ends.
- **Both sides candidate:** missing producer and no-output-use findings coexist.
- **Structural cycle:** active recipe, selector and accepted-resource edges form
  a cycle. Membership, exact witness edges and boundary links remain evidence;
  player seed supply and cycle viability remain unknown.

A recipe requires all its input slots, but an input may accept alternatives. One
observed producer for an accepted alternative prevents a local missing-producer
finding for that slot. Slots remain separate even when they refer to the same
resource. Reusable requirements count as uses and are distinguished from consumed
inputs. Chance output metadata is retained; an observed producer is not a promise
of a guaranteed yield. Inactive recipes are not active producers or consumers;
unknown lookup activity is not converted to active or absent.

Only qualified acceptance edges establish matching links. Observation-only
representatives are retained separately. Incomplete matching, missing input
inventory, unknown activity and other relevant gaps remain visible. Captured
input counts, where supplied, must agree with selector occurrences.

## Coverage and developer-local use

This first audit covers the captured finite GT model. Crafting, smelting, other
mod recipe families, external supplies (mining, loot, trade), terminal uses
(equipment, building, quests), machine execution, transitive supply viability,
batch quantities and chance feasibility are unassessed or unsupported. A zero
finding count is not a clean-pack certificate. A closed observed cycle is not
proof that no external seed exists.

Capture preparation, selected saved source, dependency/JVM admission and runtime
execution belong to Core and the explicit profiles/evidence producers. Developers
can audit their own supported retained graph through this installed command.
An older graph is historical evidence; it is not evidence of the current checkout.
The profile's developer-selected capture-input contract is a separate input
binding, not an installed capture runner or admission of arbitrary mod versions.

Atlas keeps its independent API/Crucible installation boundary. Shell's catalog
exposes the command without acquiring ownership of findings. Source navigation,
Axiom explanations, additional acquisition/use adapters and comparison workflows
must preserve their own scope and exact identity boundaries.
