# Mixin Transformer-Chain Epoch Receipt V1

This contract records the transformer provider's ordered live chain and its
ordered delegated chain whenever the candidate-bound Cleanroom provider
rebuilds delegation state. It is additive: it does not widen the meanings of
the Mixin transformation ledger, runtime-service receipt, or Runtime Snapshot
V2.

Each epoch retains:

- the triggering refresh, reason, Mixin phase, prior transformer count, and
  terminal refresh outcome;
- the exact Cleanroom provider class, defining loader, and measured provider
  and Foundation artifact hashes;
- every live and delegated transformer in ordinal order, including reported
  name, unwrapped implementation class, generated/runtime wrapper class,
  implementation loader, artifact hash, priority when available, and
  delegation-exclusion state; and
- provider exclusions separately from Foundation classloader transformer
  exclusions.

Refreshes that do not directly produce an epoch remain explicit as
`superseded` or `invalidated_at_shutdown`. The raw parser independently checks
all footer counts, maximum chain sizes, exact target-byte guards, epoch and
refresh ordering, exclusions, failures, and observer health before a receipt
can be built.

The exact `0.6.8-alpha` observer instruments only the byte-hash-guarded
`CleanMixService` and `FoundationTransformerProvider` seams. It never requests
retransformation and does not invoke an alternate provider. Forge-generated
`$wrapper.*` shells may have an `asmgen:` code source; the observer retains the
shell as `wrapper_class`, unwraps its exact parent, and binds the actual
implementation class to its real artifact.

A complete receipt proves chain custody at observed rebuild epochs. A chain
entry does not prove that the transformer ran for every class, that a
transformation completed, or that the final bytes reached class definition.
Those claims require the transformation-lifecycle and Foundation
final-definition evidence families.

The exact profile imports raw evidence with
`profiles/platforms/cleanroom/tools/import_cleanmix_transformer_chain.py`; the
importer binds the candidate/toolchain locks, agent, launch log, fixture
result, every observed code-source artifact, and the required Cleanroom and
Foundation exclusion layers.

Schema: [`../schemas/mixin-transformer-chain-epoch-receipt-v1.schema.json`](../schemas/mixin-transformer-chain-epoch-receipt-v1.schema.json).
