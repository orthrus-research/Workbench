# Supersymmetry recipe reload diagnostic V1

Status: experimental, read-only, profile-owned.

The additive pack-owned profile is selected through
[`experimental-runtime-diagnostics-v1.json`](../../experimental-runtime-diagnostics-v1.json);
the stable pack profile document is not rewritten.

`workbench-supersymmetry-recipe-reload-diagnostic-v1` explains one narrow
runtime signal that is otherwise lost in generic exception diagnosis. It
consumes the exact `minecraft-groovy-log` bytes already verified by a
process-bound Workbench V3 runtime-launch receipt. The receipt's project fields
must match the selected workspace. The current workspace revision is retained
only as unverified context because the receipt does not bind source revision.

The observer recognizes complete GregTech conflict sequences with this shape:

1. `Recipe duplicate or conflict found in RecipeMap ...`
2. `Attempted to add Recipe: ...`
3. either `Which conflicts with: ...` or
   `Could not find exact duplicate/conflict.`

All three rows must be `WARN` rows from the same logger. An `INFO` row from the
`supersymmetry` logger whose complete message is
`Running scripts in loader 'postInit'` starts an execution epoch. The first
epoch is `initial`; later epochs are `reload`. Events without such an epoch are
reported as `unbound`. The recognized postInit timing/completion row closes the
active epoch, so later warnings cannot be attributed to it.

An empty initial conflict set is reported as clear only after the same logger
emits the exact `Groovy scripts took ... to compile and ... to run in
postInit.` completion shape. A log truncated after the loader start remains
inconclusive. A repeated postInit start is sufficient for the profile's
restart-required guidance even if the later capture is truncated.

Groups are keyed by diagnostic kind, observed script logger, and recipe map.
They contain total/initial/reload/unbound counts, resolution counts, source
line bounds, and at most three line-range examples. The result also retains
bounded incomplete-sequence frontiers. Groups are ordered by reload count,
then total count, then lexical logger/map identity so the largest reload
clusters lead the answer.

The profile recommendation is `restart-required` whenever a repeated
`postInit` execution is observed. This reflects retained Supersymmetry
experimental evidence that the direct reload path can diverge even without a
source-tree mutation. This diagnostic does not claim that every grouped warning
was caused by a source change.

V1 does not:

- reconstruct or compare effective recipe registries;
- prove that a logger is the causal source owner;
- attribute native Java registrations;
- assess lookup activity, progression, JEI/client visibility, or save safety;
- authorize source edits or publication into Atlas.

The command surface is:

```text
workbench runtime-diagnose <workspace> --receipt <runtime-launch-v3.json> --recipe-reload
```

Use `--json` for the identity-bound structured result. The ordinary
`runtime-diagnosis-v2` format is unchanged.

## Cold-start conflict-group comparison

The same experimental profile also declares
`workbench-supersymmetry-groovy-conflict-group-comparison-v1`. Shell verifies
two explicitly selected V3 receipts, derives both V1 diagnostics with the same
loaded profile implementation, and asks the profile observer to compare them.

```text
workbench runtime-diagnose <workspace> --recipe-reload \
  --baseline-receipt <baseline-runtime-launch-v3.json> \
  --receipt <candidate-runtime-launch-v3.json>
```

Both observations must contain exactly one completed initial postInit
execution. A repeated postInit, unbound conflict, incomplete sequence, failed
or non-exited launch, or truncated group set makes the pair incomparable. This
prevents reload divergence from being presented as a candidate cold-start
change.

The complete comparison key is diagnostic kind, observed script logger, and
recipe map. Counts are classified as `newly-observed`, `increased`,
`decreased`, `no-longer-observed`, or `same-count`. Exact-conflict versus
unidentified-conflict count changes are retained separately. At most 100
groups can occur in each accepted V1 input, so the comparison emits the exact
union of at most 200 rows without truncation.

These are group-count observations, not stable recipe identities. Runtime
object strings in the log are not stable across launches, and V1 deliberately
does not retain attempted recipe payloads. Therefore `same-count` does not
mean unchanged recipes and `no-longer-observed` does not prove a source fix.
The baseline is an explicit caller selection, never an inferred or persisted
last-green designation. V3 binds the exact evidence bytes and process exit,
but not the workspace revision or current pack-profile document at launch.
The comparison is not an approval or CI gate.
