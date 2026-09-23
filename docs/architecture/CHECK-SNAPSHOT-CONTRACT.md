# Retained check snapshot and read contract V1

Status: schemas, validators, Core publication, chunked reads, archive export and
derived-index rebuilding are implemented, with historical contract resolution and
explicit retained comparison. Material-check history and exact-source
navigation use snapshot reads. Material CLI and IDE responses use summary-first
views, progressive findings and selected-record queries. Core also provides explicitly enabled finite history retention. Native validity and
installed workflow qualification remain separate. The [Axiom MVP](../../modules/axiom/spec/initialization-mvp.md)
defines the first consumer's required behavior.

## Ownership and authority

Core owns capture, durable publication, workspace authorization, resource access,
process/read leases, retention, cancellation and reclamation. The selected profile
and producer provide the exact applicable observation contract. Axiom owns native
recipe meaning; clients render existing workflows. A query index is a disposable
derived artifact and cannot replace the complete retained result or saved inputs.

The [snapshot schema](../../core/src/workbench_core/schemas/workbench-check-snapshot-v1.schema.json),
[query schema](../../core/src/workbench_core/schemas/workbench-check-snapshot-query-v1.schema.json),
and [response schema](../../core/src/workbench_core/schemas/workbench-check-snapshot-response-v1.schema.json)
have closed versioned envelopes. Domain section payloads retain their own schemas.
The [pure validators](../../core/src/workbench_core/check_snapshot_contract.py)
perform no filesystem access to evidence, native execution or storage mutation.
Their success is not proof that payloads exist, that metadata is truthful, or that
the caller may read the resource. Publication/access must verify those conditions.

## Snapshot identity and content

`id` is the existing Core `seal("check-snapshot", body)` identity, excluding `id`
from the body. Its encoding is Python-compatible recursively key-sorted JSON,
ASCII-escaped strings, compact separators, finite JSON numbers and no trailing
newline, encoded as UTF-8. This reuses `check_storage.canonical`; it is not RFC 8785
or a new global canonicalization rule. Array order is preserved. The archive digest
separately binds the original result's exact bytes, including its original encoding.

The envelope records:

- Original attempt/request/result IDs, producer component and exact producer-build
  digest. A new run has its own identity even if all observed content is reused.
- Digests of owner-verified source, configuration, context, engine, runtime and JVM
  binding records. A release name or executable launcher hash alone is insufficient.
  The context binding includes selected profiles, side, source/artifact locks,
  admission/observation contracts and relevant configuration policy. Source and
  configuration bindings identify complete captured inventories; native acknowledgement
  and actual configuration application remain Axiom's separate evidence obligations.
- `source_result` describing the complete original result, and ordered unique
  `retained_inputs` roles describing required captured input resources. Each content
  descriptor has decoded `sha256`, exact byte length and media type. These are content
  identities, not arbitrary paths or download instructions. The publisher must verify
  the input-role requirements against the retained request; schema validity alone
  cannot prove that every required input was declared.
- `scope_id`, `native_outcome`, `coverage` and ordered logical section descriptors.
  `native-failed` with complete observation coverage is legitimate. Neither coverage
  nor transport completion means a native validity pass.

Codec, encoded-file digest, path, index generation, pin state and expiry belong to
separately bound Core representation/lifecycle records. Recompression, index rebuild
and pinning do not rewrite the original manifest. Retiring the payload updates
availability with an operation receipt; it does not rewrite the original outcome.
Only publish after the required content and references are durable and verified.
Earlier failed/cancelled attempts remain explicit retained attempts; missing inputs
must not be replaced with invented binding digests merely to produce this envelope.

## Expected scope and section states

`scope_id` is `seal("check-snapshot-scope", scope)["id"]`, where `scope` contains
`name` and an exact mapping from logical section IDs to required booleans. Core
obtains this contract independently from the verified selected profile/producer;
the observer cannot shrink its own obligation by returning fewer sections. The
manifest must account for every declared section once, with matching requirements.

Each section has `id`, `schema`, `required`, `state`, `count`, `content`,
`dependencies` and `reason`. Its ID names a logical observation, not a source file
or implementation class. The supported states are:

| State | Contract |
| --- | --- |
| `observed` | Payload and exact logical-record count present; no missing-evidence reason; all declared dependencies observed. A zero count still requires an observed empty payload. |
| `incomplete` | Explicit reason; any partial payload/count remains bound, never treated as complete. |
| `unavailable` | Explicit reason; absence of payload cannot establish a zero count. Failure evidence may be retained without claiming observation. |
| `not-applicable` | Optional in the selected scope, with a reason and no payload, count or dependencies. |

Coverage is complete only when every required section is observed. Unknown schema
support is a separate reader decision: retained optional unknown sections remain
visible, and an unknown required schema or dependency prevents complete interpretation.
Dependencies may contain cycles; native aliasing/cycles must not be flattened away.
The contract validator checks declared dependency closure, not each payload's native
references. The publisher and section reader must verify actual graph closure,
ordering, duplicates, typed values, record count and content hashes.

## Read requests and cursors

### Optional domain consumers

`workbench_api.retained_snapshots` defines a narrow reader and injected producer
admission port. Core 0.1.4 supplies
`workbench_core.retained_snapshots.open_retained_snapshot`. The caller selects an
exact retained attempt, owner and optional snapshot/context constraints. Under a
read lease, Core verifies custody and its registered record when present, payload
hashes, retained inputs and the decoded archive identity. The owner validates its
historical request/program/intent contract using Core's exact saved-source reader;
added, missing, modified or mode-changed source files are refused.

The adapter must separately admit its optional installed/enabled producer. API
`require_optional_distribution` respects Core's active unavailable-distribution
policy before loading that integration. Snapshot fields cannot select executable
admission code. Unsupported scope/schema status remains visible and cannot be
turned into complete interpretation.

The resulting reader exposes the admitted request, original manifest, custody,
scope/schema availability and existing record/query operations. It does not
publish, rebuild, restore or execute native state. Axiom 0.1.1 provides its
historical policy without importing Core, Shell, Project Intelligence or Atlas.
Atlas's profile adapter composes these ports to import retained observations and
resolve their exact original evidence; Atlas's base query package remains independent.

### Paged reads

Every request binds `snapshot_id`, `view_id` (client generation), operation,
nullable addressing fields, `preferred_bytes` and nullable `cursor`. View identity
is a stale-response guard, not authorization. Section IDs and record keys are
logical addresses; they never resolve directly to filesystem paths.

| Operation | Address | Ready payload |
| --- | --- | --- |
| `summary` | No section/key/blob/cursor | Original scoped outcome, coverage, supported diagnostic references and retained input/producer identity; no full native document. |
| `sections` | No section/key/blob; cursor optional | `sections`, exact `total`, and starting `offset`, ordered as the manifest. |
| `records` | Section ID; cursor optional | `records`, exact section `total`, and starting `offset` in the section's declared order. Each row includes its key and either complete value or a content reference. |
| `record` | Section ID and opaque record key; no cursor | Key and either a complete value or `{sha256, bytes, media_type}` content reference. Native references remain scoped to this snapshot. |
| `blob` | Section ID, record key, expected content digest; cursor optional | Base64 `data`, decoded starting `offset`, `total_bytes`, whole-content digest and decoded chunk digest. |

For a record value exceeding the negotiated presentation preference, return a
content reference and use `blob` ranges. Do not split native semantics into a
silently truncated record. Each returned chunk must verify against its chunk
digest, record binding and declared byte range; concatenated complete content must
match the whole digest. No arbitrary content-store hash lookup is granted by a blob
request. Full original-result/input archive export remains a separate Core operation.

`preferred_bytes` is a client transport preference, not a retained-result or native
execution ceiling. Producers must account for JSON/base64 overhead and use references
when a value cannot fit; a preference too small for the envelope returns an explicit
read limitation. The complete source/result is preserved independently. Large scalar
values must be chunkable without requiring one oversized SQL BLOB or client string.

`query_id` is `seal("check-snapshot-query", request_without_cursor)["id"]`.
A cursor contains this ID, the snapshot ID and the next offset: logical records for
`sections`/`records`, decoded bytes for `blob`. It binds the query, preference and
client generation; changing any requires a new query. The executor additionally
checks range bounds, exact counts, order and membership. IDs are deterministic
bindings, not secrets or permission tokens. Cancelled reads stop promptly and release
read leases; they do not cancel or alter the original native observation.

## Read responses and freshness

Responses echo snapshot, query and view IDs plus a `request_id` identifying this
individual read: `seal("check-snapshot-read", complete_request)["id"]`, including
its cursor. This distinguishes late replies to different pages of the same query.
Responses contain `state`, `payload`,
`complete`, `next_cursor` and `reason`. `ready` requires a payload; other states
(`unavailable`, `unsupported`, `expired`, `cancelled`, `incomplete`) require an
explicit reason, null payload/cursor and `complete: false`. Previously received
pages may remain visible as partial; an unavailable page cannot become empty data.

`complete` describes the requested read stream only. A ready terminal page has
no next cursor; an intermediate page advances the cursor within the same query.
Operation-specific producers/readers must validate total/count/range completion;
the envelope validator checks identity and cursor consistency only. No returned
count or transport flag promotes the native outcome or supported scope.

The IDE may retain an old completed result while a fresh check proceeds, labeled
with its original source and pack. It must discard late replies to replaced view
generations and only apply source markers when the existing exact-source rules
permit. A missing/expired archive retains an honest history entry; a fresh run is
new evidence, not reconstruction of the deleted historical observation.

## Evolution, retention and implementation boundary

The initial derived layout decision is one SQLite database per snapshot containing
an index of logical records and independently compressed pages in a separate table.
The original complete result remains a separate losslessly compressed artifact.
This combines a single database lifecycle with page compression; it is not a shared
multi-run database or a new archive authority. Physical indexes, page sizes/codecs,
byte-range fragmenting and full integrity/custody checks belong to the publication
implementation and its versioned derived-layout contract. Large values must span
independently addressable chunks instead of relying on unlimited SQL value sizes.

The [Core store](../../core/src/workbench_core/check_snapshots.py) publishes only
after the owner verifies request/source/native relationships, the complete archive
round-trips, and section content/counts and references verify. It moves prepared
state into visibility under a Core lease and retains operation journals. Read
leases protect selected generations; rebuilding refuses while a reader/writer is
active. Interrupted unselected generations remain identifiable in recovery results.
Unknown staging content refuses cleanup. No automatic history policy is enabled.

The [page index](../../core/src/workbench_core/check_snapshot_index.py) encodes the
complete JSON value once and locates section records by byte ranges. Mapping keys
use `key:sha256:<digest of Core canonical JSON key>`; array records use `item:N` and
single-value sections use `value`. Empty and large mapping keys remain addressable.
Index JSON preserves object insertion order and array order, uses ASCII escapes
and finite JSON numbers, and has no trailing newline. It is a derived encoding;
the separate archive preserves the original byte encoding exactly.

Index pages use 128 KiB decoded chunks; this is a physical representation choice.
Large individual strings, keys and containers span pages. A verified read opens
metadata and checks file identities, then validates each requested page and range.
Routine queries do not parse the complete archive. Explicit archive export and
owner-side `read_record` may retrieve full content; client queries use preferences
and references. Cancelling a query closes its read lease.

New material executions retain the existing `result.json` alongside archive/index
storage during the CLI/owner-reference transition. This adds the raw result's
allocation to the snapshot footprint; it is not the archive-plus-index footprint
alone. Existing raw results are not deleted by publication. Historical raw results
can be explicitly imported through the owner API. Missing raw compatibility data
cannot be reported as an unexecuted check when a complete snapshot exists.

New sections and changed interpretation require explicit selected contracts.
Removing a feature can make a section inapplicable; a broken observer cannot.
Refactors record a new producer build while unchanged section meaning may retain
its schema. Index/comparison versions evolve independently. Physical byte reuse
does not prove semantic equivalence across native graph IDs or pack versions.

Core must preserve required/pinned/active/recovery references during retirement,
account for derived indexes and trash, and distinguish estimated reclaimability
from actual purge effects. Missing or unsupported ownership/schema information
does not authorize deletion. Storage lifecycle changes extend the existing
[manager](DISPOSABLE-RUNTIME-WORLD-MANAGER.md), not this read-only contract.

[Synthetic scope](../../core/tests/fixtures/check-snapshot-v1/scope.json),
[manifest](../../core/tests/fixtures/check-snapshot-v1/manifest.json),
[query](../../core/tests/fixtures/check-snapshot-v1/query.json), and
[response](../../core/tests/fixtures/check-snapshot-v1/response.json) are executable
contract examples with fixture identities. They are not native observations or
publication receipts. Contract and persistence tests cover contradictory coverage,
stale cursors, dependent schemas, complete content, large-value chunks, missing or
corrupt evidence, cancellation and interrupted writes/rebuilds. Full client result
presentation and retention have separate acceptance obligations; reader evolution
is specified below and does not establish native support for another pack version.

## Material CLI and IDE views

`checks materials show`, `execute` and `run` return
`workbench-material-check-view-v1` after execution. The view binds the original
result/request/candidate, selected context, sealed snapshot and a fresh view ID.
It includes a fixed native outcome projection, section availability, exact total
finding count and a first findings page. `detail_state: not-loaded` never means
that the native evidence is empty. Prepared requests retain their existing format.
Cancelled ingestion can return a retained incomplete attempt without a snapshot;
reopening it may explicitly finish derived publication from the original result.

`checks materials query ATTEMPT --query JSON` accepts the Core query contract.
Findings pages carry presentation labels separately from original evidence.
Compiler labels use the original compiler message at its exact native location;
long labels are marked previews. Original diagnostics and cause graphs stay intact.
Both IDEs offer progressive findings, section/record browsing and selected native
diagnostic reads. A large individual record stays a content reference in the IDE;
its complete JSON can be streamed to a user-selected file through Core.

`checks materials export ATTEMPT --snapshot ID --destination /absolute/result.json`
exports the original complete bytes. Adding `--section ID --key KEY --sha256 HASH`
exports one complete canonical record verified against its selected content hash.
Destinations must be new files. Export does not reexecute native initialization.

While a fresh check runs, the existing IDE workflow can display the previous
completed check with its original source/context identity. It clears old markers,
binds detail replies to the selected view and verifies saved source before adding
new markers. Unknown historical overview versions remain explicitly unsupported;
their original scoped outcome, indexed evidence and full export remain separate.

## Historical readers and comparison applicability

Historical reads resolve Axiom's retained V1 scope from the verified saved request
and the manifest's exact scope identity, independently of current producer
declarations. An unknown scope permits envelope inspection and complete original
export but refuses interpreted record reads and index rebuilding. Unknown section
schemas and their dependent sections return `unsupported` through the material
query route. The original native outcome and capture coverage remain unchanged.
The view's separate `interpretation` reports supported scope, unsupported/affected
sections and whether all required observations can currently be interpreted.
An unknown optional section does not block unrelated required observations.

The finite reader support policy is:

| Contract | Named consumers and support |
| --- | --- |
| Snapshot/publication/query/response V1 | Core and material CLI/Shell history; current persisted envelope. Unknown envelope versions refuse validation. |
| Axiom retained material scope V1 and its named section V1 contracts | Material CLI and both IDEs, including paired baseline/candidate sections. Other contracts require explicit admission. |
| Index record V1 and V2, JSON page layout V1 | Core historical reads. Rebuild writes V2 with exact source-result, section-view digest, converter contract and builder digest. |
| Axiom overview V1 and derived overview V2 | CLI, VS Code and IntelliJ summaries. V1 producer output remains unchanged; V2 is a disposable interpretation explicitly labeled as partial original native outcomes. |
| Section comparison V1 and stored crafting graph V1 | Explicit material retained comparisons; not native qualification or universal semantic equality. |

This is an enumerated support set, not an implicit promise to read every past or
future version. Removing a supported reader requires an explicit product contract
change and export/refusal regressions for the affected history consumers. It must
not require a legacy native runtime, silently rewrite original evidence, or delete
unsupported history. Raw original results remain the authority over derived views.
An unsupported envelope can be retained as bytes, but the current owner workflow
does not claim it can interpret or export a future envelope contract it cannot validate.

Derived overview bindings record snapshot identity, input contract and digest,
output contract and digest, and converter identity/build. Missing historical fields
return `incomplete`; no converter supplies guessed native outcomes. Index rebuilds
preserve manifest bytes and check reconstructed sections against that manifest.
The V2 index adds input bindings without changing observation or page schemas.

`checks materials compare BEFORE AFTER` opens two owner-authorized read leases.
Its section comparison binds both snapshot IDs, supported reader contracts and
comparator build. It reports `comparable`, `scope_changes`, and `incompatible`
separately. Section additions/removals, applicability and requiredness changes
are scope changes under supported scopes; failed observations are incompatible
evidence, not removals or zero counts. Split/merged sections currently have no
cross-contract migration: they report added/removed scope rather than fabricate
a one-to-one comparison. New meaning requires an explicit observation contract.

An unchanged stored section requires equal exact content/count and compatible,
unchanged transitive dependency content. Physical byte sharing, producer build
changes and changed context/runtime/JVM bindings are reported independently of
representation equality. Different representation need not mean different native
behavior, particularly when snapshot-local IDs change.

Adding `--crafting-key supersymmetry:string.cotton` compares one supported stored
crafting graph, resolving values within each snapshot. The comparison preserves
types, sequence order/multiplicity, cycles and alias relationships. Matching local
IDs cannot hide a changed referenced child; consistent renumbering alone can still
describe the same graph. Missing references or different field sets are explicitly
incompatible. This comparison concerns captured graph structure and values; it
does not establish native support across versions or replace native effect receipts.


## Manual check storage lifecycle

Core registers new published material checks as one retirement unit containing
complete evidence, saved inputs and source navigation files. A sealed custody
manifest is retained both with the attempt and in Core's protected storage ledger.
Publication, native results and earlier operation receipts remain unchanged.
Existing captures can be registered after their owner verifies their saved inputs:
`workbench context run -- checks materials register ATTEMPT`.

Use the existing storage workflow independently of the active project, Axiom
installation or profile selection:

```text
workbench storage --checks history
workbench storage --checks list --json
workbench storage --checks inspect ITEM_ID --json
workbench storage --checks pin ATTEMPT --reason "required investigation evidence"
workbench storage --checks unpin ATTEMPT --reason "required investigation evidence"
workbench storage --checks preview-group ITEM_ID OTHER_ITEM_ID
workbench storage --checks cleanup ITEM_ID --json
workbench storage --checks cleanup ITEM_ID --apply
workbench storage --checks restore TRASH_ITEM_ID --apply
workbench storage --checks export-check ATTEMPT --destination /absolute/new/bundle
workbench storage verify-bundle /absolute/new/bundle
workbench storage --checks purge TRASH_ITEM_ID --confirm TRASH_RESOURCE_ID
workbench storage --checks reconcile-checks
```

`--checks` selects the shared Core product-state check store across all contexts.
`--checks-root /absolute/previous/developer-checks` selects an explicitly retained
custom store. A scan reports its coverage and does not imply discovery of arbitrary
external directories. Historical state is retained, retired, expired or unavailable;
missing files alone never prove intentional expiry. The saved-check history lists
retired/expired entries with their original result summary. Detail reads explain
recovery through storage rather than presenting an unexecuted request.

Inventory exposes logical, allocated, protected and eligible storage, ownership,
required references, consumers, explicit pin reasons, scan time and coverage gaps.
History breaks out evidence, saved inputs, query indexes, temporary projections
and maintenance files. Group previews recompute the selected inode union, including
hardlinks shared across items. They are previews, not batch deletion. Allocation is
not an estimate of exclusive physical extents on reflink/compressed filesystems.

The store lease precedes the attempt lease. Readers and publishers share access;
collection and pin changes require exclusive access. Leases survive attempt renames.
Unknown ownership, unsupported envelopes, unsafe paths, active use, explicit pins
and required consumers refuse reclamation. Missing registry rows preserve references
from supported custody manifests and require reconciliation before collection.
An explicit empty pin record identifies unpinned history; missing pin authority
blocks collection and is never rebuilt as an empty pin list.
Trash holds its bytes and dependency references until purge; pinned or referenced
trash can be restored safely without releasing its protections.

An inspectable bundle contains the full original JSON archive, publication,
declared saved inputs and complete saved source files, plus required local dependency
closures. Export verifies every declared byte and the decoded original result before
publishing the bundle; failed exports retain a visibly partial directory. Purge
requires both the exact trash resource ID and revalidation of a local bundle bound
to this custody record. A path, URL, stale export receipt or missing export payload
is insufficient. The bundle identifies absent native engine/runtime/JVM prerequisites
and does not claim exact-input native reproduction. Remote synchronization, remote
archive retirement and deletion outside Core's selected store are separate actions.

Purge receipts report file allocation actually unlinked. A separate immutable
reclamation observation records filesystem free-space movement, which can differ
due to concurrent activity and filesystem accounting. Interrupted cleanup remains
restore-only. Interrupted purge retains its failed operation receipt and the verified
bundle; changed residual trash is protected, not falsely reported fully restored or
expired. Automatic expiry requires the separate policy authority described below.

Reconciliation rebuilds registry rows only from supported retained custody manifests;
it cannot reconstruct missing evidence. It preserves pins and original receipts,
reconciles recognized publication staging, and moves verified unselected query-index
generations into existing managed cache for explicit cleanup. A durable intent binds
both index locations so an interrupted move can be reconciled. Selected indexes,
unknown generations and source evidence are preserved. Raw compatibility results
remain part of the whole-attempt footprint until that attempt is explicitly retired.


## Finite history policy

Core's `workbench-check-retention-policy-v1` is disabled until a user reviews an
exact versioned proposal and confirms its digest. Changing scope or preferences
invalidates an older proposal. Routine operations under the enabled policy bind a
`workbench-check-retention-authorization-v1` to the current policy and an exact
storage-manager plan. Manual cleanup/restore/purge keep their existing semantics.

Use `workbench storage --checks retention status` for settings, total allocation,
known stores, per-volume free space, protections and the last maintenance result.
`preview` is read only. `configure --settings JSON` returns the disclosure and
proposal; repeat the same settings with `--confirm PROPOSAL_ID` to apply.
`maintain` runs already-authorized maintenance. An explicit historical store uses
`storage --checks-root /absolute/store retention ...`. Both IDEs expose these
controls from **Reopen retained material check → Storage and retention**. Context
selection warns about observed low headroom or overage before preparing more work.
Maintenance also runs after native execution releases its reader lease; the result
being returned remains temporarily protected. A deferred cleanup never changes its
original native outcome.

The initial editable recommendation is **10 live checks, 8 GiB allocated per store,
one day minimum age, seven days recoverable trash, 128 recent expiry summaries**.
Three complete native baseline/error/correction attempts measured about 544 MiB
each, including 298–299 MiB raw compatibility JSON, the archive, query index and
saved inputs. Ten similar captures occupy about 5.32 GiB; 8 GiB allows additional
history overhead. This is a starting preference, not a bound on a different pack,
run frequency or result. Grace, pinned data and active contexts may exceed it.
Execution and output targets remain suspended; these settings never truncate a
new capture. `keep-everything` explicitly allows storage growth.

The complete settings object has `mode` (`finite`, `keep-everything`, `disabled`),
`max_count`, `max_bytes`, `min_age_days`, `trash_days`, `metadata_count`,
`active_contexts` and `known_stores`. Counts/bytes are positive integers; ages are
nonnegative whole days. Status supplies exact active-context keys. Only explicitly
active contexts protect their latest useful retained result, including a native
failure. Pins and required references remain authoritative. Store leases protect
readers, publication and cleanup across processes. Other known stores are counted,
not granted deletion authority; overlapping, unsafe and inaccessible locations
remain coverage gaps. Policies apply individually to each selected store. Unknown
files, unsupported formats and incomplete operations remain protected exceptions.

Core first reconciles and removes admitted unused query indexes. Eligible checks
then move whole into recoverable trash. A manual restore restarts age eligibility.
After the configured grace, Core verifies a temporary local evidence bundle before
permanent release and removes that temporary bundle through the same exact-item
manager. User-selected external exports remain untouched. Partial exports retain
the original trash and can be retried after validation; partial purges remain
protected for inspection. Temporary bundles are recovery transaction data, not a
promise of recovery after permanent expiry. Inspectable evidence does not imply
that absent native engine/runtime/JVM dependencies can be reproduced.

Completed custody and operation rows become compact expiry summaries only when
proof and recovery duties end. Old expiry summaries and obsolete successful or
reconciled derived-operation journals are retired in one small transaction; a
bounded aggregate retains expired/forgotten counts. Individual history older than
that window is unavailable, never evidence that a run did not occur. Pending,
failed or referenced metadata is retained with its outstanding obligations. No
history recursion, all-pairs copies or large database rewrite is required.

Maintenance reserves small transaction headroom and checks space for each complete
export. It defers honestly when recovery space is unavailable. Actual disk-write
failure retains the incomplete operation and complete source where available; it
never certifies a partial export or truncated new result. Allocation unlinked and
filesystem free-space movement are reported separately. Cross-store hardlinks,
reflinks and compressed filesystem extent sharing are not inferred from inode
accounting. Known-store totals are coverage information, not a machine-wide scan.
