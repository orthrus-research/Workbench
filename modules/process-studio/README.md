# Process Studio

Process Studio compares bounded process evidence without creating a new
runtime truth, safety finding, release gate, or mutation authority.

## Public recipe comparison

```text
workbench process recipes compare BEFORE_GRAPH AFTER_GRAPH
workbench process recipes compare BEFORE_GRAPH AFTER_GRAPH --json
```

This narrow route delegates the complete comparison to Atlas and forwards its
human or JSON result unchanged. Inputs are explicit comparable runtime recipe
graphs. Added, removed, or changed finite recipes and resource flows remain
bounded to those graphs; the result does not infer causality, balance,
execution, or global reachability.

## Experimental effect comparator

The V2 kernel compares closed registry and registration-effect snapshots from
explicit family adapters:

```bash
workbench process effects compare \
  --baseline .workbench/evidence/before.json \
  --candidate .workbench/evidence/after.json \
  --fixture-adapters --json
```

An optional content-addressed change envelope declares expected structural
effects:

```bash
workbench process effects compare \
  --baseline .workbench/evidence/before.json \
  --candidate .workbench/evidence/after.json \
  --fixture-adapters \
  --envelope .workbench/evidence/expected.json \
  --output .workbench/evidence/comparison.json
```

The built-in GT finite-recipe and Forge crafting adapters are synthetic
fixtures and require `--fixture-adapters`. No live family adapter, capture
authentication, or temporal admission is implied.

## Comparison contract

Baseline and candidate snapshots must bind the same profile, platform, side,
lifecycle stage, scope, coverage, adapter owner, semantic encoder,
correspondence policy, and bounds. Input is strict UTF-8 JSON. Symlinks,
replacement during read, duplicate keys, floating-point values, and unsafe or
oversized structures are rejected. Output is create-new.

Adapters own correlation and semantic fingerprints. Process Studio treats
both as opaque and classifies each exact group as `added`, `removed`,
`modified`, `unchanged`, `ambiguous`, or `unresolved`. It never substitutes a
runtime record ID, list position, display label, or fingerprint for missing
correspondence.

Without an envelope, assessment is `comparison-only`. An exactly bound
envelope may yield `matches-declared-envelope`, `mismatch`, or `unresolved`.
None establishes causality, playability, balance, support, or permission to
change source.

The Python package exposes closed projection, comparison, envelope, shape
validation, and source-replay validation functions. Production callers must
supply an admitted family adapter; synthetic fixtures are never defaults.
Semantic validators recompute document IDs, ordering, group classifications,
summaries, envelope assessment, and cross-record bindings.

## Authority and limits

Process Studio does not launch Minecraft, discover captures, mutate recipes,
infer source-to-runtime cause, determine progression reachability, or approve
a release. Crucible owns controlled capture, Atlas owns observed and derived
knowledge, Blueprints owns construction, and profiles own pack/platform
semantics.

Fixed resource limits bound snapshot bytes, record and group counts, nesting,
container size, strings, and integers. Partial, failed, unavailable, and
not-observed coverage remains visible. Empty declared coverage means no
families were claimed; it is not global completeness.

Contracts and schemas:

- [bounded comparison](contracts/bounded-observed-effect-comparison-v2.md)
- [snapshot projection](schemas/bounded-effect-snapshot-projection-v2.schema.json)
- [change envelope](schemas/effect-change-envelope-v2.schema.json)
- [comparison result](schemas/bounded-observed-effect-comparison-v2.schema.json)
