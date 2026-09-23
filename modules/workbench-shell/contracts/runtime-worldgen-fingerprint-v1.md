# Workbench stopped-world worldgen fingerprint V1

Status: experimental executable contract.

`runtime-worldgen-fingerprint` transports one explicitly selected stopped
world to Atlas and can retain the identity-bearing result at a new JSON path.
`runtime-worldgen-compare` loads two retained fingerprints, requires Atlas to
verify both identities, and can retain the comparison at a new JSON path.
Neither command overwrites an existing artifact.

Fingerprint V1 block hashes use saved numeric IDs. When independently created
worlds have different Forge registry assignments, use
`runtime-worldgen-block-delta` to obtain the V2 resource-location comparison
before interpreting the raw block-hash difference.

Workbench Shell does not reinterpret block, biome, heightmap, structure,
lighting, or population differences. Atlas owns their normalization and the
comparison facts. Shell owns suite discovery, bounded JSON transport, optional
exclusive artifact creation, CLI rendering, and errors.

These standalone commands cannot establish that Minecraft is stopped. A
caller must first bind them to a completed runtime-observation receipt or an
equivalent lifecycle proof. A future opt-in runtime-observation version may
compose fingerprint retention directly without changing the V1 observation
format.

Fingerprinting is read-only against the world. Optional output retention is a
local mutation outside the world and must target a new file, normally beneath
ignored `.workbench/` evidence storage.
