# Workbench workspace context V2

Status: experimental executable bootstrap contract

## Purpose

The workspace context is the common read-only identity envelope used before a
Workbench client asks an authority or runtime service to act.

V2 is used because imported V1 identities remain immutable compatibility
formats. This contract does not reinterpret them.

## Required context

The inspector returns:

- canonical workspace URI;
- exact Git revision;
- dirty state and Git porcelain entries;
- detected Packwiz project name, version, author, manifest identity, Minecraft
  version, and declared loaders;
- the Packwiz index's declared and actual identities, including whether they
  match;
- exact platform profile ID, status, relevant declared versions, and document
  digest;
- exact pack family, selected development profile, maturity, bound platform,
  and document digest; and
- the operation permissions declared by that selected pack profile.

The platform and pack profiles may live outside the inspected project. This is
the normal Workbench integration path: the project retains its native layout,
while Workbench supplies exact context from its own selected authorities.
Project Intelligence can also be run independently with explicit profile
paths.

Profile digests bind the source documents exactly. Packwiz manifest and index
digests bind the project bytes that were inspected. They are not release,
runtime-snapshot, or Atlas evidence identities.

## Failure behavior

Inspection fails without a partial success record when:

- the workspace does not exist or is not a Git worktree;
- a required profile document is missing or malformed;
- the workspace does not contain the native markers declared by the selected
  pack profile;
- the Packwiz manifest is missing, malformed, or identifies a different pack;
- an expected identity or selected profile is missing;
- the pack profile does not bind the loaded platform profile; or
- Git cannot provide an exact revision and status.

Inspection performs no writes and creates no `.workbench/` state.
An out-of-date Packwiz index is returned as a visible mismatch rather than
preventing inspection; later materialization may still treat it as a hard
failure.

The JSON form is defined by
[workspace-context-v2.schema.json](../schemas/workspace-context-v2.schema.json).
