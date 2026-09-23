# Developer-selected recipe capture inputs V1

This input binding lets the finite Forge recipe adapter admit a developer's
saved source revision without requiring the historical circuit-branch commit.
It is a profile contract foundation. It does not launch a game, prepare an
installed capture environment, or qualify arbitrary mod/JVM versions.

The format is `workbench-supersymmetry-developer-observation-input-v1`. The
profile module `workbench_profile_supersymmetry.recipe_capture_inputs` exposes:

- `build_source_binding(candidate, *, deleted_paths=())`
- `validate_source_binding(value)`
- `build_capture_input(candidate, *, platform, runtime_artifacts, capture_id,
  launch_id, candidate_lock_sha256, adapter_profile_sha256, deleted_paths=(),
  observation_preparation=None)`
- `validate_capture_input(value)`

`candidate` is the existing `workbench-saved-candidate-v1` record. Core/Shell
composition should obtain it from Project Intelligence's saved-source capture,
which includes tracked and non-ignored untracked saved files. The explicit list
of deleted tracked paths reconciles that record's source-file count. Ignored
local mod JARs belong in selected runtime inputs, not in inferred source files.
This API validates records and identities; it does not read source file contents
or prove which bytes a native worker loaded.

## Portable content and local custody

`pack_source` contains `format`, `id`, `candidate`, `deleted_paths` and
`source_content_sha256`. The content digest covers the sorted relative-file
manifest (paths, modes, sizes and SHA-256 values) and declared deleted paths.
The pack binding covers the exact revision and content digest. Neither depends
on a workstation path. The original candidate ID and source root URI remain
intact as local custody/provenance; those records can differ across machines.

Paths must be portable relative ordinary-file paths, with no traversal,
reserved Windows names, collisions or case-insensitive aliases. Candidate
checksums, file modes, counts and size bounds are validated. Changing a saved
file, addition or deletion changes content identity; moving the same source
bytes to another root does not. The selected revision is explicit, not a moving
branch name or an implicit latest-release substitution.

## Compatibility remains a separate check

The first model admits the existing exact Minecraft 1.12.2 / Forge 14.23.5.2860 /
Java 8 dedicated-server tuple and pinned original artifact set. It accepts new
saved source revisions within that runtime model. A changed pinned artifact,
loader, Java major or physical side is rejected; caller-asserted compatibility
is not accepted. The full runtime inventory and selected Java executable still
belong to capture orchestration. Supporting a new target tuple requires a
separately established profile/matching model.
Modern Cleanroom Java 21/25 execution is not admitted by this Forge contract.

The manifest also binds capture/launch IDs and the candidate-lock and adapter
profile hashes. If original capability preparation is declared, its exact
policy and phase are required. The existing Crucible sealed capture and
preparation readers still validate actual payload evidence; a declared policy
cannot substitute for that payload. Import verifies the same capture-protocol
hashes and records this admission implementation in projection provenance.

The historical `workbench-supersymmetry-branch-observation-input-v1` path and
its exact pins remain unchanged. This new format is additive. A retained graph
preserves its historical input and native scope, not current environment
readiness. See the [finite projection](finite-recipe-projection-v1.md) and
[Atlas import contract](../../../../modules/atlas/contracts/atlas-recipe-capture-adapter-v1.md).
