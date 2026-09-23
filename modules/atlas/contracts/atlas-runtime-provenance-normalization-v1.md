# Atlas runtime provenance normalization v1

Status: implemented

This package normalizes the accepted P01 source-span index and P02 pack
mutation extraction into the C01 causal-provenance graph vocabulary without
promoting static occurrence to runtime execution. It supplies the exact
forward and reverse relation indexes consumed by the causal query.

Artifacts:

- [normalization schema v1](../schemas/atlas-provenance-normalization-v1.schema.json);
- [reviewed static-frontier fixture v1](../examples/atlas-provenance-normalization-example-v1.json);
- [`atlas_provenance_normalizer.py`](../src/workbench_atlas/atlas_provenance_normalizer.py); and
- [`test_atlas_provenance_normalizer.py`](../tests/test_atlas_provenance_normalizer.py).

The checked-in fixture is a production-shaped contract fixture, not accepted
production evidence. Large normalization outputs remain disposable.

## Input boundary

`normalize(source_index, extraction, runtime_bundle, pack_profile=...)` first
runs the complete P01 and P02 validators against locked source bytes through the
selected profile's `workbench.provenance_primitives` API-1 adapter. Library
composition can instead supply `primitive_adapter=...` explicitly. The adapter
must validate original inputs; a caller-supplied "validated" flag is not accepted.

The `static` CLI requires `--pack-profile supersymmetry` for the retained
Supersymmetry source/mutation formats, separately from `--profile`, which selects
the evidence audience. A missing or incompatible adapter refuses normalization.
The `check` CLI and `validate_normalization` consume retained artifacts without
requiring the producer's installed profile. Existing V1 record identities and
meanings are unchanged. The runtime bundle is
separately content-addressed and fixes:

- one or more exact snapshot/profile/physical-side scopes;
- accepted C01 script-operation, lifecycle-event, runtime-state, and
  final-runtime nodes;
- accepted C01 relations and immutable evidence records;
- explicit P02-operation-to-C01-node bindings; and
- explicit P02-configuration-to-`loads_configuration` relation bindings.

An empty runtime bundle is valid. It produces a static-only normalization with
honest frontiers and no execution or transition claims.

## Exact normalization rules

Every referenced P01 primitive is wrapped in a scoped C01 `source-span` node.
The primitive ID remains visible in `source_mappings`, but it is never reused
as a graph node ID. Its pinned-source evidence retains the source-index ID,
primitive ID, and complete revision/tree/path/symbol/line/byte/digest identity.

Each P02 selected configuration value becomes a scoped
`configuration-entry`. A `loads_configuration` relation is admitted only when
runtime-mechanics evidence binds the exact configuration-selection ID,
selected-value digest, scope, and matching Groovy lifecycle stage. A locked
`runConfig.json` value alone remains open at
`runtime-transition-unobserved`.

Each P02 operation remains an `operation_candidate`. Promotion to a C01
`script-operation` requires occurrence-specific stage evidence binding:

- the exact P02 operation ID, index, and operation digest;
- the exact scoped source-span node;
- snapshot, profile, and physical side;
- lifecycle stage and stage-execution identity; and
- the promoted C01 identity digest.

Promotion emits an exact source-to-operation `invokes` edge. It does not close
the runtime frontier. `registers`, `copies`, `transforms`, `mutates`,
`removes`, or `replaces` additionally require an operation-compatible
predicate, exact runtime endpoints, matching stage execution, and a
state-transition record bound to the operation and both endpoint node IDs.

Static exact targets stop at `runtime-transition-unobserved`. Symbolic targets
stop at `identity-reconciliation-unresolved`. Dynamic targets retain their
P02 unresolved-boundary ID and stop at `dynamic-script-unresolved`. Exact
runtime evidence may close any of these static analysis frontiers; target text
itself never does.

## Validator and indexes

`validate_normalization` independently recomputes:

- normalization, node, relation, evidence, candidate, and frontier IDs;
- source, configuration, operation, lifecycle, runtime, and capture bindings;
- scope and lifecycle applicability;
- strength-specific evidence kinds and exact endpoints;
- configuration and operation frontier closure;
- evidence use and summary counts; and
- minimal `forward` and `reverse` node-to-relation indexes.

Every non-reconciliation relation remains inside one exact scope.
`observed_as_final` must occur at `final-observation`. Evidence records must
use the same snapshot/profile/physical-side scope as the nodes and relations
they support. Unused evidence is rejected.

The indexes contain only nodes with outgoing or incoming normalized
relations. P03 may traverse them, but N01 does not search paths, paginate
closure, select a final request endpoint, or derive result status.

## Validation

Focused validation covers:

- static exact, symbolic, and dynamic frontiers;
- scoped primitive wrapping and multi-scope isolation;
- exact operation execution and registration endpoints;
- lifecycle and configuration occurrences;
- missing-transition retention;
- wrong profile, reversed final lifecycle, wrong operation transition,
  namespace/similarity non-resolution, evidence mutation, and index mutation;
  and
- byte-for-byte repeatability.

## Query consumer boundary

The causal query may consume:

- C01-valid normalized nodes, relations, and evidence;
- `indexes.forward` for causal expansion;
- `indexes.reverse` for final-to-source traversal;
- operation candidates and explicit frontiers for partial-path reporting; and
- the normalization, source-index, mutation-extraction, runtime-bundle, and
  policy identities.

The causal query remains responsible for request-bound traversal, global bounds, branch and
lifecycle ordering, path identity, closed/partial/bounded status, and final
C01 result validation. It must not reinterpret a static candidate or frontier
as an edge.
