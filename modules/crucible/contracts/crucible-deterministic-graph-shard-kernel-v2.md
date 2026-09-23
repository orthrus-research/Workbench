# Crucible deterministic graph-shard kernel V2

Status: historical bounded graph-shard implementation contract. This contract
alone does not provide a recipe executor or incremental publisher.

## Purpose

`workbench_crucible_kernel` is a product-generic, transport-independent
calculation boundary for one operation: turn an explicit set of canonical C01
`graph-record` snapshots into canonical NDJSON shard bytes, sealed C01
`object-descriptor` records, and the `partitions`, `record_counts`, and
`record_roots` fragments consumed by a C01 `graph-revision`.

The kernel reuses the frozen C01 canonical loader, record validator, record
sealer, and semantic-root algorithm. It introduces no alternative graph-record,
object-descriptor, blob, partition, or root identity.

## Public API

The package exports:

- `GraphShardAssembler`, whose `add_batch` boundary permits arbitrary caller
  batching and whose `finish` boundary performs one assembly;
- `assemble_graph_partitions`, the one-shot equivalent;
- immutable input/configuration types `AuthorityBinding`,
  `OwnerPolicyBinding`, `ShardKernelConfig`, and non-identity
  `ShardKernelBounds`;
- immutable output types `PartitionAssembly`, `ShardArtifact`,
  `GraphPartition`, `GraphRecordCounts`, `GraphRecordRoots`,
  `GraphAssemblyScope`, and `ShardCoordinate`;
- `plan_dependency_impact` plus immutable change, graph-set closure, bounds, and
  result values;
- `verify_partition_assembly_equivalence` and its immutable receipt; and
- `KernelValidationError`, with stable `code`, `path`, and `message` fields.

Input records are accepted only as exact canonical bytes or exact
`ValidatedRecord` wrappers. A public `ValidatedRecord` is treated only as an
immutable canonical-byte carrier: all convenience metadata is discarded and
rederived by the C01 loader without invoking caller-controlled equality.
Mutable mappings are intentionally not normalized at this boundary.

## Explicit owner ports

Every assembler requires two explicit callables:

1. `logical_key_port(record) -> str` derives the owner-defined total-order key.
   The returned key must exactly equal the identity-bearing `logical_key`
   already sealed in the graph record.
2. `partition_key_port(record, logical_key) -> str` assigns the owner-defined
   partition key.

Both results must satisfy the C01 `semanticText` lexical boundary. The ports
must be pinned, deterministic, side-effect-free owner-adapter functions. This
kernel validates their returned values and the key's agreement with the sealed
row; it does not discover or authenticate an adapter, resolve a policy, or
claim an owner attestation. A later recipe-execution boundary must supply those
trusted ports and retain the exact policy bindings.

Each invocation receives a newly reconstructed `ValidatedRecord` transport,
never the assembler's retained record object. After the port returns, the
kernel checks every transport field, canonical byte string, and reference
expectation against its pre-call snapshot. A forced mutation, exception, or
non-exact string result rejects the batch before any pending record is added;
port code therefore cannot rewrite the identity or scope that the assembler
later retains.

`ShardKernelConfig` pins the graph-record schema object descriptor, exact
total-order and partition `OwnerPolicyBinding` values, and positive maximum
records per shard. Both policies must name the same exact C01 recipe owner. The
total-order binding is copied into every shard descriptor. Both policy IDs are
returned by `graph_revision_fields()` together with their shared
`recipe_owner`, and both owner bindings remain available on the assembly,
preventing a later builder from silently attaching an unrelated partition
policy or recipe owner. The full C01 publication validator remains
responsible for resolving the schema descriptor and authority records and for
invoking the owner hooks.

The Python callable object used for either port is deliberately not a durable
implementation identity. The caller/recipe execution boundary must bind the
exact recipe implementation and prove that its owner-approved adapter supplied
these ports; this mechanics kernel does not invent a second implementation or
semantic authority record.

## Deterministic algorithm

For each record the kernel:

1. reloads and structurally/semantically validates its exact C01 canonical
   bytes;
2. requires top-level kind `graph-record`, the exact C01 graph-record schema,
   and one of the seven C01 graph body kinds;
3. evaluates the logical-key and partition-key ports;
4. rejects duplicate record IDs and duplicate logical keys globally, including
   the case where different record IDs collide on one key; and
5. requires every non-empty assembly to have one exact authority owner, graph
   family, category, resolution, ContextRef ID, and recipe ID.

Rows are grouped by `(partition_key, graph body kind)`. Groups are ordered by
partition-key UTF-8 bytes and the normative C01 graph-kind order. Rows within a
group are ordered strictly by logical-key UTF-8 bytes and split into fixed-size
non-empty shards. Ordinals are contiguous from zero. Each row contributes its
exact canonical bytes followed by one line feed.

Each output descriptor is sealed through the C01 `seal_record` boundary and
binds the raw SHA-256, blob ID, byte length, C01 graph-record schema, schema
descriptor, row count, total-order policy and authority, and inclusive key
bounds. Partition and per-kind roots use the exact C01 semantic-root domains
and projections. Empty graph kinds have count zero and root null; empty shards
are never emitted. An entirely empty assembly is valid, has `scope = None`,
zero counts, and null roots; the future graph-revision caller still supplies
the externally pinned empty-output scope and owner validation.

Input iteration order and `add_batch` boundaries do not affect any output byte,
content ID, ordinal, count, or root. An unsuccessful batch is not committed to
assembler state.

`ShardKernelBounds` caps record count, per-record and aggregate input bytes,
prior-hint count and bytes, output-shard count, and individual shard bytes.
The kernel enforces these ceilings during iteration and before C01 parsing,
copying, owner callbacks, NDJSON splitting, or shard construction. Bounds decide
whether one operation is admitted; they never enter a descriptor, semantic
root, graph field, content ID, or reuse equality.

## Incremental reuse

`finish(prior_shards=...)` treats prior shards only as untrusted in-memory cache
hints. Every prior artifact is reconstructed from its exact descriptor and row
bytes; wrapper convenience metadata is discarded without dynamic equality.
The kernel recomputes the desired clean assembly and reuses prior bytes only
when the coordinate and complete normalized immutable artifact are equal.
Changed, shifted, added, and removed shards are not reused.

`PartitionAssembly.reused_coordinates` is execution metadata and is excluded
from `graph_revision_fields()`. Consequently a clean build and an equivalent
incremental build have byte-for-byte identical NDJSON, descriptor records,
partition rows, counts, roots, and IDs.

The bounded materializer now accepts `prior_shards` at this seam but still runs
the exact recipe and supplies the complete freshly computed candidate record
set. A prior shard therefore cannot suppress execution or output; it can only
replace newly constructed bytes after exact normalized equality succeeds.

## Dependency impact

`plan_dependency_impact` accepts one immutable change set and a complete
`DependencyImpactClosure`: exact canonical graph-set bytes plus the canonical
graph revision and dependency manifest for every member, join, and refinement
graph. It reopens every C01 record, binds the graph-set ID and bytes into the
plan, requires exact graph-set membership, graph-to-manifest identity and
binding agreement, and one-to-one coverage of every graph output partition.

Direct evidence, admission, physical evidence/admission partition, keyed source
graph record/partition, and component changes match only their declared
footprints. An indexed queue closes transitive graph dependencies without
repeated whole-graph scans. An unknown token or any declared change that matches
no footprint selects `full-rebuild` over every output in the supplied graph set;
under-invalidation is never the fallback. Operational limits bound closure
bytes, manifests, changes, the exact aggregate canonical change projection,
footprints, and outputs before unbounded work. Change-projection bytes are
counted incrementally before the planner constructs or hashes the complete
projection; this non-identity admission ceiling cannot alter an admitted plan.

The plan is a mechanics result, not permission. Canonical C01 loading cannot by
itself prove that a declared source graph record occurs in the referenced shard,
because source rows are not inputs to this function. The application must
supply a previously C01-relationally validated immutable closure, retain the
exact owner decisions, and revalidate all inputs under its writer lease before
publication. The kernel does not derive changes from old/new evidence or moving
heads, decide dependency completeness, or construct only the impacted outputs.

## Exact assembly equivalence

`verify_partition_assembly_equivalence(clean, incremental)` treats both
assemblies as untrusted transport. Before reconstruction it bounds shard,
record, metadata, row, and canonical-byte cardinalities without splitting or
parsing oversized input. It then rebuilds each shard from canonical NDJSON and
descriptor bytes, rederives scope, membership, counts, roots, ordering, and
coordinates, and compares every identity-bearing value and raw byte.
`reused_coordinates` is validated separately and is the only excluded execution
metadata. A successful receipt binds the canonical identity projection and the
incremental reuse set; it is not a C01 publication or a graph/set revision ID.

## Bounded proving consumer

The accepted
[`bounded computed-graph trial`](crucible-bounded-computed-graph-materializer-v2.md)
is the kernel's retained recipe-execution consumer. For one exact registered
synthetic before-relation implementation and one pinned admitted evidence/admission pair,
the consumer seals two nodes and one directed edge and asks K01 to construct
exactly two shards: one two-row node shard and one one-row edge shard. A02 then
validates and publishes one dependency manifest, graph revision, and one-member
graph-set revision.

That consumer does not move recipe dispatch, semantic authority, persistence,
publication, or reading into this package. It therefore does not change K01's
status or any exclusion below. Its retained clean-store rebuild proves the
exact built-in synthetic implementation and K01 output identities. The
materializer selects one content-addressed execution binding from an immutable
registry snapshot, qualifies it in two fresh workers, and confirms the exact
result in a separate execution worker before K01 receives application-sealed
records. That bounded seam is neither a general hostile-code sandbox nor a
determinism proof for arbitrary future worker implementations; each new binding
remains its owner's qualification obligation.

## Explicit exclusions

This package has no:

- object store, blob persistence, or graph publication path;
- mutable reference read, compare-and-swap, or update behavior;
- job, session, progress, cancellation, clock, deadline, or subscription logic;
- workspace, profile, or world selection;
- daemon, JSON-RPC, stdio, socket, or other transport;
- Atlas database, private authority, filesystem evidence, or profile import;
- recipe implementation dispatch, derivation execution, old/new revision delta
  derivation, candidate pruning, graph-category semantic reduction, or
  whole-graph owner attestation; or
- graph-revision or graph-set sealing.

The returned object descriptors and graph-revision fragments are
pre-publication values. They become authoritative only inside a complete C01
publication that resolves all dependencies, exact bytes, authorities, and
required policy hooks. Shell or service layers may call this kernel, but they
must not reinterpret its mechanics as an Atlas semantic decision or an
authorization result.

## Focused conformance

The kernel, impact, equivalence, query, and bounded-materializer focused suites
use retained C01 synthetic records and prove:

- input-permutation independence;
- arbitrary batching independence across bytes and validated snapshots;
- failed-batch rollback with stable owner-port failure diagnostics;
- duplicate ID and distinct-record logical-key collision rejection;
- exact canonical NDJSON, descriptor bytes/IDs, blob digests, partition roots,
  and per-kind roots;
- exact unchanged-shard reuse;
- one changed row rebuilding only its affected shard inside an existing
  multi-shard partition;
- clean-versus-incremental byte and ID equivalence;
- exact complete graph-set/graph/manifest impact closure, permutation
  independence, transitive propagation, and conservative full fallback;
- rejection of incomplete or mismatched graph-set closures and binding of the
  graph-set identity into every impact plan;
- independent reconstruction and byte-exact assembly equivalence receipts;
- preparse/preallocation resource ceilings for records, prior hints, shards,
  metadata, impact closure and change projections, query inputs, and
  inspected-row results;
- hostile wrapper metadata that raises on equality cannot influence input or
  prior-shard validation; and
- rejection of mixed graph-revision scopes.
