# Mixin Selected Service Components Receipt V1

This contract records the actual component objects exposed by the selected
Mixin service in one exact launch. It is separate from provider discovery: a
service can be visible or selected without proving which class, bytecode,
transformer, tracker, audit, logger, or defining-loader objects it returned.

Every healthy receipt contains exactly one observed row for these stable roles:

- `service`;
- `service_classloader`;
- `class_provider`;
- `bytecode_provider`;
- `transformer_provider`;
- `class_tracker`;
- `audit_trail`; and
- `logger`.

Each row binds the implementation class, object identity, defining-loader class
and identity, code-source URI, and measured source artifact. Object and loader
identities are launch-local evidence; cross-epoch comparison uses implementation
class and artifact projections, never identity hash codes.

The observation also binds the exact guarded `MixinService` input bytes, the
instrumented output bytes, its defining-loader class, and its measured source
artifact. The candidate and toolchain locks, launch log, fixture result, agent,
and raw event stream are all content-addressed receipt inputs.

The exact-profile producer may observe components at their normal lifecycle
boundaries. It must not create a second provider, call a lazy accessor merely
to populate the receipt, alter service descriptors, or change service
selection. A missing or failed role makes the receipt failed rather than
silently partial.

The receipt does not prove configuration admission, transformer order,
transformation completion, or final class bytes. Those remain separate
capabilities and receipts.

Schema: [`../schemas/mixin-selected-service-components-receipt-v1.schema.json`](../schemas/mixin-selected-service-components-receipt-v1.schema.json).
