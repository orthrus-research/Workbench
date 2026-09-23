# Impact Studios

Machine, Asset, and Evolution Studio are read-only inspection flows. They make
existing evidence easier to inspect without creating a second authority or
claiming that source files prove runtime behavior.

## Machine Studio

```text
workbench machine inspect example:machine \
  --runtime-db /path/to/runtime-graph.sqlite
```

Machine Studio asks Atlas for one exact machine and its bounded relationships
and recipes. Exact Runtime Explorer supplies identity grouping, ownership, and
source navigation. The human view separately reports:

- the machine registered in the selected runtime graph;
- static form, constraint, energy, capability, and recipe-map rows that are
  actually present; and
- formed-world proof as `not-supplied`.

A registered controller, structure-shaped relationship, or lookup-active
recipe does not prove that a multiblock formed, operated, or executed that
recipe in a world. Use `--profile` and `--side` when the same identity exists
in more than one Atlas scope, and `--key-kind runtime-node-id` when supplying
an Atlas canonical node ID.

## Asset Studio

```text
workbench assets check /path/to/project --identity example:machine
workbench assets check /path/to/project \
  --identity example:machine \
  --translation-key tile.example.machine.name
```

Asset Studio discovers source asset roots under the explicitly selected
workspace and checks local blockstate-to-model, model-parent, model-to-model,
texture, and texture-variable references. It also inventories `.lang` and
locale JSON files. An exact `--translation-key` turns localization into a
required check; conventionally inferred keys remain labeled as candidates.

Missing local mod assets, duplicate logical resources, malformed documents,
and cycles produce `attention` and exit 1. A dependency in the `minecraft`
namespace that was not supplied by the workspace remains an external unknown,
not an invented local failure. The check never claims that an asset was
registered, selected, rendered, or visible in a running client.

## Evolution Studio

```text
workbench evolution recipes BEFORE_GRAPH AFTER_GRAPH --json
```

Evolution Studio is a discoverable route to the existing Atlas runtime recipe
comparison. It translates only the command name. Atlas still owns the JSON
format, compatibility decision, bounds, evidence gaps, human rendering, and
exit status. In particular, added and removed immutable recipe signatures are
not paired into inferred edits or migrations, and bounded progression exposure
is not proof that gameplay is broken or unreachable.

All three routes are experimental. They are useful local inspection flows, not
stable-support, release, save-compatibility, or runtime-execution claims.
