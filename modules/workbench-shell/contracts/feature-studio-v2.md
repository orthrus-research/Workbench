# Feature Studio embedded projection V2

Feature Studio is the IDE-neutral projection for the Materials & Recipes
workspace. It composes Project Intelligence, Blueprints, Atlas, selected
profile authority, and Crucible without replacing any of them.

## Operations

The current projection has five operations:

- `inspect` opens a revalidated source plan or one retained material-flow V2
  receipt;
- `plan` returns the exact owner plan and proposed source diffs;
- `verify` runs an exact reviewed plan in a fresh disposable runtime;
- `explain` presents owner-backed state, limitations, and safe next actions;
  and
- `export` writes an atomic patch bundle without applying it.

There is no placeholder impact operation. Recipe and dependency impact belong
to Atlas surfaces that operate on an explicit verified graph.

## Wire contracts

The closed schemas are:

- `feature-studio-request-v2.schema.json`;
- `feature-studio-result-v2.schema.json`; and
- `feature-studio-capabilities-v2.schema.json`.

All identity-bearing values use canonical JSON V2 and domain-separated content
IDs. The result preserves owner artifacts as canonical bytes where their owner
format supports canonical JSON V2. A retained owner file additionally carries
its URI, byte digest, and size. Formats outside that canonical domain remain
retained byte records; Feature Studio never coerces them.

## Review and mutation boundary

Direct calls are marked `direct-cli`. Catalog calls must carry the complete
catalog, action, review, and command binding for the exact operation. Both
paths bind the result to the flow-plan and source-snapshot identities.

`verify` and `export` require the reviewed plan ID and fresh source
revalidation. Verification delegates staging, runtime, observation, and
source-immutability decisions to the material-flow owner. Export rejects any
destination overlapping the source workspace and publishes by atomic rename.
Feature Studio has no source-apply operation.

The separately versioned
[snapshot V2](feature-studio-snapshot-v2.md) removes embedded owner JSON from
the native-client payload while retaining exact source, diff, assertion,
action, and receipt links.
