# Recurrent Complex 1.4.8.6 on Cleanroom 0.6.8-alpha

Status: exact-version compatibility warning; not a support approval

## Scope

This note applies only to the Supersymmetry experimental Cleanroom target with:

- Recurrent Complex `1.4.8.6`, SHA-256
  `253226e6c7efe61ae255df0cc2e19d1420945cb7e86f7cd79f51d5db10fd9de8`;
- IvToolkit `1.3.3-1.12`, SHA-256
  `ffb745111790e27cb7810a2e03d5270265dee7ebd3ef98fde897c409d58b1e59`;
  and
- the provisional Cleanroom `0.6.8-alpha` profile.

Generated sessions, captures, worlds, logs, and receipts remain in ignored
`.workbench/` storage. They are not portable support evidence.

## Observed lifecycle behavior

The stock mod participated through ordinary Forge lifecycle events; the
fixture contained no Recurrent Complex import, private hook, or adapter.
Observed structure writes reached terminal chunk storage in one bounded
dedicated-server run. This demonstrates one integration path, not general
compatibility across chunks, dimensions, sides, seeds, or artifact versions.

A repeated ordinary-structure case entered cascading neighbor generation and
was stopped at its operator bound. The same seed and route therefore did not
establish a repeatable bounded result.

## Blocking resource failures

Recurrent Complex rejected four of its 26 bundled `.rcig` resources during
post-initialization:

- `Unholy`;
- `TribalChest`;
- `PeacefulCrypt`; and
- `Holy`.

With the candidate's Gson `2.14.0`, those resources request
`NBTTagCompound`, while nested value deserializers return `NBTTagInt` or
`NBTTagShort`. The resulting `ClassCastException` traces are a platform/mod
compatibility defect, not harmless diagnostic noise.

The run also reported five cascading-generation warnings involving
`OakTreeHuge` and `GenericTreeHuge`. They did not prevent the first bounded
process from completing, but they remain cross-chunk generation risks.

## Disposition

Workbench must not report Recurrent Complex as supported for this exact
candidate while the bundled-resource failures remain. The observed Forge event
path may inform future compatibility tests, but it cannot override that veto.

Future qualification needs fixed resource loading, a completed repeated
bounded case, and explicit preservation of cascading-generation diagnostics.
Generic observers and replacement generators must continue to avoid RC-specific
imports, reflection adapters, or private registration hooks.
