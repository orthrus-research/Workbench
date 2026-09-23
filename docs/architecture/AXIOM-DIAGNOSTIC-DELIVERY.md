# Axiom diagnostic delivery

A saved material check can present its complete diagnostic evidence before
recipe snapshot publication and indexing finish. Core owns the diagnostic files
inside the existing check attempt. Axiom supplies their meaning, original native
evidence and source bindings; IDE clients read and present them.

After native invocation verification and source attribution, the owner writes
immutable diagnostic pages and publishes a sealed manifest last. The manifest
binds the exact saved request, selection, workspace, producer, native outcome,
coverage and every page's count, size and digest. Partial writes do not advertise
a ready revision. The full retained result references that diagnostic revision;
snapshot finalization checks its complete content against the original evidence.

The delivery view orders located candidate errors first for navigation. Stable
finding identifiers and their original records are preserved. Each page contains
up to 128 complete findings. This is a presentation size, not a limit on captured
findings or individual messages. All pages and original diagnostic records remain
available, including unlocated startup findings.

The existing material-check command exposes these reads:

```text
checks materials delivery ATTEMPT
checks materials diagnostics ATTEMPT --revision REVISION --offset OFFSET
checks materials diagnostic ATTEMPT --revision REVISION --diagnostic FINDING
checks materials source ATTEMPT --revision REVISION --diagnostic FINDING
```

Use them through the selected `workbench context run SESSION --` route.
`delivery` reads request metadata and publication state without recapturing the
program or opening a recipe index. Diagnostic pages check current source
freshness. Source navigation verifies retained source custody and reads the exact
saved bytes. A changed working copy remains distinct from retained evidence.

New prepared requests advertise `workbench-material-diagnostic-view-v1` so IDEs
can enable polling with matching Core versions. Clients retain the existing
execute command while reading diagnostics independently. The view reports native
outcome and observation coverage separately from snapshot detail state:
`preparing`, `ready` or `interrupted`. Opening a diagnostic never implicitly
rebuilds the recipe index. Reopening an interrupted attempt returns its committed
diagnostic view; explicit snapshot operations retain their existing semantics.

Diagnostic files belong to the attempt's existing retention lifecycle, including
known interrupted writes. They are not an unbounded second history store. Native
execution, cancellation, raw capture and full snapshot publication retain their
existing owners. This delivery path does not provide an independently surviving
background service or foreground scheduling priority.

Delivery measurements must separate IDE action, confirmation, diagnostic
publication, receipt, visible presentation, verified source navigation, complete
diagnostic access and snapshot completion. Window captures establish an observed
upper bound on presentation time; model updates alone do not establish paint.
Installed versions, exact source inputs, cache conditions, scripted interactions
and unsuccessful attempts belong in each measurement's evidence. Ordinary
native errors remain errors regardless of transport or presentation timing.

## Persistent IDE results

VS Code presents saved Axiom findings in a context-only **Axiom Results** native
Panel view beside the editor. IntelliJ presents an **Axiom Results** tab in the existing
**Workbench Records** tool window. Source navigation leaves these browsers open.
Both distinguish native outcome, global finding counts, saved-source freshness,
observed scope and recipe-detail readiness. Loaded editor markers are a subset
of retained findings, not the run's total error or warning count.

New diagnostic revisions seal six severity/location groups into their immutable
summary. `diagnostics --group error-unlocated` reads a bounded page directly;
`--offset` advances within that group. `findings_count` remains the global total,
`finding_counts` reports all six groups, and `group_count` gives the selected
group's total. Stable finding identities still address original native evidence.
Historical revisions retain their original seal and use the existing all-findings
reader without invented group counts.

Selecting a located finding can open its byte-verified saved source. Stale or dirty
source offers captured source instead; a native call-site anchor is not a claim
that a particular expression begins on that line. Unlocated errors remain visible
and open their original diagnostic. Recipe details become available separately
when Core finishes publishing the snapshot. Loaded evidence survives read failures;
late pages from another selected run cannot replace the current results.
