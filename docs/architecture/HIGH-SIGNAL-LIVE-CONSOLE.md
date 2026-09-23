# High-signal live console V1

Status: current experimental Shell surface

## Boundary

The live console is Workbench's terminal presentation and orchestration layer.
It discovers admitted commands, collects typed options, renders an exact
review, observes a launched process, and retains a searchable local timeline.
It is available with or without an IDE plugin.

The console is not a domain or evidence authority. It does not decide project
identity, Atlas facts, Blueprint admission, profile policy, runtime validity,
or mutation success. The selected command route and its owning module still
validate inputs, plan work, obtain any required consent, execute, and publish
the authoritative report or receipt.

## Current commands

The installed launcher is the user-facing route:

```text
workbench console catalog [--json]
workbench console wizard [COMMAND]
workbench console run COMMAND [--set KEY=VALUE ...] [--execute]
workbench console watch [OPTIONS] -- COMMAND [ARG ...]
workbench console ingest PATH [PATH ...]
workbench console sessions [--json]
workbench console replay [SESSION]
```

`workbench console` opens the interactive surface when input and output are
suitable TTYs; otherwise it prints the catalog and directs the caller to an
explicit subcommand. `catalog`, `run`, `watch`, `ingest`, `sessions`, and
`replay` are usable without the full-screen interface.

From a source checkout, use the same router through
`python3 tools/workbench.py console ...`. The locked Pixi form is
`pixi run --locked --no-config workbench console ...`. An installed Core uses
its packaged suite identity and external suite-state policy; it does not
require or infer a source checkout.

Run `workbench console --help` and
`workbench console SUBCOMMAND --help` for the exact current options. The live
catalog, rather than this document, is the current command inventory.

## Catalog, review, and consent

Every catalog entry declares an owner, availability and visible limitations,
typed fields, risk, preview strategy, exact argv template, and action digest.
Unavailable entries remain discoverable. The console does not infer a
workspace, profile, target, side, Java runtime, launcher, or consent answer to
make an entry appear runnable.

The wizard checks interaction-level shape such as required values, choices,
booleans, integers, paths, repetitions, and exclusive groups. It does not copy
downstream profile policy or treat a rendered preview as successful owner
validation.

`console run` builds argv as separate tokens and launches with `shell=false`.
Its safely quoted display is never parsed back into a command. `console watch`
likewise accepts one explicit argv after `--`; it is not an implicit shell.

Native clients bind a run to the complete catalog digest, selected action
digest, and returned review digest. They request the review first, present the
preview and execution argv, run any owner preview, and carry the same digests
into explicit execution. Drift fails before child launch. The digests bind
content; they are not authentication or proof of human approval. Direct
terminal use without digest expectations retains the same owner-validation and
single-process consent boundary.

The exact wire sequence and sensitive-option binding are defined by the
[command-binding V2 contract](../../modules/workbench-shell/contracts/workbench-live-console-command-binding-v2.md).

## Presentation

The `--console` modes are `auto`, `tui`, `plain`, `jsonl`, and `messages`.
Interactive mode provides the palette, wizards, follow/pause, filters, literal
search, and event detail. Plain mode emits stable lines without cursor
assumptions. Message mode emits normalized messages without console chrome or
the default signal-only view. JSON Lines emits one derived event object per
line.

Color, wrapping, filtering, literal search, exact-duplicate folding, and
viewport limits are presentation state. They do not alter retained bytes,
event order, identities, summary counts, or owner results. Child control
characters are escaped before display, and cursor control is disabled for an
incapable or redirected output.

## Retained sessions

Source-checkout sessions live under:

```text
.workbench/sessions/live-console/<session-id>/
  session-v1.json
  manifest-revisions-v2/
  events-v1.jsonl
  artifact-index-v1.json
  <observed-stream>.raw
```

The packaged Core redirects this custody into its selected external suite
state root. The storage root and session directory are owner-private; session
files are owner-readable/writable only. Symlinked roots, unsafe parents, and
session-ID collisions are rejected. `--no-retain` is the explicit exception
that uses an ephemeral session.

The session manifest starts as `running` and terminates as `complete`,
`failed`, `cancelled`, or `incomplete`. Manifest revisions preserve the
published state chain. On successful finalization, the artifact index seals
the event journal and raw-stream sizes and digests. A process kill, power loss,
or retention failure can leave inspectable residue, but never implies a
completed run.

The retained command preserves argv order, working directory, intent, and
`shell: false`. Catalog-declared sensitive option values are replaced with
`<redacted>` in that retained command projection. The child still receives the
reviewed values, and the console does not promise to redact secrets printed by
the child.

Exact record shapes are owned by the
[live-console V1 contract](../../modules/workbench-shell/contracts/workbench-live-console-v1.md),
the [session schema](../../modules/workbench-shell/schemas/workbench-live-console-session-v1.schema.json),
and the [event schema](../../modules/workbench-shell/schemas/workbench-live-console-event-v1.schema.json).

## Raw source and derived events

Each observed `*.raw` file preserves the exact bytes accepted from one stream,
including low-signal, repeated, invalidly encoded, empty, and unterminated
input. `events-v1.jsonl` is a derived append-only navigation projection. Each
event binds a half-open byte range in one raw stream and records its ingestion
order, classification provenance, limitations, and any textual source-locator
candidates.

Ingestion timestamps and cross-stream order describe the console's local
observation, not emitter order or a distributed clock.
Classifications such as severity, subsystem, Mixin, registry, or worldgen are
bounded presentation matches, not ownership or causal findings. Unmatched
material remains generic rather than receiving an inferred diagnosis.

High signal controls emphasis, not retention. Clustering is exact only, and
every occurrence keeps its own event row and raw locator. Search is literal,
not regular-expression, fuzzy, semantic, mapping, or causal search. A textual
source locator becomes clickable only when the bounded local resolver finds
exactly one file; zero or multiple matches remain unlinked.

## Outcome and process safety

Line severity and command outcome are separate. A handled probe may emit an
error, while an explicit failed required check may make a zero-exit process an
effective failure. Nonzero exit, launch failure, signal termination, retention
failure, or an admitted explicit outcome-failure event prevents a completed
success. These rules preserve an observed failure but do not identify root
cause or replace the owning command's result.

Launched commands receive a distinct process group. Cancellation first sends
an ordinary interrupt to that group and records the request; any escalation is
bounded and visible. Wizard, palette, filter, and search input is never
forwarded as child stdin.

## Validation

Run the current help routes and focused Shell checks from the repository root:

```bash
python3 tools/workbench.py console --help
python3 tools/workbench.py console run --help
python3 validation/validate.py --suite workbench-shell
python3 validation/validate.py --policy
git diff --check
```

The broader entry-point and storage context is in the
[Workbench Shell README](../../modules/workbench-shell/README.md), and
validation depth is described in
[Validation and testing](VALIDATION-AND-TESTING.md).
