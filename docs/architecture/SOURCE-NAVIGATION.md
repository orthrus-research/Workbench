# Shared source intelligence

Source navigation uses the selected Work Session from [developer context](DEVELOPER-CONTEXT.md).
It searches recipe, material and BetterQuesting declarations without compiling
Groovy, launching Minecraft or writing state into the pack.

```bash
workbench context run SESSION_ID -- source search water --kind material
workbench context run SESSION_ID -- source inspect SOURCE_SELECTION_ID
workbench context run SESSION_ID -- source related SOURCE_SELECTION_ID --max-depth 2
workbench context run SESSION_ID -- source location SOURCE_SELECTION_ID
```

Copy `selection_id` from a search result. IDs bind the whole source snapshot,
selected profile resources, native interpreter code and shared normalizer code.
They are not line numbers or stable names: even an unrelated source edit requires
a fresh search. `source_declaration_id` describes an individual declaration but
is not accepted as an operation selection. Queries do not add a session journal,
an index database or a global last-selection pointer.

## Ownership

- Project Intelligence captures immutable source bytes in the same bounded,
  two-pass Git observation used by the developer context. Ignored generated
  files are excluded; symlinks, submodules and moving trees fail closed.
- Pack Program Studio owns the declaration contract, Groovy lexer, source
  normalization and exact JSON/source locations. Its analyzer can consume
  captured bytes without reopening the checkout.
- The native `workbench.source_interpreters` profile interface supplies
  Supersymmetry's GT material and BetterQuesting interpretation. Profile code
  does not import Shell or Core. Quest source extraction is independent of
  capture admission and runtime reconciliation.
- Atlas builds a deterministic in-memory read model over those declarations.
  Shell composes the same `run_source_action` for CLI and service callers and
  verifies freshness again before returning. Core remains the generic host.

This is a bounded lexical source view, not a complete Groovy interpreter.
The current profile uses an explicit client-side static analysis context;
loader warnings, lifecycle exclusions and unknown installed-mod conditions
remain visible. A declaration can be present in source without executing.

## Relationships and uncertainty

Materials, fluids, literal items, ore-dictionary selectors, metaitems, recipes,
quests and quest lines retain distinct typed identities. Atlas does not join
them by display-name similarity. A plain material `.liquid()` can declare a
profile-expected default fluid name; custom fluid builders remain unresolved.
This is not evidence of FluidRegistry registration. Material census allocation
and occupied-ID checks remain unchanged and are not replaced by navigation.

Recipes expose literal ingredient and product selectors, quantities, catalyst
and chance annotations. Unsupported expressions are explicit unresolved edges;
ore selectors are not expanded into invented alternatives. Quest requirements,
prerequisites and line membership are source declarations, not player progress
or proof of task completion. Settings outside these declarations remain visible
as unsupported records.

Duplicate definitions remain ambiguous. Missing quest prerequisites are
dangling; referenced items/fluids without local definitions are external-unknown,
not absent from the runtime. Prerequisite cycle witnesses and truncated walks
are reported. `related` traverses both directions through separate declaration
and resource nodes. Bounds are depth 0–8, nodes 1–200, edges 1–1000, with an
additional traversal-work bound. Frontiers explain where exploration stopped.

Observed runtime evidence remains an independent explicit lens through
`workbench atlas recipes ...` over a verified categorical graph. Source
navigation neither attaches the latest capture nor equates a source occurrence
with an observed recipe by name. Existing exact runtime comparison and admission
checks still apply. No source query grants reachability, reload safety, runtime
support, or release qualification.

## Editor handoff

Both IDEs expose **Workbench: Navigate Source Declarations** using an explicit
existing Work Session ID and the same source-only CLI. Select a search result to
open it in the normal editor; no new dashboard or client-side domain parser is involved.
The session must select the current project root. This first handoff requires a
native host; cross-host Windows/WSL mapping is not claimed.

`location` contains a portable relative path, file SHA-256, half-open byte range
and one-based UTF-16 start/end coordinates. The IDE requests a fresh location,
verifies the file digest and coordinates, rejects symlinks and unsaved/stale
buffers, then reveals the range. A referenced resource without a declaration
has no fabricated location. Inspect its definitions or referencing declarations.

The older generic Groovy/ZenScript text-search lens remains explicitly text-only.
Its selections now bind file bytes and exact intervals; old position-only
`source-occurrence` IDs are rejected without a compatibility adapter.

## Next boundary

The next layer is [IDE-first local source review](LOCAL-SOURCE-REVIEW.md).
Developer-authored saved edits are the default input; recipe, material and quest
declarations provide analysis coverage rather than separate construction flows.
Guided authoring and constructor convergence are deferred. The existing recipe
constructor remains add-only; local review can inspect developer-written changes
without authorizing or generating them.
