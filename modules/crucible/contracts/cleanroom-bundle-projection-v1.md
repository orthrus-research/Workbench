# Cleanroom Bundle Projection V1

## Purpose

`workbench.worldgen-observatory.cleanroom-bundle-projection.v1` is a bounded,
derived matrix input for a single validated Worldgen Observatory V1 bundle. It
allows each large bundle to be validated and released in a separate worker
before the Cleanroom fixture matrix is evaluated.

This projection does not replace, weaken, or redefine the V1 capture bundle.
The retained bundle remains the evidence authority. A projection is accepted
only through the strict parser in `cleanroom_matrix.py` and records the exact
canonical SHA-256 of its source bundle.

## Retained material

Each content-addressed projection retains:

- the explicit run manifest, including caller-supplied runtime identities;
- the exact capture summary;
- either the exact completion seal or the exact incomplete crash residue;
- canonically ordered checkpoint rows with exact actor fields;
- canonically ordered cooperative RNG rows with exact actor fields; and
- independent canonical digests of both row projections.

Completed projections require complete, zero-drop, sealed source evidence and
full fixed-region checkpoint and RNG coverage. Incomplete projections require
an unsealed `process_crash` or `forced_termination` residue and cannot contain
checkpoint or RNG summaries.

## Trust boundary

Content addressing detects accidental or unacknowledged byte changes; it is
not a signature. `project_cleanroom_bundle` is the publication authority for
this derived artifact because it validates the complete source bundle before
projection. `parse_cleanroom_bundle_projection` validates closure, identities,
digests, ordering, actor exactness, and completed/incomplete invariants without
loading the source bundle.

No plan, profile, fixture, transformed-runtime, mod-set, configuration, or
world-instance identity is inferred by this boundary. Those values must already
be present in the validated run manifest supplied by the runtime harness.
