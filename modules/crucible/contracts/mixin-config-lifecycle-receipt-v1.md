# Mixin Configuration Lifecycle Receipt V1

This contract records configuration custody from the requested resource through
CleanMix admission, phase gating, selection, preparation, post-initialisation,
and terminal promotion or failure. It is a new receipt; no V1 runtime-service
or transformation-ledger meaning is widened.

Each configuration attempt retains:

- the requested config and fallback phase;
- the resolved resource URL, its resolution basis, owner ID and owner
  description, plus verified owner-artifact and resource-entry hashes when the
  resource was read on that attempt;
- the effective `required` state and the observed active state of every
  `requiredFeatures` entry;
- admission decision and queued phase;
- every distinct phase-eligibility result, including nonterminal
  `phase_not_reached` deferrals;
- selection, preparation, and post-initialisation outcomes; and
- one terminal `active`, `deferred`, `duplicate`, `rejected`, or `threw`
  outcome with its reason.

The exact-profile observer may derive a JAR entry URL from a native config
source description and requested path only when the importer subsequently
opens that exact local archive, verifies that the entry exists, and records
both hashes. A cached duplicate does not claim a second resource read or
feature check.

The observer is fail-open toward CleanMix and fail-closed toward the receipt.
Every instrumented class is guarded by an exact input-byte hash, every expected
hook count is verified before transformed bytes are returned, and missing,
rejected, or failed instrumentation prevents a complete receipt.

Active promotion proves configuration lifecycle completion, not target
transformation, transformer-chain order, or final class-definition bytes.

Schema: [`../schemas/mixin-config-lifecycle-receipt-v1.schema.json`](../schemas/mixin-config-lifecycle-receipt-v1.schema.json).
