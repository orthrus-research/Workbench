# Atlas target-parameterized query contract v1

Status: active M1 contract

This contract separates an Atlas question definition from one exact request to
apply that definition. A question defines audience meaning and required answer
sections. A query instance binds that meaning to one exact typed selector,
snapshot, profile/side scope, traversal policy, and authority expectation.

Contract artifacts:

- [Query-instance schema v1](../schemas/atlas-query-instance-v1.schema.json)
- [Capability-policy schema v1](../schemas/atlas-capability-policy-v1.schema.json)
- [Query-result schema v1](../schemas/atlas-query-result-v1.schema.json)
- [Capability policy v1](../data/atlas-capabilities-v1.json)
- [Machine-checked examples v1](../examples/atlas-query-contract-examples-v1.json)
- [Atlas V1 question definitions](../data/acceptance-questions-v1.json)

## Exact request identity

`query_instance_id` is
`atlas-query:sha256:<lowercase-hex>`. The digest is SHA-256 over canonical JSON
bytes for every query-instance field except `query_instance_id`. Canonical JSON
uses sorted object keys, no insignificant whitespace, UTF-8, and no trailing
newline. Any selector, scope, traversal, authority, question hash, snapshot,
policy ID, or policy digest change creates a different request identity.

`question_definition_sha256` is the SHA-256 digest of the selected complete
question-definition object under the same canonical JSON rule. The bridge must
load exactly one matching `question_id` and reject a query instance if the
definition digest differs. A stable question ID therefore cannot silently
change meaning beneath a stored request.

`capability_policy_id` and `capability_policy_sha256` bind the request to the
complete capability-policy object under the same canonical JSON rule. The
bridge rejects a request when either field differs from the loaded policy.
A stable capability-policy ID therefore cannot silently change selector,
section, or authority semantics beneath an existing request identity.

Scopes are explicit, nonempty, unique profile/physical-side pairs. A selector
never carries a hidden scope. Traversal options are fully materialized in v1 so
no process limit depends on a caller, language, or CLI default after request
identity is established.

Required and optional authority arrays are unique, sorted, and disjoint.
Required authorities must include every evidence basis required by the question
definition. `unresolved` is a result state and is never an authority
requirement.

## Exact selector admission

Display text, translated names, shortest identifiers, database order, and
namespace guesses are not selector key families. The public kind maps to one
normalized runtime kind:

| Public kind | Runtime kind | Admitted exact key families |
| --- | --- | --- |
| `material` | `material` | `material-resource-location`, `material-numeric-id`, `runtime-node-id` |
| `item` | `item` | `item-resource-location`, `runtime-node-id` |
| `item-variant` | `item_variant` | `item-variant-sha256`, `item-variant-identity`, `item-variant-resource-location`, `runtime-node-id` |
| `fluid` | `fluid` | `fluid-name`, `fluid-default-registration-name`, `fluid-resource-location`, `runtime-node-id` |
| `fluid-variant` | `fluid_variant` | `fluid-variant-sha256`, `fluid-variant-identity`, `fluid-variant-name`, `runtime-node-id` |
| `ore-dictionary` | `ore_dictionary_key` | `ore-dictionary-name`, `runtime-node-id` |
| `ore-prefix` | `ore_prefix` | `ore-prefix-name`, `runtime-node-id` |
| `recipe` | `recipe` | `recipe-semantic-sha256`, `recipe-native-identity`, `recipe-resource-location`, `runtime-node-id` |
| `recipe-map` | `recipe_map` | `recipe-map-name`, `runtime-node-id` |
| `machine` | `machine` | `machine-resource-location`, `machine-numeric-id`, `runtime-node-id` |
| `recipe-rule` | `recipe_rule` | `dynamic-rule-identity`, `runtime-node-id` |
| `process-rule` | `process_rule` | `procedural-rule-identity`, `runtime-node-id` |
| `worldgen-deposit` | `worldgen_deposit` | `runtime-node-id` |

The query schema recognizes `quest` and generic `entity` spellings for
fail-closed interoperability, but capability policy v1 does not admit them.
They return `unsupported-kind` until a versioned resolver and authority policy
exist.

Resolution operates independently in each requested scope:

- `resolved`: exactly one target in every scope;
- `resolved-with-profile-gaps`: exactly one target in at least one scope and
  no target in the remaining named scopes;
- `missing`: no target in any named scope;
- `ambiguous`: more than one candidate in any one scope; and
- `unsupported-kind`: the policy does not admit the public target kind.

Every candidate remains deterministically ordered in an ambiguity response.
The resolver never selects one.

## Capability policy

The capability policy normalizes the target-kind by answer-section matrix.
Question definitions remain authoritative for which sections a question
requires. Metadata sections are always emitted by the result envelope; every
other required section must have exactly one policy row.

Per-section status means:

- `supported`: the target resolved, policy admits the kind/section pair, and
  every section-required authority is available;
- `not-applicable`: positive versioned policy excludes the kind/section pair;
- `unavailable`: the pair is applicable and the target resolved, but one or
  more required authority inputs are unavailable; and
- `unresolved`: the pair is applicable but identity, ambiguity, policy, link,
  or semantic closure prevents a safe result.

Absence in captured data never produces `not-applicable`. `unavailable` does
not assert that a fact is false. `unresolved` does not choose among candidates.
Each capability row names the question, target kind, available and missing
authorities, reason code, and evidence IDs used to establish its status.

Authority availability is separate from capability status:

- `available`: exact in-scope authority evidence exists;
- `not-requested`: the query requests neither required nor optional use;
- `not-applicable`: positive policy excludes the authority for this section;
- `not-observed`: the authorized evidence surface was inspected and contained
  no matching observation, without a closed-world negative claim;
- `unavailable`: a requested input or authority artifact is absent;
- `ambiguous`: authority records do not close to one exact target; and
- `unresolved`: records exist but their semantic or evidence closure fails.

All six authority bases appear once in canonical order in a query result.
Every authority evidence ID resolves exactly once in the top-level `evidence`
array. Each binding carries its authority, record kind, complete immutable
record, and a recomputable SHA-256 digest. A composed answer must contain the
same record for every projected binding. Optional absence remains visible but
does not by itself downgrade an otherwise supported section. `runtime` is
envelope metadata rather than a capability row; exact runtime identity remains
the required authority behind the universal `target` capability.

## Aggregate result status

Aggregate status is derived, never display-text selected. An unknown or
inactive question, question-digest mismatch, or policy-digest mismatch rejects
the query instance before any result envelope is produced:

1. an unsupported target kind yields `unsupported-query`;
2. ambiguous target resolution yields `ambiguous`;
3. missing target resolution yields `unresolved-identity`;
4. all substantive required sections `not-applicable` yields
   `not-applicable`;
5. any applicable required section `unavailable` or `unresolved` yields
   `unresolved-evidence`;
6. a successful preflight without composition yields `resolved`;
7. a composed answer retains `answered`, `answered-with-profile-gap`, or
   `no-mechanical-route` from the validated answer semantics.

`answer` is null for preflight and fail-closed results. A non-null answer must
validate against the Atlas V1 answer contract and the query-result semantic
validator. Its question, selector, snapshot, scopes, evidence, and status must
agree with the query instance and capability rows.

## V1 compatibility

The existing `corpus_bridge.py answer` command and `build_answer` API remain
valid as the V1 entry point. The parameterized `query` command adapts a V1
question to the same composition semantics by:

1. loads the V1 question and its embedded selector;
2. maps only a declared primary key family;
3. materializes the existing route, recycling, and construction defaults;
4. copies the question-required evidence bases to required authorities;
5. records all other authority bases as optional;
6. computes the question and query-instance hashes; and
7. invoking the shared answer composer after parameterized preflight.

The compatibility `answer` command continues to emit the existing
`susy-unified-process-atlas-answer-v1` object byte-for-byte for accepted V1
fixtures. The nested answer produced by the parameterized command is held to
that same byte-level behavior for V1-adapted requests. The new command requires
an explicit complete query instance and never falls back to the question’s
embedded selector. Runtime-only targets may resolve even when optional catalog,
corpus-link, quest, presentation, or placed-world authorities are absent.

## Example fixture semantics

The examples file contains one complete base query, shallow top-level
replacements, and expected result cases. A contract test materializes each
query case, recomputes its question digest and query-instance ID, and validates
it against the schema. The display-text selector case must fail. Result cases
cover exact, missing, ambiguous, and required-authority-unavailable states and
are checked against both the result schema and the status invariants above.
