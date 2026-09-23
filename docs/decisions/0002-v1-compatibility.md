# Decision 0002: preserve retained identities, not legacy systems

Status: accepted
Date: 2026-07-30
Amended: 2026-08-28

## Decision

Workbench is in its first-deliverable phase. Legacy readers, routes, writers,
workflows, storage layouts, and implementations are not retained by default.
They remain executable only when a named current deliverable, active consumer,
historical-inspection requirement, or tested rollback boundary requires them.
An audited unused legacy system may be removed directly instead of receiving a
compatibility adapter or side-by-side migration.

When Workbench retains an identity-bearing V1 artifact as evidence, a proof
input, or a historical fixture, its exact bytes, `susy-*` format, schema
identifier, hash, scope, and historical meaning remain verbatim. Retention of
that artifact does not imply retention of every reader or system that once
produced or consumed it. New semantics use an explicit successor contract;
they never repair or reinterpret retained V1 bytes in place.

Each legacy surface selected by current work receives exactly one recorded
disposition:

1. `retained-evidence`: keep the exact identity-bearing artifact and the
   canonicalization or validation needed by its named proof; no product reader
   or route is implied.
2. `supported-compatibility`: a named current consumer or rollback boundary
   requires the old behavior; keep the executable system and apply its declared
   parity, custody, cutover, and rollback gates.
3. `retired-unselected`: no current consumer, deliverable commitment, rollback
   need, or unique evidence obligation selects the system; record the inventory
   and remove it without manufacturing equivalence.

## Reason

Renaming a format, path, or normalized payload in place would change digests
and make existing proof unverifiable. A clean repository boundary is not
permission to rewrite historical meaning.

Conversely, preserving every imported compatibility system before Workbench
has a shipped dependency base would create duplicate authorities, code paths,
rollback obligations, and maintenance cost for hypothetical consumers. The
first deliverable should carry only the legacy surface that has an explicit
present requirement.

## Consequences

- There is no suite-wide requirement to keep V1 readers or legacy routes
  available. This supersedes the original universal compatibility-reader
  consequence of this decision.
- Each retained legacy system names its current consumer or rollback purpose;
  absence of one is grounds for removal.
- A replacement for an active legacy workflow still requires the parity,
  custody, and rollback evidence declared by that workflow. An unused route
  may instead close through an audited no-consumer removal disposition.
- Retained identity-bearing artifacts remain byte-exact, but an executable
  compatibility reader exists only when its named requirement needs one.
- New profiles distinguish retained historical evidence from current
  admission.
- The old namespace does not imply that generic Workbench modules are
  Supersymmetry-only.
