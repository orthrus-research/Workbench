# Workbench live console V1

Status: experimental product-generic Shell presentation and orchestration
contract

Contract ID: `WORKBENCH-SHELL-LIVE-CONSOLE-V1`

Machine-readable artifacts:

- [live console event V1](../schemas/workbench-live-console-event-v1.schema.json)
- [live console session V1](../schemas/workbench-live-console-session-v1.schema.json)

The event format is `workbench-live-console-event-v1`. The session format is
`workbench-live-console-session-v1`. A semantic change to an identity-bearing
field, source-retention rule, outcome rule, or authority boundary requires a
new format version.

## Purpose and authority

The live console provides terminal navigation, capability discovery, option
wizards, safe command invocation, live display, and private retained sessions.
It is a Workbench Shell record, not a new evidence authority. The catalog and
wizard construct an argv vector for an existing route. The route's owning
module and selected profile remain responsible for target validation, plans,
consent, execution, and authoritative reports or receipts.

An event classification is a derived navigation projection over ingested
process text. It can say that text matched a declared literal or structural
rule. It cannot establish that a mod caused a crash, a listener made a
mutation, a Mixin applied, a registry entry exists, a build artifact is sound,
or a Blueprint gate passed. Atlas facts, Blueprint admission and verification,
Manual teaching, Sentinel policy findings, and Crucible runtime evidence keep
their existing owners.

## Command catalog and wizards

The catalog admits named public and expert commands. Every admitted entry has:

- a stable catalog ID, owner, title, and bounded description;
- an exact argv prefix, with no shell fragment;
- a declared read-only, preview, or mutating posture;
- an availability state and a visible reason when unavailable; and
- fields for every supported option, including required values, choices,
  booleans, integers, paths, repeatable values, and mutually exclusive groups.

Catalog metadata is presentation metadata. `available` means the Shell can
offer the route with its known prerequisites; it is not a readiness or
compatibility result. Unavailable entries remain discoverable and are not
silently removed. The console does not infer a pack profile, target, side,
runtime, Java role, or consent answer.

The wizard may check required entry, choice membership, and primitive syntax.
It must show the exact argv it will pass. It must not reproduce downstream
profile policy or treat a successful wizard review as downstream validation.
For a command that can mutate state, the same route-specific preview,
revalidation, and consent contract applies inside and outside the console.
The console cannot add an execute, force, purge, approval, or confirmation
token on the developer's behalf.

Invocation always uses an argv array with `shell=false`. Display quoting is
one-way presentation and is never parsed back into executable input. Child
output cannot become a command. V1 has no implicit arbitrary-shell catalog
entry.

## Adaptive presentation

Interactive mode requires suitable input and output TTYs. It provides menus,
a command palette, field wizards, review, live follow/pause, filters, literal
search, and event details. Plain mode emits stable lines and accepts no cursor
control assumptions. Message-only mode emits every safely decoded normalized
message and does not inherit the default signal-only view. It is not the exact
raw byte record; only retained `*.raw` artifacts serve that role. JSON Lines
mode writes one event object per line.

Selection, wrapping, color, exact-duplicate folding, filters, and search are
view state. They do not change retained order, identities, classifications,
session summary, or downstream artifacts. When output is redirected, V1 does
not emit ANSI cursor movement. Control characters in untrusted messages are
escaped in the display while the retained source record remains available.

## Retained session

One launched child produces one directory:

```text
.workbench/sessions/live-console/<session-id>/
  session-v1.json
  events-v1.jsonl
  <observed-stream>.raw
```

The storage root and directory are private mode `0700`; each file is private
mode `0600`. A producer must create a fresh non-symlink directory, use
exclusive file creation, and reject a path collision or unsafe parent. Session
IDs are producer-generated identifiers, not user paths. The session's
`retention` member binds its exact directory, event projection, and map of raw
source streams. Permissions are live producer invariants rather than evidence
inferred from the manifest.

The directory is operational state under ignored `.workbench` storage. It is
not automatically Atlas evidence or a release proof. It can contain source
paths, workspace output, stack frames, and other private development details.
The child receives the exact reviewed argv. Its retained command projection
preserves argv order and flags but replaces catalog-declared sensitive values
with `<redacted>` before the session manifest is created. The catalog action
digest binds that sensitivity declaration.
V1 does not redact arbitrary secrets printed by the child, so it must not make
these files group/world-readable, commit them, or publish them automatically.

Every stream selected for observation is declared even when it is empty. V1
permits at most sixteen raw streams in one session. A consumer must bind replay
to the physical enumerated session directory and reject or quarantine a
manifest whose session ID or retained directory disagrees with that location.

The session lifecycle is:

- `running`: private retention exists and the launch/ingestion attempt is in
  progress;
- `complete`: all observed streams closed and a successful effective outcome
  was established;
- `failed`: launch, execution, required-check, or retention failure was
  established;
- `cancelled`: a console cancellation was requested and termination observed;
  or
- `incomplete`: retention failed before terminal state could be established or
  an interrupted manifest was recovered.

`started_at` and `updated_at` are UTC wall-clock observations. `ended_at` is
present only for a terminal manifest. They do not establish cross-host event
order. The exact command records catalog command ID, optional label, argv,
working directory, intent, `shell: false`, observed PID, and observed process
group ID. The optional exit member records the process and effective exit
codes, effective outcome label, and cancellation detail; it does not
reinterpret the owning tool's domain result.

## Raw transcript source record

The per-stream `*.raw` files together are the session source record for console
ingestion. They preserve the exact bytes read from each observed stream.
Complete logical lines are projected when their delimiter arrives; an
oversized record can be bounded at a chunk limit, and a final unterminated
fragment is projected when the stream closes. Repeated and low-signal bytes
are never omitted.

The event message is bounded decoded text supplied to the classifier. V1 uses
UTF-8 `surrogateescape` while framing, renders an invalid source byte visibly
as `\xNN`, and records `invalid-utf8-rendered-as-byte-escapes` while keeping
the exact original bytes in the raw source record. A raw byte position is not
a statement about when the child originally emitted it. Reads from stdout and
stderr can race, and descendant processes can buffer output.

The event's `raw_locator` carries the raw artifact name (or null only when
retention was explicitly disabled), half-open `byte_start` and `byte_end`, a
one-based logical `line` and `chunk`, and the observed boundary (`lf`, `crlf`,
`cr`, `limit`, or `eof`). The byte range includes the line terminator when one
was observed. A producer must write raw bytes before the derived event so that
a retained event never intentionally points ahead of its source storage.

## Derived event fields

Each row in `events-v1.jsonl` is an independent closed event object. Required
members have these meanings:

- `format_version` is exactly `workbench-live-console-event-v1`;
- `event_id` is unique within the session and stable for that retained row;
- `sequence` is the one-based ingestion order and has no gaps in a completed
  session;
- `ingested_at` is a UTC wall-clock observation and `monotonic_ns` is the
  local monotonic observation used for in-session ordering;
- `source_timestamp` preserves a parsed timestamp string when one was present
  and is otherwise null;
- `source` is a stable producer or catalog label and `stream` is the bounded
  observed stream name;
- `raw_locator` points to the exact source byte range and records its logical
  line, chunk, and boundary;
- `kind` is `text`, `log`, `build`, `compiler_diagnostic`, `mixin`, `groovy`,
  `registry`, `worldgen`, `exception`, `stack_frame`, or `stage`;
- `severity` is `trace`, `debug`, `info`, `warning`, `error`, `fatal`, or
  `unknown`;
- `subsystem` is `generic`, `gradle`, `compiler`, `minecraft`,
  `cleanroom-fml`, `mixin`, `cleanmix`, `groovy`, `registry`, `worldgen`,
  `java`, or `workbench`;
- `logger` and `thread` preserve parsed bounded labels and are otherwise null;
- `message` is the decoded logical line or bounded chunk without its line
  delimiter;
- `parse_provenance` is `raw`, `parsed`, or `heuristic`;
- `classification_basis` contains the exact bounded rule IDs that justify
  classification and is empty for fallback classification;
- `cluster_key` is a SHA-256 presentation key shared only by exact duplicates;
- `signal` controls display emphasis but never retention;
- `outcome_failure` records an explicit failure signal independently of
  severity;
- `source_locators` contains textual path, line, optional column, and label
  candidates, deduplicated in appearance order without claiming filesystem
  resolution;
  and
- `limitations` records event-specific decode, ordering, parsing, or linking
  bounds.

Event IDs and cluster keys are local presentation identities. They are not
content-addressed evidence IDs and must not be used as Atlas, Blueprint, or
Crucible identifiers.

## Classification, exact clustering, and linking

The V1 classifier uses declared case-sensitive or case-insensitive literal
recognizers, strict prefixes, process state, and bounded stack-frame syntax.
`classification_basis` exposes every matched rule ID. When several rules
match, deterministic producer precedence selects display fields while all
applicable basis IDs may remain visible. This is classification provenance,
not a causal chain.

Clustering is exact only. The key hashes source, stream, kind, severity,
subsystem, logger, thread, decoded message, parse provenance, classification
basis, outcome marker, and the complete deduplicated source-locator list. All
of those values must compare exactly. Event ID, ingestion clocks, parsed source
timestamp, and raw range are occurrence metadata and are deliberately excluded,
so the same parsed log record can cluster when it recurs at another time or raw
offset. V1 does not erase numbers, object IDs, paths, frames, or coordinates to
manufacture similarity. Every occurrence remains an event row. A folded view
derives count and first/last occurrence without modifying source or projection.

Search is a literal substring over the event message and exposed labels. V1
does not interpret regular expressions or perform fuzzy, semantic, mapping, or
causal search.

A source locator is a textual candidate, not yet a link or an existence claim.
Candidates are deduplicated by exact path, line, and column in appearance
order. The presentation may create a clickable link only when a bounded local
search resolves that candidate to exactly one file, and that resolution must
not mutate the retained event. If no file or several files match, the candidate
remains visible but unlinked. The console does not choose between mapping
namespaces, generated and handwritten source, or original and transformed
methods.

## Severity, failure, and session outcome

Line severity and command outcome are separate. An error-level line can be an
expected handled probe. Conversely, an owning tool can report a failed
required check or corrupt artifact and still be followed by a zero process
exit. `outcome_failure` is true only for an explicit structural signal: a
nonzero process outcome, launch failure, signal termination, or an exact
downstream failure/fatal marker admitted by V1. A mere occurrence of words
such as “error” or “failed” in arbitrary prose is insufficient.

The terminal manifest's `state` and `exit.outcome` are derived monotonically:

- `running` while no terminal process observation exists;
- `cancelled` when cancellation was requested and termination was observed;
- `failed` for launch failure, nonzero exit, signal termination not caused by
  the recorded cancellation, retention failure, or any event with
  `outcome_failure: true`;
- `complete` only after a zero exit, closed streams, complete retention, and
  zero outcome-failure events; and
- `unknown` when terminal state cannot be established.

Once an outcome failure has been retained, a later success-looking message or
zero exit cannot change the session to complete. This rule preserves failure;
it does not establish root cause or replace the owning command's result.

Summary counts must describe all retained events, not only the current filter
or folded rows. Severity, subsystem, and kind totals plus the explicit
outcome-failure and source-locator totals are navigation data. Exact clusters
are rebuilt from the retained event keys rather than asserted by the summary.
The JSON Schema checks shape;
the producer or semantic validator must check counts, contiguous sequences,
event ID uniqueness, locator integrity, state transitions, and outcome
derivation.

## Process groups and cancellation

The launched child owns a new process group. An ordinary cancel request is
sent to that group and retained as a console lifecycle event. V1 allows a
bounded visible escalation only after the ordinary request fails to terminate
the group. The manifest records observed termination rather than assuming the
request succeeded. This prevents the console from knowingly abandoning a
Gradle daemon wrapper, Java process, or Minecraft descendant while claiming
the session stopped cleanly.

The console does not forward wizard, palette, filter, or search input to the
child. Commands that legitimately accept interactive stdin require a
purpose-specific admitted control; V1 never treats arbitrary developer
keystrokes as server-console input.

## Validation and limits

The closed schemas validate individual document shape, vocabulary, timestamps,
safe relative artifact names, path text, and bounded counts. They do not prove
that `byte_end` is at or after `byte_start`, that a range matches its raw bytes,
filesystem permissions, symlink safety, write ordering, child ancestry,
process-group completeness, downstream validation, truthful classification,
summary arithmetic, or authority admission. Producers must enforce those live
and cross-record invariants.

V1 does not instrument runtime event buses, prove listener order or mutation,
map arbitrary obfuscated frames, infer crash causality, identify a root
exception, compare runs, or redact arbitrary child output. It retains one
local process tree and one local observation clock. An interrupted directory
can be useful residue but must not be presented as a completed session without
a valid terminal manifest.
