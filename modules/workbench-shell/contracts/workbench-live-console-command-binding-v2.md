# Workbench live-console command binding V2

## Status and scope

This contract adds cross-process content binding for native IDE consumers. It
does not modify the V1 console event, session, or retained-source formats and
does not move validation or approval authority out of the owning tool.

The catalog wire format is
`workbench-live-console-command-catalog-v2`. Its complete, unfiltered payload
has a top-level `catalog_digest`. Every command has an `action_digest`. Both
are lowercase `sha256:<64 hex>` identifiers over UTF-8 canonical JSON (sorted
object keys, no insignificant whitespace, and Unicode emitted directly).

## Digest material

An action digest covers:

- format `workbench-live-console-action-binding-v2`;
- every public field of the command except the digest itself, including risk,
  preview strategy, availability, option rendering metadata, and limitations;
  and
- the exact internal argv template after Workbench-root and Python tokens are
  resolved, while typed option-placement markers remain explicit.

The catalog digest covers the exact V2 catalog payload without
`catalog_digest`, including suites and all action digests. A filtered catalog
view retains the digest of the complete catalog from which it was selected.

## Review and execution sequence

A native client uses one selected catalog record and sends:

```text
console run COMMAND [--set KEY:=JSON ...]
  --expect-catalog-digest CATALOG
  --expect-action-digest ACTION
  --review-json
```

Workbench reloads the current catalog and fails before an owner process is
started if either expectation differs. It returns an exact
`workbench-live-console-command-review-v2` object. The object identifies the
catalog, action, command, risk, preview strategy, preview and execute intents,
human-rendered preview and execute argv, and a `review_digest`.

The review digest covers
`workbench-live-console-command-review-binding-v2`, the catalog and action
digests, command ID, risk, preview strategy, both intents, and the exact
unredacted preview and execute argv arrays plus their rendered review strings.
It therefore changes if the
checkout, catalog action, typed assignments, rendering policy, preview argv,
or execute argv changes.

Each option's public catalog material also binds its `sensitive` flag. The
review and child process receive the exact unredacted value so consent and
owner execution stay bound to the real argv. Before creating a retained V1
session manifest, however, the console replaces every catalog-declared
sensitive option value with `<redacted>` while preserving its flag, position,
and argument shape. A sensitivity change therefore changes both the action and
catalog digests rather than becoming an unreviewed retention policy change.

The client carries the same three expectations into the owner preview and the
final execution:

```text
console run COMMAND [--set KEY:=JSON ...]
  --expect-catalog-digest CATALOG
  --expect-action-digest ACTION
  --expect-review-digest REVIEW

console run COMMAND [--set KEY:=JSON ...]
  --expect-catalog-digest CATALOG
  --expect-action-digest ACTION
  --expect-review-digest REVIEW
  --execute
```

Supplying any digest expectation outside `--review-json` makes the invocation
a bound run and requires all three. A mismatch is terminal before child launch.
`--review-json` itself never launches either argv and requires the catalog and
action expectations. Direct command-line use without digest expectations
remains available and retains the V1 single-process consent behavior.

These digests are content identities, not authentication, authorization,
freshness, or evidence that a human approved the action. The IDE controls the
review → owner-preview → consent → execute sequence; the owning command still
performs its own plan binding, revalidation, and consent checks.
