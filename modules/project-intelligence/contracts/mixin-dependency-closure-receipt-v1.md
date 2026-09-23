# Workbench Mixin dependency closure receipt V1

Status: experimental product-generic static-resolution contract

Contract ID:
`WORKBENCH-PROJECT-INTELLIGENCE-MIXIN-DEPENDENCY-CLOSURE-RECEIPT-V1`

Machine-readable artifact:
[Mixin dependency closure receipt schema V1](../schemas/mixin-dependency-closure-receipt-v1.schema.json).

## Purpose

This receipt supplements, but does not modify, the Mixin component topology
receipt V1. It binds exact archive bytes to caller-declared artifact roles and
directed dependency edges, retains each JVM class header's declared class,
superclass, and direct interfaces, and resolves requested class names only
through the declared runtime closure.

Closure `complete` means `caller-declared-complete`. It is suitable for a
terminal missing-class decision only when the caller has authority over the
dependency graph. It is not proof that Workbench independently discovered all
dependencies. An incomplete closure yields `unresolved`, never `missing`.

## Resolution

Runtime traversal follows only `required-runtime`, `optional-runtime`, and
`provided-runtime` edges. `compile-only` and `observation-only` artifacts stay
available as evidence but cannot provide runtime classes. Each provider records
the lexicographically smallest shortest artifact path from the requester.

Exactly one reachable, path-correct class header is `resolved`; none is
`missing` only in a complete closure and otherwise `unresolved`; more than one
is `ambiguous`. An adjacent artifact with no reachable declared edge is not a
provider. Multiple classpath providers remain ambiguous because this contract
does not claim runtime classloader order. Multi-release variants are likewise
separate providers until a later contract binds runtime selection policy.

## Exact identity and validation

Artifacts, edges, class headers, resolutions, and the whole receipt are
content-addressed with the prefixes declared in the schema. IDs hash canonical
JSON with only their own ID field omitted. Arrays are uniquely ID-sorted.

A semantic validator recomputes all IDs, set digests, class-resolution paths,
states, selected providers, references, and summary counts. It rejects duplicate
directed edges, duplicate request keys, unknown references, and reidentified
resolution tampering. Exact class entry and artifact byte hashes are produced
from input bytes; external custody must retain those bytes if later independent
rehashing is required.

The receipt does not prove class initialization, runtime loading, classloader
order, plugin decisions, connector output, or transformed class completion.
Candidate-lock V1 is not modified.
