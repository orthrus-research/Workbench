# Read an Atlas listener-to-write chain

Status: experimental working guide

Use this to interpret one Worldgen Observatory answer without extending it
beyond the sealed capture.

## Authority boundary

Crucible owns the captured records and their closure. Atlas owns the bounded
query result. Manuals explains how to read it; it does not turn one occurrence
into a general compatibility claim.

## Procedure

1. Check `status` before reading a path. `unavailable`, `ambiguous`,
   `incomplete-evidence`, and `invalid-evidence` are outcomes, not empty
   successes.
2. Confirm the capture, bundle, question, subject, dimension, chunk, stage,
   and query bounds.
3. Follow `evidence_ordinals` in order from outer lifecycle event to listener,
   decision, logical write, and terminal storage actor.
4. Keep the logical initiator separate from the method that physically wrote
   the block.
5. Read every limitation and frontier before writing a conclusion.

```bash
python3 modules/atlas/tools/query_worldgen_observatory.py \
  --bundle /path/to/canonical-bundle.json \
  --query /path/to/query.json \
  --output .workbench/evidence/<case>/atlas-answer.json
```

A useful conclusion names the exact observed route and scope. It does not say
that the actor always runs, that no other actor can write the block, or that
the result is compatible outside the captured version and profile.

Retain the answer beside its source bundle and query. Copying only a prose
summary discards the evidence binding needed for later review.
