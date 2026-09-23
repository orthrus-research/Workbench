# Crucible Stage-Bound Registry and Effect Snapshot V1

This contract seals registry observations and observed registration effects at
one exact lifecycle stage. Every record carries the same stage binding,
Crucible provenance, an `runtime-observed` evidence state, and a content
identity.

The snapshot does not contain source declarations and does not decide whether
anything is playable. Pack Program Studio owns static source declarations;
Atlas can link this immutable observation into SOURCE/RUNTIME/PLAYABLE
projections and derive evidence-bounded conclusions.

The stage boundary prevents a final registry state from erasing repeated or
failed effects that occurred during registration. This is required for checks
such as duplicate material fluid-state registration.

Schema: `../schemas/stage-bound-registry-effect-snapshot-v1.schema.json`.
