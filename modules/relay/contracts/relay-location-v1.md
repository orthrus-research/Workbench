# Relay location V1

## Outcome

`workbench relay locate IDENTITY` carries one exact identity from an explicit
retained input to an owner-backed Atlas, source, archive, or receipt location.
The flow is read-only.

## Inputs

Exactly one input is required:

- `--explorer-result PATH`: a complete, identity-valid Exact Runtime Explorer
  V1 result;
- `--receipt PATH`: a validated Atlas or Crucible receipt supported by the
  Exact Runtime Explorer; or
- `--runtime-db PATH`: an immutable Atlas runtime-graph query database.

The ordinary identity syntax is one typed Explorer identity such as
`machine:example:press`. `--identity-kind KIND` supports an exact identity
kind without a public shorthand, for example a Crucible
`runtime-snapshot-id`.

## Resolution rule

Relay emits `resolved` only when:

1. the Explorer envelope and its content identity validate;
2. the result and relevant owner coverage are not truncated;
3. exactly one entity carries the requested case-sensitive kind and value;
4. the entity is not ambiguous;
5. an observed Atlas runtime-graph or Crucible facet is present;
6. a non-unresolved owner binding is present; and
7. at least one retained navigation locator is present.

Any failed condition emits `unresolved`, a stable reason code, no resolved
location, and exit 1. Invalid input emits exit 2.

## Authority and context

Relay composes Exact Runtime Explorer providers and preserves the chosen
entity and relevant source rows verbatim in
`workbench-relay-location-v1`. Those rows retain profile, physical side,
session, snapshot, evidence state, owner, navigation, and limitations exactly
as supplied by Atlas, Crucible, Project Intelligence, or another admitted
Explorer source.

Relay does not copy Atlas data, infer ownership, refresh a stale recorded
path, assert that current source bytes equal retained runtime bytes, or
authorize a construction or release decision.
