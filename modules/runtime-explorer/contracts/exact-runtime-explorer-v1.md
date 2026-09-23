# Workbench Exact Runtime Explorer V1

Status: implemented experimental read-only composition contract; amended
2026-08-28 to retire direct Worldgen Observatory receipt projection

Machine-readable artifact:
[Exact Runtime Explorer result schema V1](../schemas/workbench-exact-runtime-explorer-result-v1.schema.json).

## Purpose

V1 answers one bounded identity-navigation question across explicitly supplied
Workbench sources. Its result format is
`workbench-exact-runtime-explorer-result-v1`, `schema_version` is `1`, and
`read_only` is always `true`.

The explorer owns query interpretation, ranking, grouping, terminal rendering,
and durable navigation. It owns no game fact, policy, construction decision,
teaching authority, or runtime custody. A result is a projection whose facets
remain independently attributable to Project Intelligence, Atlas, Crucible,
Workbench Shell, or Manuals.

## Source envelope

Every source has format `workbench-exact-runtime-explorer-source-v1`, a stable
`source_id`, `source_kind`, `authority`, `state`, exact identity, scope,
coverage, and limitations. Source state is one of:

- `complete`: the supplied source was validated and fully read within its
  declared contract;
- `partial`: a query, relationship, identity, record, publication, or evidence
  coverage boundary was reached;
- `unavailable`: the source authority was not supplied or cannot apply;
- `invalid`: a retained source explicitly records invalid state; and
- `unvalidated`: the bytes were safely decoded but no semantic family
  validator was available.

`complete` describes ingestion of that bounded source. It does not imply
complete knowledge of the assembled game.

V1 admits these projections:

1. current Project Intelligence workspace runtime surfaces and validated
   serialized surfaces whose recorded paths are not treated as current;
2. Project Intelligence Mixin topology plus exact JVM archive/class surfaces;
3. immutable Atlas runtime-graph query projections;
4. semantically validated Crucible Mixin transformation ledgers, runtime
   service receipts, and defining-loader trace receipts;
5. bounded generic identity fields from unknown receipts;
6. retained Workbench live-console sessions whose manifest/event bindings and
   terminal counts validate, plus classified raw logs; and
7. checked-in Manuals guides.

Unknown receipt formats remain `unvalidated`. Their field names are projected
only from an allowlist; password, secret, credential, API-key, and token fields
are never indexed.

Historical V1 implementations also projected exact-format
`workbench-crucible-worldgen-observatory-bundle-v1` receipts. Under
[Decision 0002](../../../docs/decisions/0002-v1-compatibility.md), that unused
Runtime Explorer provider is `retired-unselected`: current direct receipt and
CLI admission reject the format explicitly. Crucible retains the underlying
bundle, schema, validator, tests, identity, and historical claims as
`retained-evidence`. The separate embedded
[graph-native V2 presenter](graph-native-presentation-v2.md) neither recreates
nor modifies this V1 projection.

## Facet evidence states

Each source record becomes one facet with exactly one state:

- `observed`: an Atlas runtime node or a semantically validated controlled
  Crucible occurrence;
- `declared`: an exact declaration in project metadata or source;
- `static-possible`: exact static bytes or references whose runtime selection
  is unobserved;
- `verbatim-evidence`: an identity-like field retained without semantic
  reinterpretation;
- `presentation-observation`: a console/log projection that does not establish
  mechanical causality;
- `teaching`: Manuals context; and
- `unresolved`: evidence exists but cannot close the represented fact.

The explorer must not upgrade one state because another facet shares a literal
identity. In particular, a static class joined to an observed Atlas node stays
static, and a Crucible observation stays scoped controlled evidence rather
than becoming Atlas knowledge.

## Query and ranking

Input is bounded to 8,192 single-line characters. Shell-like quoting is parsed
without evaluating shell syntax. The grammar accepts plain terms, typed
identity prefixes, and the filters `kind`, `owner`, `state`, `profile`, `side`,
and `source`. A filter may be repeated; comma-separated values within one
filter are alternatives, and option filters may form a query without free
text. Typed identities cover code and transformation members, registry and
resource names, recipes, blocks/block states, items/stacks/metadata, loot, biomes,
structures, generators, machines, configuration/Groovy keys, fluids,
materials, ore prefixes, dimensions/world types, events/capabilities,
profiler events, and coordinates.
An explicit typed prefix scores only compatible typed identities; a registry
literal with the same spelling cannot masquerade as a recipe, item, or other
requested domain.

The interpreter recognizes namespaced Minecraft IDs, Java class/member forms,
stack frames, source locators, and coordinates. Ranking is deterministic:
typed exact identity, exact value, case-insensitive exact, prefix, substring,
all terms, then conservative fuzzy terminal-name similarity. Evidence state
changes ordering but never the facet's claim. When a structured identity has
an exact hit, weaker substring and fuzzy neighbors are removed from the
primary result set.

V1 returns at most 100 entities. All source readers have independent byte,
file, relationship, identity, declaration, and record bounds. Truncation is an
explicit source or result state, never a silent empty answer.

## Entity grouping and ambiguity

Facets may be grouped only by exact strong identities such as canonical runtime
node ID, mod ID, registry name, resource location, class/Mixin/target class,
source member, domain-qualified game ID, file path, coordinate, event ID,
artifact identity, or Manual identity. Weak values such as metadata alone and
unqualified configuration/Groovy keys do not join facets. Grouping is
presentation, not proof of equivalence or causality.

An entity is ambiguous when the grouped facets retain more than one Atlas
runtime node, more than one static artifact, an ambiguous/conflicting owner
binding, more than one version, or conflicting observed profile/side scopes.
Status is:

- `not-found` when no supplied record matches;
- `matches` when only ranked non-exact matches exist;
- `exact` when exactly one unambiguous exact entity exists; and
- `ambiguous-exact` when exact resolution has multiple entities or the one
  exact entity retains ambiguity.

No confidence score may hide an ambiguity, missing authority, or partial
source.

## Serialized result and identity

The result contains query interpretation, summary, all supplied source
envelopes, returned entities, uncertainty, and limitations. Each entity
contains merged identities, owners, versions, scopes, original source facets,
relationships, Manual links, and navigation targets.

`entity_id` is the SHA-256 identity of its sorted facet record IDs.
`result_id` is the SHA-256 identity of the complete result without
`result_id`. The semantic validator rejects stale identities, duplicate source,
entity, or facet IDs, unknown source references, authority/source mismatch,
facet/state disagreement, forged merged identity/owner/version/scope/
relationship/navigation/limitation or ambiguity projections, count or
truncation mismatch, inconsistent status, and inconsistent Atlas-runtime
coverage. Content addressing detects change; it does not create authority for
a forged upstream record.

Facet record IDs are namespaced by source. Exact upstream declaration, class,
member, reference, resource, registration, and event IDs remain identities on
the facet, so repeated evidence from separate sources can join without
colliding or erasing provenance.

## Read-only and safety boundary

V1 never loads a JVM class, launches Minecraft, changes a project, writes an
index, downloads dependencies, or repairs a target. Artifact and evidence
inputs must be regular non-symlink files and are identity-checked across the
read. ZIP members are bounded and path-safe. Atlas SQLite input is opened
query-only and immutable after exact application/user version, file identity,
and transient-journal checks.

An invocation accepts no more than 64 explicit inputs of any one kind, 256
source envelopes, and 500,000 projected records. Artifact sets are rejected
before inspection when they exceed 32 files or 512 MiB in aggregate. These
top-level limits supplement each provider's file, byte, member, and record
bounds.

The only direct writes performed by the command are terminal output. Generated
upstream indexes and receipts remain under ignored `.workbench/` custody.
Human-readable output strips terminal command sequences and visibly escapes
rendering controls; the JSON envelope remains the lossless machine surface.

## Known V1 limits

- Atlas projection selection is explicit; automatic runtime capture/index
  discovery is not implemented.
- Workspace source navigation and exact classfile structure are implemented;
  source attachment, mapping resolution, decompilation, instruction decoding,
  and before/after bytecode diffs are not.
- Semantically known transformation receipts are projected, but V1 does not
  infer a complete transform chain from unrelated receipts.
- Direct JSON input for the remaining receipt families is capped at 128 MiB.
  The exact Worldgen Observatory V1 bundle format is no longer admitted by
  Runtime Explorer; its retained evidence remains under Crucible custody.
- Console classifiers remain presentation metadata, and source candidates are
  not silently resolved when multiple local files match.
- IDE-native panels and Relay handoff are future clients of the same command
  and result contract.
