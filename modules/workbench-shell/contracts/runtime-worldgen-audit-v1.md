# Workbench runtime world-generation audit V1

Status: implemented CLI vertical slice; read-only integration observation.

## Purpose and authority

`runtime-worldgen-audit` joins retained configuration, effective biome-registry
reports, runtime logs, and installed artifact identities without changing the
pack, runtime, or world. It exists to narrow a world-generation investigation
before a disposable fixed-seed experiment is justified.

The result is an Atlas-derived observation transported by Workbench Shell. It
is non-normative, is not an Atlas publication, and is not a Sentinel policy
finding. Atlas owns normalization, joins, limitations, and derived findings;
the shell selects the profile, composes project context, assigns the transport
identity, and renders the result. Exact Supersymmetry expectations, known
dimension-only biomes, discovery paths, and developer guidance remain in the pack-owned
`workbench-runtime-worldgen-audit-profile-v1` document.
See the Atlas-owned
[retained runtime worldgen observation contract](../../atlas/contracts/atlas-runtime-worldgen-observation-v1.md).

## Inputs

The command accepts a recognized project workspace and an explicit retained
Minecraft runtime root:

```bash
workbench runtime-worldgen-audit /path/to/supersymmetry \
  --runtime-root /path/to/instance/.minecraft
```

The runtime root may also be a dedicated-server root with the same relative
`config/`, `logs/`, and `mods/` layout. Discovery never leaves that root.
Matches must be regular files; file counts, individual sizes, and aggregate
retained evidence bytes are bounded; and every consumed file is represented
by URI, byte size, and SHA-256.

V1 does not accept a launch receipt. The runtime root is therefore explicitly
`unverified` relative to the selected project even though every consumed byte
has an exact identity. A later receipt-bound revision may promote that
relationship after revalidating the materialized tree.

V1 observes the following when available:

- BiomeTweaker `forBiomes` and `forAllBiomesExcept` assignments that feed
  `addToGeneration`;
- BiomeTweaker per-biome JSON output, including climate membership, duplicate
  weights, cross-climate membership, and profile-declared non-Overworld
  membership;
- RTG's unsupported-realistic-biome warning table, RTG world seeds, and
  explicit missing world-generation event messages;
- selected Forge configuration keys and whether their exact names occur in
  `.class` entries of the profile-selected installed artifact.
- exact profile-pinned BiomeTweaker, RTGU, DimStack, and optional compatibility
  artifact identities, plus selected project-source/runtime-file alignment.

Class-literal absence is deliberately reported as
`class-literal-not-observed`, never as proof that a setting is inert. A mod may
bind configuration indirectly through reflection or a transformer.
Profile-specific guidance is emitted only when every artifact it declares as
required has the exact pinned SHA-256. A missing or drifted required artifact
downgrades dependent confidence and suppresses that guidance.

## Result semantics

`workbench-runtime-worldgen-audit-v1` has four states:

- `findings-observed`: at least one profile expectation or structural/runtime
  observation matched;
- `no-findings-observed`: the minimum expected evidence was available and
  produced no finding;
- `inconclusive`: some evidence was available, but a required profile
  expectation or minimum input was not observed;
- `insufficient-evidence`: no input file was available to analyze.

The result contains a deterministic `audit_id` over every field other than the
ID itself. It also includes the project and runtime-root identities, the exact
pack audit-profile identity, all input identities, normalized observations,
profile guidance, and explicit limitations.

The audit can confirm profile-excluded positive registry membership or an
exact logged event-contract report. It cannot, by itself, attribute a visual
chunk symptom. V1 does not start a
server, generate chunks, inspect Anvil data, compare terrain, or establish
repeat-run determinism. Those require a disposable fixed-seed world-generation
experiment and Atlas-owned comparison.

Positive weights define generation candidacy in normalized climate
observations. Zero-weight structural entries are not called selectable, and
negative weights fail the audit. A reported missing event remains a logged
mod assertion: severity does not make it a terminal process failure, and a
later server-ready marker is retained when present.
V1's script parser intentionally covers direct, same-file, single-line
`forBiomes`/`forAllBiomesExcept` assignments feeding `addToGeneration`. A
required selector expectation outside that supported shape is `unobserved`
and makes an otherwise finding-free result `inconclusive`; it is never treated
as a clean match.

## Mutation and failure policy

The command writes nothing. Missing optional evidence is surfaced as
`unavailable` with a limitation. Malformed, oversized, symbolic-link, escaped,
or otherwise untrusted evidence fails the audit instead of being silently
ignored.

## Schema

- [runtime-worldgen-audit-v1.schema.json](../schemas/runtime-worldgen-audit-v1.schema.json)
