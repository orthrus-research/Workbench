# Manuals

Manuals turns implementation knowledge into practical developer guides. It
should tell a developer what to change, where it belongs, why the step exists,
which variants are common, and what to try when it fails.

## Current capability

The retained V1 material provides:

- an implementation-guide semantic contract;
- eight requirement classes;
- a requirement policy;
- valid and invalid fixtures; and
- a focused contract-fixture semantic test.

There is no canonical generated Supersymmetry guide family yet.

An [experimental Worldgen and CleanMix field-guide set](guides/experimental/worldgen-cleanmix/README.md)
now leads with the working prototype's short edit-build-run-read-logs loop and
then teaches the deeper observation procedures. These working guides are
ordinary Markdown, not `susy-manual-family-v1` artifacts or deterministic
projections. They may be corrected as experiments teach us more, and they
never authorize code, compatibility, or release.

Manuals can teach from Atlas, working source, and Blueprints patterns. It does
not need a proof dossier before drafting a useful experimental guide, but it
must label assumptions and update the guide when real implementation failures
teach us something new.

## Same-pass teaching discipline

When a capability becomes operable, or an experiment changes how a developer
should use or diagnose it, add or update its experimental field guide in the
same implementation pass. Bind the explanation to checked-in authority and
retain open or failed gates. A canonical Manual family requires its own
versioned schema, validator, identity, and deterministic projection.
