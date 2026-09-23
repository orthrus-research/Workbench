# Atlas experimental Anvil block delta V2

Status: implemented experimental read-only observation.

The observer compares `(Forge resource location, legacy metadata)` after
resolving each world's saved `minecraft:blocks` registry. It retains
stopped-world validation, evidence binding, bounded ranked attribution, and a
non-causal authority boundary.

This prevents independent numeric block-ID assignment from masquerading as
world-generation drift. V2 scans every shared chunk when the registries differ,
reports the number of raw numeric-state differences separately, and counts
numeric-remap-only positions that are semantically equal. Exact transition,
endpoint, namespace, and vertical-band records describe only semantic changes.

The format is `atlas-experimental-anvil-block-delta-v2`, schema version 2. Its
`observation_id` is a SHA-256 identity over every other field.

The result observes final saved states only. A namespace on a transition
endpoint does not prove which mod or event placed that block, and it cannot
reconstruct generation order.
