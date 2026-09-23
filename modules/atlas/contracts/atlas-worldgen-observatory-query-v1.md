# Atlas Worldgen Observatory query contract v1

Status: experimental query surface over sealed Crucible evidence

Contract ID: `WORKBENCH-ATLAS-WORLDGEN-OBSERVATORY-QUERY-V1`

This is a new contract. It does not modify the identity-bearing Atlas V1
runtime-graph record schema or reinterpret an existing V1 identity.

Atlas admits a Worldgen Observatory capture only after Crucible validation has
verified the exact run binding, completion seal, component digests, balanced
spans, and declared coverage. An incomplete, corrupted, mismatched, or
lossless capture with dropped records cannot produce an answered query.

## Shared answer envelope

Every answer names the query, exact capture IDs, requested scope, evidence
record ordinals, status, and limitations. Status is one of:

- `answered`: the bounded evidence closes the requested occurrence;
- `no-match`: a complete declared scope contains no matching occurrence;
- `ambiguous`: matching records exist but exact attribution is not closed;
- `equal`: two admitted runs have no semantic difference in the compared
  scope;
- `diverged`: the first difference in canonical comparison order is retained;
- `unavailable`: capture coverage does not contain the requested domain;
- `incomplete-evidence`: a capture lacks a valid completion seal; or
- `invalid-evidence`: integrity, binding, span, or coverage validation failed.

Only `answered`, `no-match`, `equal`, and `diverged` are evidence-closed
results. `no-match` requires a machine-readable selector manifest that closes
the queried domain; an opaque capture-plan digest is not enough. The initial
V1 implementation therefore returns `unavailable` for an absent block-write
record rather than inventing selector closure.

Invalid input has no trusted capture ID. Its answer uses a local
`crucible-worldgen-invalid:sha256:` submission digest so attacker-controlled
seal text cannot become an audit identity.

## Who wrote this block?

Input identifies one capture, dimension, and exact block position. Atlas
returns all matching logical write chains in observed order, including before
and after semantic block states, terminal outcome, write channel, generating
and target chunks, active phase, event/listener span, cooperative decision and
RNG stream when observed, and actor binding. Every returned causal-path step
retains the actor from its exact span-enter record, and that ordinal is
included in the answer's evidence set; consumers therefore do not need to
infer a listener identity from a generic operation name.

Nested `World` and `Chunk` observations with the same `write_chain_id` form
one logical attempt. They are retained as channel observations but not counted
as independent writes. The last successful terminal mutation determines the
bounded final attempt. Its result reports the logical initiating API actor and
the low-level terminal storage actor separately; it does not rename vanilla
storage code as the initiating decorator. An ambiguous or unbound actor
remains so.

## Which handler changed this event?

Input identifies one capture and exact event-dispatch occurrence. Atlas
returns listener invocations in dispatch order. A handler changed the event
only when canonical before and after event state differs. Cancellation,
result, and typed mutable fields are compared separately, and thrown handlers
remain visible. Posting a mutable event proves neither that every mutation was
observed nor which listener caused it unless per-listener coverage is complete.
Listener enter and terminal records must retain one actor and listener span. A
nested or reentrant post belongs to its nearest post span and is not folded
into its parent dispatch.

## Where did these runs first diverge?

Input identifies two captures and one exact comparison scope. Runtime ordinals,
wall time, thread IDs, durations, and object identity are excluded. Records
are joined by their stable `comparison_key` and ordered by declared stage,
chunk, record-domain, and local occurrence ordinal.

Atlas uses checkpoint fingerprints to bind the comparable chunk/stage cohort.
Within each stage it orders named RNG observations, decisions, event
transitions, logical writes, and then the terminal checkpoint, so a causal
observation is not hidden by its later state digest. Checkpoints belonging to
another comparison-scope hash are excluded even when their chunk and stage
overlap. The result retains the last equal item, first differing item, and
whether the divergence is a changed value, missing-left occurrence,
missing-right occurrence, or coverage boundary. Equal means equal only for the
declared canonicalizer, domains, chunks, stages, and complete coverage.

## World Studio projection

World Studio may render these answers as a block-write history, listener tree,
or first-divergence timeline. It must preserve capture health, scope,
limitations, and unresolved actor bindings. It does not copy records into a
second authority or promote a query result into a Blueprint approval.
