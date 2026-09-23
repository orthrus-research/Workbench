# Cleanroom Mixin Doctor report V1

Status: experimental exact-profile contract.

`workbench-cleanroom-mixin-doctor-report-v1` applies the bound Cleanroom
policy to the exact archive bytes represented by a Project Intelligence Mixin
topology receipt. It is a deterministic static admission report, not a runtime
success receipt.

## Inputs and identity

The producer MUST bind:

- the exact SHA-256 of the policy file;
- the content-addressed topology receipt and scope IDs;
- every exact artifact SHA-256 and label through each matched finding; and
- every policy rule through one rule-coverage row.

The policy SHA-256 identifies the exact policy file bytes. A parsed policy
mapping cannot independently reproduce that byte identity because JSON
whitespace and member order are lost; callers that bypass the file-reading
entry point MUST supply the digest of the exact bytes they parsed.
Policy loading MUST reject duplicate JSON object keys instead of accepting a
parser-specific last-key-wins interpretation under the bound byte digest.

The report ID is `workbench-cleanroom-mixin-doctor-report:sha256:` followed by
the SHA-256 of canonical JSON for every top-level member except `report_id`.
Finding IDs use the same construction with the prefix
`workbench-cleanroom-mixin-doctor-finding:sha256:`.

## Rule coverage

Every rule in the bound policy appears exactly once in `coverage`:

- `evaluated` means the admitted static facts were sufficient to decide the
  condition;
- `partially-evaluated` means the condition was decided only for a subset of
  relevant subjects; and
- `not-evaluated` means the required fact is outside this scanner's custody.

Any partial or unavailable rule forces at least a `review` disposition. A
matched `reject` rule dominates every other disposition. A report may say
`accept` only when all policy rules were evaluated and no review or reject rule
matched.

Some malformed inputs are rejected while constructing the topology receipt,
before a Doctor report can be published. Successful scanner admission is the
evidence for the corresponding malformed-input rule not matching.

## Exact CleanMix configuration semantics

`minVersion` is parsed as Sponge `VersionNumber`: one through four numeric
parts, each one through five digits and in the inclusive range 0 through
32767, followed optionally by a hyphen-prefixed suffix. Missing parts compare
as zero and suffixes do not affect ordering. The Doctor distinguishes malformed
text (runtime `VersionNumber.NONE`) from a part overflow (runtime
`IllegalArgumentException`) and rejects both, as well as valid versions above
the candidate's `0.8.7`.

`compatibilityLevel` is trimmed, uppercased, and admitted only when exact enum
lookup finds `JAVA_6` through `JAVA_25`. Enum presence does not establish
effective support: levels above CleanMix's declared `MAX_SUPPORTED` of
`JAVA_13` remain review findings because the launch JRE and ASM also determine
the effective maximum.

Each `requiredFeatures` identifier is likewise Java-trimmed and uppercased with
`Locale.ROOT` before exact enum lookup. The pinned candidate exposes
`UNSAFE_INJECTION` as statically active and
`INJECTORS_IN_INTERFACE_MIXINS` as dependent on process-wide compatibility and
effective JRE/ASM support. Unknown identifiers are terminal static defects;
the conditional feature remains a partial review until runtime state is bound.

Target parsing splits on each `&`, `|`, or space and selects the first token
matching the case-sensitive `@env(...)` or `@environment(...)` uppercase
payload grammar. That first recognized token wins even when its phase is
unknown. If no such token exists, CleanMix attempts an exact-case match of the
complete raw target before using the caller's fallback environment.

## Truth boundary

This report can establish packaging, declarations, exact resource presence,
original-namespace embedding, normalized owner-ID collisions, and the subset
of configuration semantics retained by the scanner. It does not establish:

- selected runtime Mixin service providers;
- connector or config-plugin runtime decisions;
- declared dependency resolution and inherited plugin/connector interfaces;
- decoded target members or injection-point overlap;
- relocated dependency provenance;
- configuration preparation or transformation completion; or
- the final class bytes defined by Foundation.

Those observations belong in typed Crucible runtime evidence. The
`recurrent_complex_integration_scope` boundary says only that this Doctor
introduces no adapter and that Recurrent Complex integration is outside its
assessment. It does not claim whether Recurrent Complex or any adapter is
present in a target installation; Recurrent Complex remains an ordinary
independent Forge participant.

A missing same-archive config plugin or connector is therefore a review, not a
claim that the class is absent from the runtime. Directly declaring the expected
plugin interface is positive static evidence. Its absence from the direct
interface table is still partial because V1 does not resolve superclass
inheritance or explicit dependency edges. V1 does not retain connector class
headers at all, so connector interface identity remains explicitly unresolved.

## Semantic validation

JSON Schema validation is necessary but insufficient. Internal validation MUST
recompute the report and finding content IDs, require unique sorted rule and
finding IDs, close finding-to-rule references, and recompute all summary
counts.

Binding-only validation additionally validates the referenced Project
Intelligence receipt, requires the exact external policy ID and file SHA-256,
requires exactly one coverage row for every policy rule with matching fact and
condition, requires every retained finding to preserve that rule's
disposition, severity, rationale, and fact, and closes artifact labels and
SHA-256 identities against the receipt. Binding-only validation does **not**
prove that a zero-match rule, coverage state, limitation, or omitted finding is
the deterministic result of the bound artifact facts.

Scan-backed validation supplies the exact raw scan mappings, rebuilds the
Project Intelligence receipt from them, requires exact equality with the bound
receipt, reruns every policy condition, and requires the complete regenerated
report to equal the submitted report. Scan-backed validation is therefore the
required admission check for rule matches, zero-match claims, coverage states,
limitations, findings, summaries, and disposition. This closes both a
scan-to-topology gap and the binding-only validator's intentional inability to
reconstruct condition results without the scans.
