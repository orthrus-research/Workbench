# Workbench local Packwiz materialization V2

Status: experimental executable contract

Schema:
[`packwiz-materialization-receipt-v2.schema.json`](../schemas/packwiz-materialization-receipt-v2.schema.json)
and
[`packwiz-materialization-result-v2.schema.json`](../schemas/packwiz-materialization-result-v2.schema.json).

## Purpose

V2 installs a client from a native Packwiz workspace using the defaults that
the pack author declared in each optional metafile. Packwiz remains the
installer and source of package semantics. Workbench supplies Packwiz's own
installer-state representation before installation, observes its result, and
does not emulate downloading or delete optional outputs after Packwiz runs.

This is the only supported Packwiz materialization contract. Its `packwiz-v2`
target variant separates the current pack-declared-default policy from a
pristine Cleanroom bootstrap fixture.

## Pack-declared-default policy

The only V2 policy is `pack-declared-defaults`, policy version `1`. Workbench
reads the refreshed Packwiz index and every indexed metafile before invoking
Packwiz Installer. For every client-applicable metafile (`client` or `both`)
with `option.optional = true`, it records:

- the normalized index-relative `metadata_path` and exact metafile SHA-256;
- Packwiz name, declared side, and derived payload-relative output path;
- the effective declared default, where an absent `option.default` is false;
- the value applied to Packwiz Installer state; and
- whether the output exists, with its exact identity when present.

Rows are sorted by `metadata_path`. The applied value must equal the effective
Packwiz default. For a client target, a false default is absent and a true
default is present. Server-only entries remain Packwiz-owned side exclusions
and are not written into the client installer state or V2 decision set.
Duplicate optional metadata paths or output paths are invalid. The counts and
`decisions_sha256` are derived from the complete ordered pre-install decision
projection; omitting an optional metafile is a failed materialization, not a
warning.

The `option_policy` summary is a closed derived projection: client side,
`pack-declared-defaults`, and an isolated empty launcher sentinel. It cannot
override or disagree with the authoritative `packwiz_options` decision record.

## Native Packwiz installer state

Packwiz Installer 0.5.14 does not honor a new optional file's default in its
non-GUI UI: that UI changes every option it is shown to true. V2 avoids that UI
override without replacing Packwiz. Before the one normal installer run,
Workbench writes a minimal native `packwiz.json` whose `cachedSide` is
`client` and whose `cachedFiles` contains every indexed optional metafile with
`isOptional: true` and `optionValue` equal to its Packwiz default. Pack and
index cache hashes are deliberately absent, forcing Packwiz to process the
refreshed pack normally.

The initial state is always synthesized afresh. Workbench never edits a
hydrated final state to change a choice: retained Packwiz pack or index cache
hashes can authorize an up-to-date fast path before the changed option is
applied.

The installer receives that file at its standard pack-folder state path,
performs side filtering, validation, downloads, removals, and state update,
then writes the final `packwiz.json`. The V2 receipt binds the relative path,
URI, cached side, and exact byte length and SHA-256 of both initial and final
installer state. The final native state must bind `packFileHash` to the exact
refreshed `pack.toml`, `indexFileHash` to its declared current index, and every
decision row to the matching cached optional value. Packwiz success without a
parseable and fully corresponding final state is failure.

## Target and bootstrap isolation

The receipt target has `variant: packwiz-v2`, a policy-bound `variant_id` and
`variant_root_uri`, and distinct fixture, instance, payload, and receipt URIs.
Its public receipt path is
`receipts/packwiz-materialization-v2.json`.

`bootstrap_source` binds the exact Cleanroom bootstrap receipt and launcher
tree copied into the variant. V2 still runs Packwiz Installer against an empty
launcher sentinel and proves that the copied launcher files remain identical.
It does not modify an installed Prism/MultiMC instance or account.

## Identity, reuse, and result

The materialization identity includes the source, refreshed pack, tool,
launcher, payload, target, bootstrap, option-decision, and initial/final
installer-state projections. Their retained
URIs, byte lengths, counts, digests, and option-output identities therefore
cannot change while the same V2 materialization ID still verifies. The receipt
separately retains every admitted seed byte and root.
Reuse remeasures the whole payload, launcher tree, optional outputs, and final
installer state and requires them to match the receipt.

The result format is `workbench-packwiz-materialization-result-v2`, schema
version `2`, with outcome `installed` or `reused`. Its nested receipt format is
`workbench-packwiz-materialization-receipt-v2`, schema version `2`.
