# Atlas experimental Anvil region observation V1

Status: implemented experimental read-only observation.

Atlas owns bounded normalization and structural findings for one explicitly
selected, stopped Minecraft world directory. V1 discovers conventional
`region` directories without following symbolic links and records exact
SHA-256/size evidence for every `.mca` file.

For each allocated chunk slot, V1 validates:

- the 8192-byte header and 4096-byte sector framing;
- allocation completeness, bounds, and non-overlap;
- declared payload length and gzip, zlib, or raw compression framing;
- bounded NBT syntax; and
- root `Level.xPos/zPos` identity against the region filename and slot.

Every resource bound is explicit in the result. A file that changes during its
read is rejected. Structural corruption is retained as a confirmed Atlas
finding; exceeding an observation safety bound fails the operation rather than
silently truncating evidence.

The result format is `atlas-experimental-anvil-region-observation-v1`. Its
`observation_id` is a SHA-256 identity over all other result fields. The result
is non-normative, is not an Atlas publication, and does not create a Sentinel
policy decision.

Atlas cannot itself establish that the game remained stopped across the whole
multi-file observation. Workbench Shell owns that lifecycle binding, projected
save selection, artifact retention, and session transport. V1 does not validate
blocks, entities, lighting, heightmaps, biome correctness, population order,
terrain continuity, or any user-reported visual symptom.
