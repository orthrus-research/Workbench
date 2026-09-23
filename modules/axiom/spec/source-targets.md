# Portable source targets

Status: implemented source-composition foundation. This does not yet construct
the full registry or execute the full pack recipe program.

The Supersymmetry profile owns `axiom-target-policy.json` and exposes its exact
bytes through `workbench.axiom_targets`. The Axiom Java engine independently
verifies and inspects captured packages. Python capture tooling handles Git
objects and archive construction only; it implements no recipe or loader rules.

## Capturing a target

Use four clean checkouts at the source lock's exact commits:

```sh
python3 tools/build_axiom_target.py \
  --supersymmetry /sources/supersymmetry \
  --gtceu /sources/gtceu \
  --susy-core /sources/susy-core \
  --groovyscript /sources/groovyscript \
  --output .workbench/targets/supersymmetry.axiom-target.zip
```

Alternatively, `python3 tools/axiom_sources.py --provision NEW_DIRECTORY`
fetches the four pinned public commits without floating refs, credential helpers
or checkout hooks. Existing directories are never overwritten. Failures remain
available for inspection. This is source-build tooling, not runtime provisioning
or a Minecraft launch.

Capture reads raw Git objects, not filtered checkout files, checks the pinned
commit/tree and clean tracked state before and after reading, and applies the
profile's explicit path selection. Untracked files are not captured. New
candidates are atomically promoted without clobbering an existing path.

The source archive is reproducible and contains:

- `manifest.json`: profile, installed rule binding, policy/lock digests,
  repository roots and source identities.
- `source-lock.json`, `policy.json`: the exact captured authorities.
- `trees/<git-sha1>`: original Git tree objects, including trees needed to
  establish which files the policy selects.
- `blobs/<sha256>`: selected file contents, deduplicated by content hash.

The Java reader verifies tree-object identities, traverses the locked roots,
proves every selected file's Git-blob membership, and rejects missing selected
files, duplicate entries, altered bytes, unsupported paths and unreferenced
archive content. It never extracts archive paths onto the filesystem.
Limits are 256 MiB expanded total, 16 MiB per entry, 50,000 archive entries and
bounded tree traversal. These are package limits, not full OS memory quotas.

Policy completeness means **all files selected by this policy were captured**.
It does not mean all active pack dependencies or executable semantics are
resolved. Required rule-source references cannot be removed by narrowing policy.

Source targets stay local/ignored by default. They contain upstream source under
its original licenses and retain each selected repository's LICENSE. They are
not automatically public artifacts and do not relicense upstream implementation
as part of the Axiom engine.

## Independent and installed use

```sh
/path/to/axiom/bin/axiom target --target /path/to/supersymmetry.axiom-target.zip

workbench axiom target --profile supersymmetry \
  --engine-home /path/to/axiom --java /path/to/jdk/bin/java \
  --target /path/to/supersymmetry.axiom-target.zip
```

The standalone command requires only its Java installation, Linux bubblewrap
and the selected package; Git, Workbench, the original checkouts and Minecraft
are unnecessary. The isolated worker receives that one source archive read-only.
The existing time, heap and transport bounds still apply.

Workbench additionally admits the explicitly installed profile and requires the
returned policy digest to match its packaged policy. A missing, disabled,
changed or mismatched profile is an integration error, never an implicit
fallback. Core still owns process supervision/cancellation and request cleanup.

## Candidate edits and detailed inspection

The first call returns `result.targetId`. Supply it in a subsequent JSON request:

```json
{
  "schema": "axiom.target-request.v1",
  "targetId": "axiom-source-target:sha256:<exact returned digest>",
  "overlays": [],
  "includeFiles": true,
  "sourcePaths": ["groovy/postInit/chemistry/organic_chemistry/Coolants.groovy"]
}
```

Pass this on standalone stdin or with Workbench `--request FILE`. The detailed
record includes the source SHA-256 needed for optimistic edit admission.
`sourcePaths` optionally limits syntax inventory to 1–64 explicit script paths;
loader membership is still derived for the candidate as a whole. Without it,
all scheduled scripts are inspected. Default output is compact; detailed output
remains subject to the transport bound.

Each overlay contains `repository`, `path`, `expectedSha256` and `text`:

- Edit: exact current source hash plus replacement UTF-8 text.
- Create: `expectedSha256: null` plus text, for an absent selected path.
- Delete: exact current source hash plus `text: null`.

Stale expectations, duplicates, absent deletions and out-of-policy paths are
rejected. The original archive never changes. The candidate identity binds all
effective source bytes and the base target; changing a helper, configuration or
Java source changes it too. Inspection filters do not change candidate identity.
Analysis caches must additionally bind `engineId`, not only the source identity.

Inspection also returns [dependency composition](dependency-composition.md):
complete captured artifact declarations, index integrity, explicit side/options,
configuration identities and source transformation declarations. Add optional
`composition: {"side": "client", "options": {}}` to select declarations without
installing or asserting active mods. That selection has its own identity.
An optional `--artifacts` archive adds [verified offline binary inputs](artifact-inputs.md).
The engine rechecks their exact candidate/selection binding before inspection.

## What inspection establishes

The Java implementation preserves GroovyScript's phase order, Linux path
normalization, cross-loader textual-prefix admission and within-loader
specificity/ordering rules. Unknown loader stages are visible but not scheduled
as built-in stages. Legacy classes migration is unsupported. Packmode and
preprocessors are captured but not evaluated.

Groovy 4.0.30 parses scheduled source through conversion only. Inventory records
syntax errors, declared classes, imports, method names, literal selector
references and dynamic selector occurrences, including helper bodies. None of
these syntax facts means a reference resolved or a method executed. Literal
reference counts are distinct syntactic kind/name pairs, not registered items;
dynamic reference counts are syntax occurrences, not runtime invocation counts.

Every result preserves `definitionsExecuted: false`,
`registryReferencesResolved: false`, `registryState: "not-constructed"`,
`installedCompositionQualified: false` and `wholePackParity: false`.
An accepted **target inspection** is not an accepted recipe check.
The existing explicit-context `check`/`query` operations remain separately
bounded; this command does not silently feed unresolved pack state into them.

The next implementation work is the actual dependency, registry/material and
construction execution layer, followed by complete machine coverage and
processing. Full-goal completion still requires the entire
[behavior and parity contract](behavior-and-parity.md).

## Verification

Java tests exercise source membership, omitted-file and corruption rejection,
path confinement, overlays/deletions, loader ordering and non-executing syntax
inspection. `tools/axiom_target_smoke.py` exercises the real pinned package from
an outside-checkout installation, optionally through installed Core and profile.

`tools/axiom_loader_conformance.py` compiles original pinned GroovyScript methods
and compares 2,000 regular-file ordering cases and 2,000 path-admission cases.
Its linked-map, string-count and log dependencies are explicit adapters; this
is evidence for those source methods, not the complete GroovyScript runtime.
The required Linux Axiom CI lane captures exact source inputs and runs these
checks alongside the existing GT overclock/allocation source comparisons.
