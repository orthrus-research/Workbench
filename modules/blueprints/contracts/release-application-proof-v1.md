# Blueprints release, application, and proof contract v1

Status: implemented experimental contract

Contract ID: `BLUEPRINTS-EXECUTABLE-ENGINE-V1`

This contract completes the release boundary defined by the
[executable engine contract](executable-engine-v1.md). It governs how one
fully passing, evidence-bound simulation becomes an exact release; how an
authorized direct release may change the developer target; and what local
history or portable proof may claim afterward.

It does not define CLI argument parsing, admit a feature standard, reserve an
allocation, or prove that a real Supersymmetry feature is usable.

## Release admission

A release is admitted only when all of the following remain true:

- request, plan, candidate, simulation, and run records pass their closed
  schemas and recomputable identity checks;
- the plan, candidate, simulation, and target bindings form one causal chain;
- every required simulation gate exists in plan order and passed;
- the private simulation evidence has the same gate digests, candidate, plan,
  target, and environment-lock digest as the public simulation;
- the current standard registry, trusted assets, and allocation ledger have
  the same authority fingerprint retained by simulation;
- the exact target manifest is unchanged since planning and simulation;
- the sealed operation manifest is byte-for-byte bound to the plan and
  candidate content-manifest digest; and
- no admitted upstream identity has been invalidated.

A failed or unavailable gate, missing evidence object, authority drift,
target drift, sealed-object drift, or invalidation releases no candidate
bytes. Failure records name causal identities and evidence digests, not
private content.

## Exact release bundle

The private sealed candidate is converted into one canonical
`susy-blueprints-release-bundle-v1` object. Each ordered operation contains:

- its ordinal, operation kind, and normalized authorized path;
- exact baseline kind, Git-compatible mode, SHA-256, and base64 bytes for an
  update or delete; and
- exact released kind, mode, SHA-256, and base64 bytes for a create or update.

Only a create lacks baseline content. Only a delete lacks released content.
The operation projection without embedded bytes must equal both the immutable
plan operation list and the candidate `content_manifest_sha256`.

The release manifest binds the candidate and simulation identities to one
operation digest and three projections. Every projection carries that same
digest:

- `instructions`: ordered actions, modes, exact base64 content, and UTF-8 text
  when losslessly decodable;
- `patch-bundle`: the canonical release bundle with exact before and after
  material; and
- `direct-apply`: an exact non-mutating before/after preview with hashes,
  modes, base64 bytes, and optional UTF-8 text.

The selected output mode exposes only its projection. The other projections
exist as deterministic identities so output-mode agreement is testable.

## Application authorization and compare-and-swap

Only a run released as `direct-apply` with explicit
`allow_direct_apply: true` consent may enter application.

Before mutation the application engine:

1. revalidates every request, plan, candidate, run, release, and bundle
   identity and cross-record binding;
2. verifies the bundle digest against the plan, candidate, release, and
   content-addressed locator;
3. recaptures the entire target and compares its `target_state_id` with the
   accepted planning target;
4. rejects every operation outside the plan’s authorized path prefixes;
5. acquires one durable local Blueprints transaction lock;
6. writes and synchronizes a recovery journal; and
7. rechecks each operation’s exact path kind, mode, and baseline bytes while
   refusing symlinked parents, directories, collisions, or missing parents.

A stale target or failed precondition changes no candidate path. The lock
serializes Blueprints transactions. It does not claim to prevent another
process from editing the repository; such interference is detected by exact
post-state validation.

## Logical atomicity and rollback

Released files are fully written, mode-set, and synchronized at private
temporary paths before target mutation begins. Ordered target replacements
use same-directory atomic rename; deletes use exact released paths. Directory
entries are synchronized after mutation.

The transaction is logically atomic:

- if every operation lands and the resulting full target differs from the
  baseline only at the released paths with the released bytes and modes, the
  application is `applied`;
- if mutation or post-state validation fails, every touched path is restored
  in reverse order from the release bundle and the complete target is
  recaptured;
- exact restoration records `rollback: succeeded` and
  `atomic: true`; and
- any restoration mismatch records the critical
  `BPA143_ROLLBACK_FAILED`, sets `atomic: false`, and deliberately retains the
  journal and lock paths for recovery inspection.

“Atomic” here means all released effects commit or the accepted target is
restored exactly. It does not claim multi-file filesystem visibility as one
hardware transaction.

## Verification

Verification is admitted only after a successful direct application.

It always:

- recaptures the entire target and compares it with the application post-state;
- verifies every released create/update byte and mode and every released
  delete;
- runs optional post-checks in deterministic check-ID order;
- converts a post-check exception into failed evidence rather than trusting
  it; and
- recaptures the target after checks so a check that mutates the repository
  fails `verification-side-effect`.

Every check records only status and an evidence digest publicly. One failed
check moves the run to `verification-failed` and prevents direct-application
proof export. A complete pass moves it to `verified` and closes proof over the
exact applied target identity.

## Local content-addressed history

Local history lives under a caller-selected, protected
`.workbench/blueprints/` root outside the captured developer target.
Canonical objects are addressed by SHA-256, written with private modes, and
checked on every read.

A closed `susy-blueprints-history-manifest-v1` records:

- run identity and state;
- all causal request-through-verification record identities available at that
  state;
- logical artifact identity, kind, digest, privacy, retention class, and
  retained state; and
- policy `BLUEPRINTS-LOCAL-RETENTION-V1`.

`compact` records are retained. A `bulky` payload may be pruned explicitly;
its successor manifest retains the digest and marks the payload
`retained: false`. Pruning never turns absence into successful evidence.

Release bundles, sealed candidates, simulation evidence, verification
evidence, transaction journals, and local run records remain local-private
unless a separate export rule explicitly permits their projection.

## Portable proof export

Instruction and patch-bundle releases may export proof after release
validation. Direct-apply runs may export only after target verification.

The closed `susy-blueprints-proof-export-v1` contains:

- an export manifest bound to run identity and closure;
- one recomputable portable proof;
- canonical base64 payloads only for artifacts marked `exportable`; and
- `excludes_private: true`.

The proof names the complete causal identity chain and may retain logical
private-artifact IDs and digests as non-exported evidence references. It never
contains private locators, sealed candidate bytes, simulation command output,
or local paths. The exporter recomputes every included payload digest and
rejects any manifest, artifact, or privacy drift.

Proof closure is exactly one of:

- `release-validated`: a non-mutating instruction or patch release is exact
  and simulation-authorized; or
- `target-verified`: the direct release was applied to and verified against
  the named target state.

Neither closure claims Minecraft runtime behavior beyond the gates actually
listed in the simulation and verification records.

## Stable fail-closed diagnostics

The implementation reserves the `BPA100`–`BPA151` family. Important
causal boundaries include:

- `BPA128_SIMULATION_BINDING` and `BPA129_SIMULATION_EVIDENCE`;
- `BPA130_AUTHORITY_DRIFT` and `BPA135_STALE_TARGET`;
- `BPA137_RELEASE_DRIFT` and `BPA138_STALE_APPLICATION_TARGET`;
- `BPA139_PATH_AUTHORITY` and `BPA140_APPLICATION_COLLISION`;
- `BPA143_ROLLBACK_FAILED` and `BPA144_APPLICATION_ROLLED_BACK`;
- `BPA147_VERIFICATION_FAILED` and `BPA148_EXPORT_NOT_ADMITTED`; and
- `BPA149_EXPORT_ARTIFACT` and `BPA150_EXPORT_MANIFEST`.

Diagnostics are stable machine codes with one location and concise message.
Run errors additionally bind the phase, causal record IDs, and a detail
digest.

## Implementation and test boundary

The deterministic implementation is
[`lifecycle.py`](../src/workbench_blueprints/lifecycle.py). Its conformance
tests are [`test_lifecycle.py`](../tests/test_lifecycle.py).

The tests cover:

- all three release projections and their common operation identity;
- no release after failed simulation, invalidation, stale target, or authority
  drift;
- exact create, update, and delete application;
- stale application and tampered-bundle rejection before mutation;
- partial-application exact rollback and critical rollback mismatch retention;
- target drift, released-content placement, post-check failure, and
  verification side effects;
- compact and bulky history retention; and
- release-validated and target-verified proof export without private payloads.

The public lifecycle command exposes this behavior through the shared core.
The engine conformance fixture exercises the whole engine without admitting a
pack standard. A profile may expose the release path only after its selected
standard declares and passes qualifying gates.
