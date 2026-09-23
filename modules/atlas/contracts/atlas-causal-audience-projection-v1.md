# Atlas causal audience projection v1

Status: implemented

This package derives one developer explanation and one player explanation from
the same independently validated P03/C01 causal result. The C01 result is
embedded unchanged, so the projection can be recomputed without consulting
N01, source indexes, runtime databases, curated prose, or another authority.

Artifacts:

- [audience projection schema v1](../schemas/atlas-causal-audience-projection-v1.schema.json);
- [reviewed closed projection v1](../examples/atlas-causal-audience-projection-example-v1.json);
- [P03 causal query handoff](atlas-causal-query-v1.md);
- [`atlas_causal_projection.py`](../src/workbench_atlas/atlas_causal_projection.py);
- [`test_atlas_causal_projection.py`](../tests/test_atlas_causal_projection.py); and
- the exact admission helper `corpus_bridge.causal_provenance_projection`.

The reviewed example is a production-shaped synthetic contract fixture. It is
not accepted production evidence or a claim about a real diluted-oil recipe.

## One mechanical basis

`project(result)` first validates the complete C01 result. Both audience views
then bind the same:

- result, request, query-instance, snapshot, scope, policy, and final-runtime
  identities;
- path IDs, states, order, and open reasons;
- relation IDs, direction, predicate, causal strength, lifecycle stage, and
  exact endpoint IDs;
- evidence IDs, negative statements, bounded wording, frontiers, and
  truncation; and
- final runtime node identity.

The projection embeds `source_result` byte-for-byte and is itself
content-addressed. `validate_projection` validates the embedded C01 result,
rebuilds both audiences from it, and requires exact document equality.

## Developer view

The developer view retains every path node with its complete C01 identity,
scope, evidence, and position. Each transition is content-addressed and keeps
the exact predicate, strength, lifecycle stage, subject, object, evidence, and
position. It also retains the exact C01 negative statements and closure
limitations.

This is a structured explanation, not a replacement graph. Consumers that need
source bytes follow the retained `source-span` identity and pinned-source
evidence back to the locked repository/revision/tree/path/byte interval.

## Player view

The player view preserves every path and transition in order while attaching a
stable bounded sentence to each C01 predicate. It keeps action codes, strengths,
lifecycle stages, endpoint IDs, evidence IDs, path states, open reasons,
negative statements, and bounds even when a renderer chooses to hide some of
those details.

The fixed wording distinguishes declaration, registration, configuration,
invocation, copying, transformation, mutation, removal, replacement, identity
reconciliation, final observation, and presentation derivation. A player
renderer cannot turn possibility into causation, omit a removal/replacement,
hide truncation, or convert an open result to an answer.

The five summary states are also fixed:

- `causal-path-closed`;
- `causal-path-partial`;
- `causal-path-bounded`;
- `causal-path-unresolved`; and
- `causal-evidence-unavailable`.

## Atlas bridge boundary

`corpus_bridge.causal_provenance_projection` admits a validated projection only
when its snapshot equals the Atlas answer snapshot, its primary
profile/physical side was requested, and its final runtime node ID is one of
the exact resolved Atlas targets.

B01 intentionally does not add a new active acceptance question or silently
attach causal data to every existing answer. V01 must supply an accepted P03
result for an exact resolved target before the bridge can expose this field.
Absent, cross-snapshot, cross-scope, invalid, or wrong-target causal results
fail closed.

## Validation

Focused tests cover:

- closed, partial, bounded, unresolved, and unavailable projections;
- exact path/node/relation/evidence parity across audiences;
- fixed wording coverage for every C01 predicate;
- omitted and reordered transitions;
- strengthened causal claims and rewritten player wording;
- scope drift and hidden truncation;
- evidence padding and altered negative wording;
- exact bridge snapshot/scope/target admission;
- byte-for-byte repeatability; and
- reviewed example reproduction.

The reviewed projection ID is
`atlas-causal-audience-projection:sha256:e2970872551193c3e88fe6fa5e523ddb0ba44deaee1e3f75444871c19fc0a6ca`.
No game execution, runtime capture, world write, external evidence mutation,
or production causal closure occurred.

## Atlas bridge consumer boundary

The Atlas bridge may apply only to a causal result whose final record is exactly
resolved in the same Atlas snapshot and scope. It must test both audiences and
retain any partial, bounded, unresolved, or unavailable outcome. Reviewed
synthetic closure proves the machinery; it cannot substitute for a production
final-record capture and operation-bound transition evidence.
