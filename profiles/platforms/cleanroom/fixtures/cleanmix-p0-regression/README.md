# CleanMix P0 regression fixture

This exact-profile fixture supplies the phase, late-registration, and named-gap
scenarios in the CleanMix runtime regression matrix. Each row is selected by
`workbench.cleanmix.p0.row_id`, runs in a fresh JVM, and exits during Forge
pre-initialization after emitting one structured result or failure.

The native no-op transformer records first-seen target order while retaining
the total entry count. Repeated calls through Foundation's live and delegated
paths therefore remain visible without being mistaken for repeated class
definitions. Foundation final-definition evidence remains an independent
required receipt.

The reentrant row deliberately requests its target from the config plugin's
`getMixins` callback while preparation is active. The late-after row retains
the original loaded `Class` identity and reports any correlated trigger
failure instead of fabricating retroactive transformation.

This is candidate-bound experiment code. It does not claim a portable mod,
consumer compatibility, or support for a different Cleanroom/CleanMix epoch.

Run all P0 rows from the repository root with:

```bash
python3 profiles/platforms/cleanroom/tools/run_cleanmix_p0_matrix.py \
  --output .workbench/evidence/cleanmix-runtime-conformance/x01-execution
```

The output must be initially absent. The runner builds both fixture archives,
creates an isolated runtime per row, captures all five exact runtime evidence
families, validates their raw streams, and writes one content-addressed
execution report. XFAIL and XPASS remain distinct from pass, and the
target-already-loaded row succeeds only as `must_reject`. The immutable X01
report retains its creation-time three-deep XPASS; the separate X02 result
resolved that finding as a false first-entry oracle using direct application
joined to Foundation final bytes.
