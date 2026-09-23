# Exact Runtime Explorer

Exact Runtime Explorer is Workbench's read-only search surface across project
declarations, exact artifacts, Atlas knowledge, controlled Crucible records,
retained output, and Manuals links. It preserves each source's authority and
never treats name similarity as runtime proof.

## Use it

```bash
workbench explore example:machine
workbench explore class:example.ExampleMod --details
workbench explore 'at example.ExampleMod.register(ExampleMod.java:42)' \
  --runtime-db .workbench/cache/runtime-graph.sqlite \
  --artifact .workbench/artifacts/example.jar \
  --receipt .workbench/evidence/transformation-ledger.json \
  --sources
workbench explore
```

The final form opens the terminal omnibox. The live console exposes the same
single-query operation as `explorer.search`.

Use `--json` for the complete content-addressed V1 result, `--details` for
facets and relationships, `--sources` for coverage, and `--require-observed`
when automation must reject static-only answers.

## Evidence boundary

- Workspace and archive facets are exact static declarations or
  possibilities; files and classes are never executed.
- Atlas facets are accepted observed or derived assembled-game knowledge from
  an explicit immutable projection.
- Crucible facets are validated controlled occurrences with their capture and
  coverage limits; they do not become Atlas interpretation.
- Pack Program diagnostics are exact for the checked source and endpoint but
  do not prove script execution or registry state.
- Logs and console sessions are presentation evidence, not causality.
- Manuals links are teaching, not authorization.

Facets join only through declared strong identities. Conflicting owners,
versions, profiles, sides, or exact nodes remain visible ambiguity. Missing
runtime evidence becomes an explicit state rather than an empty success.

Typed query prefixes cover code, JVM members, Mixins, registries, resources,
recipes, blocks, items, world domains, configurations, events, capabilities,
coordinates, and source locations. Filters such as `kind:`, `owner:`,
`state:`, `profile:`, `side:`, and `source:` narrow the declared identity
domain.

## Embedded graph query

The graph-native V2 presenter is an embedded-library route over one registered
Crucible `graph/query`. It returns the canonical owner-result bytes and may
render a validated, sanitized terminal view without converting the result into
Explorer V1 facets. Arbitrary endpoints and saved RPC output carry no
presenter authority.

See the [graph-native presentation
contract](contracts/graph-native-presentation-v2.md).

## Safety and contracts

Explorer rejects unsafe or replaced files, malformed receipts, unsafe archive
members, live database journals, and inputs above declared file, byte, source,
or record limits. It opens Atlas databases query-only and does not mutate
`.workbench`, launch Minecraft, download sources, or define archive classes.
Terminal output strips rendering controls; JSON preserves escaped structured
values.

The serialized surface is defined by the [V1
contract](contracts/exact-runtime-explorer-v1.md) and [result
schema](schemas/workbench-exact-runtime-explorer-result-v1.schema.json).
Architecture and nonclaims are summarized in [Exact Runtime Explorer
V1](../../docs/architecture/EXACT-RUNTIME-EXPLORER.md).
