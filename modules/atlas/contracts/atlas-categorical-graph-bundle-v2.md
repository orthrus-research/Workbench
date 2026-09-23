# Atlas categorical graph bundle V2

Status: experimental current-graph foundation

Atlas V2 publishes a graph set as dependency-closed categorical partitions,
not as one producer-owned graph object. Each partition owns canonical JSONL
node and edge streams, counts, digests, evidence-category bindings, and a
content ID. The graph-set ID covers those authoritative streams and their
dependency order.

Node identity is `(kind, semantic_key)` and remains stable when observed
properties change. Edge identity is `(relation, source, target, semantic_key)`.
Observation-specific facts and record ordinals are evidence, never identity.
Compact occurrence references use adapter ID plus record ordinal; the bundle's
evidence binding supplies the capture, category, and category-result digest.

An edge may reference nodes in its own partition or any earlier declared
dependency. It may not reference a node outside that closure. This makes a
material-core partition independently useful while allowing composition,
forms, fluids, and other lenses to be admitted separately.

`query-index.sqlite3` is a derived, disposable traversal index. Its digest and
source graph-set ID are recorded, but its bytes do not determine graph
identity. The `query_index` manifest member is always present and may be null;
an absent index does not make the authoritative graph invalid.

`validate_bundle_directory` validates only the authoritative manifest and
canonical JSONL streams. It rejects unsafe or non-canonical paths, symlinks,
unknown manifest, descriptor, partition, node, or edge fields, stale counts or
digests, invalid partition content IDs, duplicate semantic identities, and
edge endpoints outside the owning partition's declared transitive dependency
closure. Validation streams bounded JSONL rows and derives node/edge identity
again rather than trusting record IDs.

`verify_query_index` is the separate derived-cache check, but it first requires
successful authoritative directory validation. It binds the exact regular-file
bytes to the manifest descriptor and graph-set ID, requires the closed SQLite
schema and exact partition/node/edge counts, and rejects dangling integer
endpoints. It then reconstructs every canonical node and edge record in stream
order and requires the resulting byte counts and SHA-256 digests to equal the
identity-bearing stream descriptors. Resealing the disposable index descriptor
therefore cannot authorize divergent cache rows.

Query construction performs the same authoritative directory validation and
semantic-equivalence verification before returning a SQLite connection. Query
connections use SQLite read-only immutable mode and never create or repair a
database implicitly. A missing authoritative stream fails closed even when a
fully formed index remains.

Read-only query connections request at most 256 MiB of SQLite memory mapping to
avoid repeated small filesystem reads. Platforms may provide a smaller mapping
or fall back to the ordinary pager. All byte, schema and semantic-equivalence
checks still run; the mapping does not admit unverified cache content.

`rebuild_query_index` first validates the authoritative streams, builds
deterministically in bounded batches under a staging directory, and remeasures
the exact stream reads used for construction against their descriptors. It
verifies the staged index's complete semantic equivalence and validates the
authoritative directory again before atomic exposure, then atomically replaces
the database and its descriptor without changing the graph-set ID.
`CategoricalGraphQuery(..., rebuild_if_missing=True)` is the explicit recovery
path for a missing index; invalid present indexes still fail closed and require
an explicit rebuild call.

New builders publish validation profile
`workbench-atlas-categorical-graph-declared-dependency-closure-v1`. This field
is identity-bearing and requires own-partition plus declared transitive
dependency closure. Retained pre-hardening V2 manifests have no validation
profile and keep their existing graph-set IDs. They validate under the exact
historical own-or-earlier-partition endpoint rule so those immutable objects
remain readable, but no new builder emits that legacy profile. Migration to the
strict profile is a new graph-set publication and must repair any undeclared
dependencies before rebuilding.

The runtime producer never emits these records. Crucible retains occurrence
evidence and coverage custody; Atlas alone owns the semantic projection.
