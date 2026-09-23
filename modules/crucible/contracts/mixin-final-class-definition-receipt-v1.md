# Mixin Final Class-Definition Receipt V1

This contract proves final class bytes for explicitly requested classes at the
exact Foundation definition boundary. It is additive: a CleanMix audit row,
configuration activation, transformer-chain entry, or bytecode-provider return
cannot substitute for this receipt.

The candidate-bound producer instruments the exact byte-hash-guarded
`top.outlands.foundation.boot.ActualClassLoader.findClass` method. For each
requested target it records the start of the lookup and either:

- a successful returned `Class`, joined to the corresponding Foundation class
  dump entry and its SHA-256;
- a cached return, which is retained separately and is not counted as a new
  definition; or
- a definition or observer failure.

The raw parser requires one healthy capture, the exact instrumented Foundation
class, a sorted unique nonempty target set, and a terminal outcome for every
started target. A complete receipt requires every requested target to return
successfully as a new definition, every returned definition to have an exact
dump-byte join, no missing target, and no transform, definition, observer, or
write failure. A class dump entry alone is never accepted as proof that
`defineClass` returned successfully.

Each definition retains the target and returned class name, launch-local class
and defining-loader identities, the target artifact hash, dump-relative path,
and exact final byte hash and size. The receipt separately binds the candidate
and toolchain locks, agent, raw trace, launch log, fixture result, Foundation
artifact, and canonical Foundation dump manifest.

A complete receipt proves successful Foundation definition and final bytes for
only the named targets in the bound launch. It does not prove successful method
invocation, application behavior, all classes in the process, cached-class
history, or compatibility with another Cleanroom/Foundation epoch.

The exact profile imports raw evidence with
`profiles/platforms/cleanroom/tools/import_foundation_final_definitions.py`.
Composite Runtime Snapshot V2 binds the receipt as
`mixin.final-class-definition`, and Runtime Explorer projects each retained
definition as a separate exact evidence record.

Schema: [`../schemas/mixin-final-class-definition-receipt-v1.schema.json`](../schemas/mixin-final-class-definition-receipt-v1.schema.json).
