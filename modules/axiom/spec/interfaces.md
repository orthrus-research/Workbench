# Evaluation interfaces

Status: long-term interface design. The [first-slice protocol](first-slice.md)
implements only explicit-program check, ordinary MIXER query and coverage.
The additional [target interface](source-targets.md) verifies source packages,
candidate overlays and loader/syntax inventory without executing definitions.

## Operations

| Operation | Inputs | Result |
| --- | --- | --- |
| Check source | Exact target baseline, candidate source overlays, construction configuration, declared scope | Construction/registration diagnostics, generated lineage, machine compatibility and outstanding conditions |
| Query machine | Admitted target, effective definitions, concrete machine identity/state, inventories, environment and query kind | Matching/selection/gate decisions, transformed recipe, start admission and ordered effects |
| Simulate processing | Query state plus explicit logical clock, ordered events, random choices/state and bounds | Processing/interruption/completion transitions and output disposition |
| Inspect coverage | Target and optional producer/machine/rule scope | Discovered, source-traced, implemented and qualified coverage with gaps |

A source check may request machine compatibility without inventing a live machine
state. A query must distinguish testing a named recipe from asking which recipe
the machine would select, and distinguish fresh lookup from cached-machine
behavior.

Standalone and Workbench commands now expose `axiom check`, `axiom query`,
`axiom coverage`, `axiom target`, `axiom platform` and `axiom material-program`.
The material-program operation executes complete saved source with explicit
native context and pending qualification; see [its contract](material-program-preflight.md).
The platform operation inspects explicit
[platform/library inputs](platform-inputs.md), not an executable runtime.
`axiom simulate` remains unimplemented and
is not registered.

## Request envelope

The future protocol must identify operation/schema version, target source and
dependency fingerprints, input source/registry identity, requested scope,
concrete machine state where applicable, environment assumptions, resource
limits and cancellation policy.

Source overlays include exact bytes or content-addressed references and deletions.
Resolve relative paths within an explicit input root; reject path traversal,
unexpected symlinks and ambiguous roots. An absolute path alone is not source
identity. Arbitrary callbacks cannot be hidden inside an allegedly data-only
request.

Duplicate keys, malformed typed values and unsupported protocol versions are
request errors. A recipe rejection is not a transport or engine crash.

## Result envelope

Return the target and candidate identities, operation, coverage, source locations,
stage decisions, prerequisite facts, effective recipe identity/lineage, original
and resulting machine state identity, allocations, transformations and ordered
effects.

Each stage distinguishes accepted/rejected, requires-context, unsupported and
not-evaluated. Evaluation failure, timeout or cancellation is a separate
completion status. No aggregate success flag may conceal missing rule coverage.

Diagnostic records need stable rule codes, human-readable explanation, source
span, upstream rule reference, relevant values, assumptions and whether the
finding is native rejection, request error or developer-intent warning.

Output recovery must disclose discarded fractions rather than equating a
successful handler fill with conservation. Compatibility claims name their
witness configuration and bounds; search exhaustion is not universal rejection.

For randomness, preserve supplied decisions/state and distinguish exact
conditional outcomes from distributions or incomplete branches. Results must be
reproducible without access to the original UI session.

## Workbench and IDE behavior

Developer edits stay in their IDE. Partial syntax/type diagnostics may appear
before semantic evaluation, with clear status. Expensive evaluation can run as
a bounded worker; current source identity must be checked before attaching its
result to an editor buffer.

Workbench supplies process/resource policy and the selected profile. The engine
produces domain diagnostics; clients must not infer unsupported validity or
calculate competing matches. Source navigation follows declaration and generated
lineage rather than relying on display recipe order or unstable runtime IDs.

Raw Groovy/Java, credentials or private workspace paths are not uploaded by
default. Standalone evaluation is local; networking and subprocess use by recipe
code remain denied unless explicitly supported by a separate policy.
