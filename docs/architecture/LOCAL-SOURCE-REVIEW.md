# IDE-first local source review

Workbench follows developer-authored edits. Guided generation is optional and
deferred; local source review does not require Blueprints, a construction plan,
a pull request, a compiler installation, or a game runtime.

In VS Code or IntelliJ, choose **Workbench: Review Saved Local Changes**. Create
a developer context inside the IDE or select an existing Work Session, then
choose a local Git baseline. The baseline is resolved once to an exact commit
and retained in IDE memory for subsequent reviews; choose another context or
baseline explicitly when needed. Nothing is fetched, checked out, or committed.

The same backend operation is callable from a service or terminal:

```bash
workbench context run SESSION_ID -- review local --baseline-ref=HEAD
workbench context run SESSION_ID -- review local --baseline-ref=COMMIT_OID \
  --expect-review source-review:sha256:REVIEW_DIGEST
```

Use actual returned identities. The second form rejects changed source, index,
profile, interpreter, or other operation inputs; it never silently retargets an
old finding. Baseline references are local only, and captured Git objects bypass
checkout filters, external diff drivers, replacement objects and lazy fetching.

## What the developer sees

- Exact saved before/after file diffs, including additions, deletions and mode
  changes. Binary and over-bound files remain listed with byte identities and
  an explicit reason a text projection is unavailable.
- Recognized recipe, material, quest and quest-line declaration changes.
  Changed semantic keys remain additions/removals; nearby lines are not proof
  of recipe identity across versions. Duplicate definitions remain distinct.
- Source findings labeled introduced, persisting, resolved, or uncompared,
  with verified locations and declared related sources.
- Separate source interpretation, compiler and runtime check states. This
  vertical runs source interpretation only. Compiler and runtime checks are
  explicitly not run; neither silence nor source applicability implies success.

Both clients use native read-only diffs, source navigation and saved-source
diagnostics (VS Code Problems; IntelliJ editor highlights and the finding
picker). Review details open as a read-only JSON document, not a new dashboard.
Changing local editor text or receiving workspace filesystem changes invalidates
the active result and clears its diagnostics. Older/cancelled responses cannot
replace a newer review. Reanalysis is explicit and cancellable, not triggered
on every keystroke. Snapshot documents remain historical read-only views.

The baseline/candidate comparison includes saved tracked and nonignored
untracked source. Unsaved editor buffers are not silently saved or included;
findings never decorate a buffer that differs from the captured bytes. Native
compiler/language-service buffer diagnostics remain independent.

## Ownership and bounds

Project Intelligence reads immutable local Git objects and captures the saved
candidate. PPS and installed profile interpreters normalize declarations. Atlas
compares them, retains unknowns and provides declared relationships. Shell
composes the existing Work Session selection; clients only present the result.
Core retains environment/process/resource lifecycle ownership.

There is no source application, rollback authority over developer edits,
construction receipt, new session journal, persistent index, source-local state,
or automatic runtime execution. The source review can be installed without
Blueprints: Shell, Cleanroom and Supersymmetry now declare construction as a
native optional extra. Existing authoring features require that extra and remain
available for separate use; their records and recovery behavior are not deleted.

Each input retains the existing 100,000-file, 64 MiB/file, 512 MiB/tree bounds.
Symlinks, submodules, unresolved index merges and moving candidate trees fail
closed. Results bound files, declaration changes and findings to 1,000 each;
text is bounded to 256 KiB/file and 4 MiB overall before transport limits.
Counts and truncation remain explicit. Unsupported syntax or unavailable
interpretation preserves the file review while marking analysis unavailable.

The current Supersymmetry interpreter uses the documented client-side static
analysis context. It does not prove runtime registration, dedicated-server
behavior, progression, reload safety or publication readiness. This IDE handoff
is native Linux first; Windows/WSL cross-host mapping is not claimed.

## Explicit execution after review

[Saved-candidate checks](SAVED-CANDIDATE-CHECKS.md) connect saved source to explicit,
disposable execution without a generated construction plan. Source review still
does not launch anything. Compiler observations, execution, retained evidence and
cleanup remain distinct; guided authoring remains tertiary work.
