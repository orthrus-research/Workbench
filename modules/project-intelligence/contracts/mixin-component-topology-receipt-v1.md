# Workbench Mixin component topology receipt v1

Status: experimental product-generic inventory contract

Contract ID:
`WORKBENCH-PROJECT-INTELLIGENCE-MIXIN-COMPONENT-TOPOLOGY-RECEIPT-V1`

Machine-readable artifact:
[Mixin component topology receipt schema v1](../schemas/mixin-component-topology-receipt-v1.schema.json).

## Purpose and authority

This receipt binds exact artifact bytes to the logical Mixin and transformer
components discovered inside them, then records configuration, registration,
target, compatibility-epoch, and bounded diagnostic relationships. It answers
what was found, where it came from, and which relationships were declared or
observed. It does not by itself prove that a transformation completed or that
transformed state survived into a final game record.

Project Intelligence owns this inventory boundary. Atlas remains the authority
for interpreted knowledge and causal answers. Crucible remains the authority
for controlled runtime captures and their completeness. Blueprints may consume
a validated receipt as evidence without making the receipt an approval path.

## Exact identity

`receipt_id` has the form
`workbench-mixin-topology-receipt:sha256:<lowercase-hex>`. Its digest is SHA-256
over canonical JSON for the entire receipt except `receipt_id`. Canonical JSON
uses sorted object keys, UTF-8, no insignificant whitespace, integer numeric
values only, and no trailing newline. Array order is retained.

Every artifact, component, configuration, registration, mixin binding,
compatibility epoch, evidence record, and finding uses the same rule with its
own schema-declared prefix and with only its own ID field omitted. Producers
must sort top-level arrays by their ID field before publication. Version claims
are sorted by `(namespace, value, state)` with `null` before strings; ID arrays
are sorted lexicographically.

An artifact record is admitted only for exact bytes and therefore always
retains byte size and SHA-256. Published coordinates, manifest versions, source
revisions, and repository metadata are separate claims backed by evidence;
they cannot replace the artifact digest. One artifact can contain multiple
version axes, such as a distribution version, embedded framework version, and
ASM version, without collapsing them into one value.

A component is bound to one exact artifact. `exact-entry` additionally binds
the contained entry path and entry-byte SHA-256. `artifact-bound` means the
logical component is known to be contained by the exact artifact but no
independent entry digest was admitted. `declared-only` and `unresolved` remain
weaker states and must not be upgraded from a package or class-name guess.

## Candidate-lock V1 preservation

The optional `scope.candidate_lock_v1` object is a reference to the unchanged
`workbench-cleanroom-candidate-lock-v1` document. It repeats only:

- the exact `candidate_id`;
- format `workbench-cleanroom-candidate-lock-v1`;
- schema version `1`;
- the complete candidate-lock file SHA-256; and
- a non-authoritative locator.

The candidate-lock bytes remain authoritative for the fields already present
in V1. This receipt must not insert component inventory, CleanMix versions, or
derived topology into that document, and it must not reinterpret a component
receipt as a new candidate maturity decision. Changing candidate-lock bytes
creates a new binding digest. Supporting another lock format requires a new
versioned receipt schema rather than silently broadening this field.

`platform-candidate` and `runtime-session` scopes require a non-null V1 binding.
`project-build` and `offline-artifact-set` scopes may use `null` when no
candidate applies.

## Configuration and registration topology

Configuration records retain the exact resource state and digest when
available; owner resolution state; declared phase; required flag; priorities;
package; minimum runtime version; required features; plugin; refmap; and the
registrations that introduced the config. Null means explicitly unresolved or
not declared according to the adjacent state, never an inferred default.

Registration records are typed directed edges. Their declaration state and
runtime state are independent:

- `declared-exact` proves an exact manifest, service file, config resource, or
  programmatic registration record was found;
- `discovered` proves bounded discovery but not loading;
- `loaded`, `active`, `inactive`, and `failed` require runtime observation;
- `not-observed` is not evidence that registration did not occur; and
- `unresolved` never selects an endpoint or phase by convention.

Mixin bindings represent one mixin/config/target relationship. The
`application_state` distinguishes declaration, eligibility, preparation,
application entry, completed application, skip, and failure. An apply-entry
log or callback is `apply-entered`, not `applied`. `applied` requires an exact
completion observation admitted under a runtime policy. Priority and ordinal
are retained separately because declared priority does not prove runtime chain
order.

All referenced IDs must exist in the corresponding receipt arrays. IDs are
unique per array. Summary counts must equal array lengths. These referential,
ordering, and count constraints are normative even where JSON Schema cannot
express them.

### Semantic validation beyond JSON Schema

Before publication, a semantic validator must:

- recompute the receipt and child-record content addresses from canonical JSON,
  and recompute artifact, resource, entry, and exact-evidence digests when their
  bytes are available;
- verify candidate-lock bytes against the repeated file digest, format, schema
  version, and candidate ID without changing those bytes;
- enforce ID uniqueness, reference existence and endpoint type, required array
  ordering, and policy-defined input/artifact/configuration set digests;
- make summary counts equal their arrays and count unresolved or contradicted
  records according to the declared producer policy; and
- require admitted evidence of the appropriate truth scope for exact findings
  and runtime states, including a completion observation before `applied`.

A structurally valid document that fails one of these checks is not a validated
receipt.

The offline scanner V1 producer counts one unresolved condition per retained
finding whose kind is `compatibility`, `conflict`, `packaging`, or
`unresolved`. A finding may summarize several invalid entries, so the count is
the number of independently addressable finding records, not a hidden count
parsed from prose. Its validator also recomputes the artifact/configuration set
digests and requires the exact scanner tool, version, policy ID, and policy
digest before admitting such a receipt.

## Compatibility epochs

Compatibility-epoch records keep distribution version separate from behavior
selection. They can describe artifact, configuration, mixin-class, or member
scope and distinguish:

- exact metadata;
- a member override;
- inheritance from a containing mixin;
- an annotation-processor default;
- a runtime fallback caused by absent metadata;
- invalid or absent metadata;
- a positive not-applicable result; and
- unresolved state.

`declared_epoch`, `effective_epoch`, `latest_supported_epoch`, and
`fallback_epoch` are independent nullable values. A producer must materialize
the values it used rather than derive them later from the artifact version.
For example, a framework release may retain an earlier latest behavior epoch,
and absent metadata may deliberately select an older fallback. The evidence
record must bind the metadata resource or the bounded observation establishing
absence.

## Evidence and findings

Evidence records separate collection, admission, and truth scope:

- collection: `exact`, `observed`, `derived`, `partial`, `missing`, or
  `rejected`;
- admission: `admitted`, `supporting-only`, or `rejected`; and
- truth scope: artifact identity, static declaration, runtime occurrence,
  comparison-only, or diagnostic-only.

Exact evidence requires a byte digest and size. Missing evidence has neither.
Logs and intermediate bytecode are always `supporting-only` and
`diagnostic-only`. They can locate a problem or support a later experiment,
but they cannot authorize final topology or causal truth. Content addressing
detects byte changes; it is not a signature or proof of trustworthy origin.

Findings are content-addressed statements with explicit evidence, subjects,
state, severity, and limitations. `observed-exact` is exact only within the
declared evidence truth scope. `derived-bounded` must name its limits.
`declared-only`, `unresolved`, `contradicted`, and `rejected` cannot be promoted
by prose. A finding with no admitted evidence is invalid.

## Publication and boundaries

`summary.receipt_state` is `complete` only when every required input for the
declared scan policy was admitted and all open conditions are counted. It does
not mean the runtime was completely transformed or observed. `incomplete` is a
valid retained result; `rejected` means the receipt cannot be consumed as
admitted topology.

The required `boundaries` constants make the following invariants
machine-visible:

- candidate-lock V1 is an exact external binding and is not mutated;
- a static declaration does not prove runtime application;
- application entry does not prove completion;
- logs and intermediate class images are not final truth;
- a content address is not a signature; and
- Atlas remains the interpretation authority.

Generated receipts and supporting captures are operational evidence and stay
under ignored `.workbench/` storage or another declared external evidence
store. Exact platform-specific expectations, service implementation details,
and supported candidate decisions belong in `profiles/`, not this generic
contract.
