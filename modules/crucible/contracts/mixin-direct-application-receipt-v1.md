# Mixin direct-application receipt V1

Status: implemented for bounded exact-profile investigation

This contract records target application at the exact
`MixinApplicatorStandard.apply(SortedSet)` seam. It separates a Foundation
transformer entry from the Mixin applicator invocation that actually owns a
named target/mixin pair.

## Producer boundary

The observer is opt-in, installed before class definition, and guarded by the
exact input SHA-256 of `MixinApplicatorStandard`. It records method entry,
normal return, and propagated failure without requesting a class, enumerating a
provider, changing the mixin set, reordering an applicator pass, or altering a
target byte array. The instrumented applicator byte hash is retained.

The V1 raw stream is UTF-8 NDJSON with contiguous sequence numbers under one
capture ID. A healthy footer requires exactly one start and one normal return
for every requested target, one admitted applicator transformation, and zero
application, observer, transform, or write failures.

## Receipt boundary

The content-addressed receipt binds:

- launch, profile, side, and capture identities;
- the observer agent, raw stream, launch log, and fixture result;
- the exact CleanMix artifact and applicator class input/output hashes; and
- ordered completed applications with their named target and mixin classes.

The receipt does not prove final class definition or invocation behavior.
Those claims require an exact Foundation final-definition receipt and a
fixture result from the same launch. Launch-local classloader identities are
locators only.

## Cross-epoch use

An applicator class may be observed across runtime epochs only when its input
bytes match the declared exact guard. Other observer families remain
independently byte-guarded; success here cannot be used to infer their
availability for another CleanMix or Foundation version.
