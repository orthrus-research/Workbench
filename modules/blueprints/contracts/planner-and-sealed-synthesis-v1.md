# Blueprints planner and sealed-synthesis contract v1

Status: implemented experimental contract

Contract IDs: `BLUEPRINTS-EXECUTABLE-ENGINE-V1` and
`BLUEPRINTS-IMPLEMENTATION-STANDARD-V1`

This contract turns one intake projection, one exact Git target, one
byte-current admitted standards registry, one
validated allocation ledger, and one current evidence bundle into either:

- no plan because no admitted primary standard exists;
- an immutable blocked plan and no candidate; or
- an immutable ready plan plus one private, content-addressed candidate.

Planning does not compile the target, execute tests or runtime fixtures, release
code, generate a patch, mutate a repository, reserve an allocation, or expose
candidate bytes. Those responsibilities remain with the simulation and
release/application stages.

## Implemented records

Planning adds three closed records to the base lifecycle records:

| Record | Schema | Meaning |
| --- | --- | --- |
| target manifest | `blueprints-target-manifest-v1.schema.json` | Exact HEAD, index, worktree, and permitted-untracked state |
| planning evidence | `blueprints-planning-evidence-v1.schema.json` | Target-bound Atlas results, allocation occupancy, and reconciliation classification |
| sealed manifest | `blueprints-sealed-manifest-v1.schema.json` | Private operation bytes behind a local content identity |

The target manifest’s `manifest_sha256` is the SHA-256 of its canonical
path-sorted `entries`. Each entry independently binds HEAD, index, and
worktree SHA-256 identities, nullable HEAD and index modes, its current
Git-compatible worktree mode, kind, and participating layers. Separate modes
preserve staged executable and symlink transitions for exact simulation
reconstruction. The target identity is then recomputed from repository
identity, 40-character HEAD revision, dirty flag, and that manifest digest.

Ignored files and the protected local history root are excluded. Other
untracked files are included. Unmerged index entries, non-UTF-8 paths,
unsupported file kinds, malformed Git records, and target changes during
planning fail closed. Symlinks are recorded as link bytes and are never
followed.

## Canonical intake

`compile_request(intake, target_manifest)` is the shared intake boundary.
Interactive, CLI, and JSON adapters must project exactly these fields:

- sequence;
- feature family;
- operation, target key, and desired outcome;
- developer-supplied parameter rows;
- requested variants;
- output mode; and
- consent.

It normalizes strings to NFC, rejects duplicate parameters and variants,
sorts both set-like lists, imports the verified target projection, enforces
direct-apply consent, and recomputes the request identity. It never inserts
derived, defaulted, allocated, or optional values.

## Evidence integrity

Every planning-evidence bundle binds the exact target state. Atlas query rows
bind standard key, query and expected result identities, availability,
complete result JSON, and a recomputable evidence digest. Allocation rows bind
domain, authority, sorted occupied values, proposed external value, and a
recomputable digest. Reconciliation rows bind the selected standard,
classification, complete observed value, and recomputable digest. The planner
independently requires absent observations to be null and equivalent
observations to equal every standard-declared comparison field.

`atlas.current_id` is recomputed over the target identity, relevant-drift
classification, and complete canonical query rows. Missing, duplicated,
misordered, stale-target, or identity-invalid evidence is rejected before
selection. A required unavailable or failing invariant and relevant Atlas
drift produce a blocked plan.

Atlas reports observed truth; it does not select or approve a standard.
Reconciliation and allocation adapters likewise supply evidence, never
executable rules. Only the admitted standard interprets them.

## Standard and variant selection

The planner recompiles and checks the complete registry, then independently
recomputes every selected standard identity. Active primary candidates must
match both feature family and target repository. Selection ranks admitted
maintainer priority, specificity, and then identity. An equal priority and
specificity rank remains a developer-choice tie; the deterministic provisional
record is blocked unless an explicit tied standard identity is supplied.

Required components are selected automatically. Optional components require
an explicit request. The selected component version must be admitted, active,
allowlisted by the primary, and compatible with the selected primary and
component variants. Component records are sorted by standard identity.

Variant conditions evaluate only after effective parameters exist. Explicit
variant choices must be declared and applicable. Otherwise priority and
specificity choose the applicable variant. Remaining equal-rank ties block.
Component variant IDs are namespaced in the plan as
`<component-standard-key>--<variant-id>`.

## Parameters and compliant revisions

All selected parameter declarations are merged by name. Byte-identical
declarations compose directly. A conflict requires an admitted
`parameter-<name>` precedence surface with one applicable winner; otherwise it
blocks.

The planner:

1. accepts supplied required and optional values;
2. proposes removal of unknown or developer-supplied derived, allocated, and
   defaulted values;
3. applies such a proposal only when `accept_compliant_revision` is true;
4. inserts admitted defaults;
5. makes a provisional allocation;
6. evaluates derived parameters in dependency order; and
7. validates final types and constraints.

The original request is never rewritten. The execution result records each
proposed removal and its acceptance, while the plan records the final value,
class, origin, and acceptance bit. Expression semantics are deterministic:
`literal`, `parameter`, `concat`, Unicode lowercase and uppercase, literal
`replace`, lowercase underscore `slugify`, safe `posix-path-join`, and exact
RFC 6901-style `json-pointer-get`.

For a `blueprints-ledger` domain, active and retired ledger reservations plus
current occupied-value evidence are all unavailable. The planner chooses the lowest
remaining integer in the admitted inclusive pool and records the domain,
authority, evidence identity, and exact ledger identity in the parameter
origin. It never writes the ledger. For `existing-authority`, the content-bound
proposed value must exist and must not be occupied.

## Reconciliation

The evidence classification selects exactly one admitted reconciliation rule.
`create` permits declared creation operations. `approved-update` permits only
a compatible declared update. `equivalent`, `request-new-identity`, and
`reject` are terminal planning results and produce no candidate. Missing or
ambiguous reconciliation evidence blocks.

## Rendering and formatting

Only admitted `rendering.outputs` can create an operation. A render output
must be activated by the selected variants, target the request repository,
evaluate to a normalized non-protected path under its declared target prefix,
and use exactly its declared operation.

Declarative templates are UTF-8 and support only `{{parameter_name}}`
substitution. Invalid delimiters and unavailable parameters block. JSON values
use canonical JSON; booleans are lowercase; null becomes the empty string.
Template bytes are rehashed immediately before use.

Trusted render hooks and pinned formatter commands are supplied through narrow
runner interfaces. A missing runner blocks. Hooks return bytes for their one
declared output. Formatters receive and must return the same path-keyed byte
map within their declared prefixes; adding, removing, or renaming a path
blocks. Simulation remains responsible for process isolation and independently
verifying the formatter binary against its lock.

Each create path must be absent; each update or delete path must exist.
Symlink-bearing target paths block. Duplicate output paths require exactly one
admitted precedence winner for the integration surface.

The entire path, template/hook, and formatter pipeline executes twice from the
same inputs. The ordered operation and byte manifests must be identical.
Operations are then path-sorted, assigned contiguous ordinals, and content
hashed. No blocked plan retains an operation manifest.

## Sealed candidate boundary

Only a ready plan can be sealed. The private manifest binds plan, target,
primary-first standard identities, the exact operation-manifest digest, and
base64 operation bytes. Its canonical bytes are stored in a mode-`0700`
content-addressed root with mode-`0600` objects and atomic rename.

The public candidate record contains only lifecycle metadata and a
`local-cas:sha256:<digest>` locator. It contains no output path, source,
generated byte, patch, or placement instruction. Reads independently verify
the object identity, canonical encoding, every decoded content digest, and the
operation-manifest digest.

Repeating the same request, target, registry, ledger, evidence, selections,
hooks, and formatter must reproduce identical request, plan, candidate,
locator, and sealed bytes. A request, plan, or candidate edit increments the
edit generation and invalidates the old candidate plus every named downstream
identity. Planning records that invalidation projection; later lifecycle
storage is handled by the release/application stage.

Immediately before sealing, the planner recaptures the target and revalidates the
complete registry and ledger authority identity. Any target, standard, lock,
or ledger change rejects the candidate.

## Implementation interface

The implementation is [`planner.py`](../src/workbench_blueprints/planner.py):

```text
capture_target_state(repository, repository_id)
compile_request(intake, target_manifest)
build_planning_evidence(...)
Planner(...).execute(intake, target_manifest, evidence, choices=...)
SealedStore(...).read(locator)
invalidate_candidate(candidate, invalidated_ids=...)
```

The planner has no separate CLI. The public Blueprints lifecycle CLI exposes
this same core without changing its semantics.

## Planning, simulation, and release boundary

A planning `ready` status means only that synthesis inputs and generated bytes are
internally admissible. It is not a simulation pass and is never releasable.
Simulation must place the exact sealed candidate in an exact-revision isolated
worktree and execute every plan gate. Release may expose only that identical
candidate after a passing simulation and unchanged target/authority state.
