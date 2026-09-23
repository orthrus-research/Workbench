# Workbench component capability registry V3

Status: executable contract and conformance slice; runtime composition and
service loading are not implemented by this artifact

## Purpose

The component capability registry is the Workbench Suite's closed inventory of
V3 component topology, owner-supplied operation contracts, and executable
handler registrations. It lets Shell project discovery, preview, consent, CLI,
IDE, local-endpoint, and stdio surfaces from one validated source without
becoming an authority or creating another dispatch path.

The registry has two distinct graphs:

- component topology records ownership and allowed dependencies; and
- capability flow records which owner supplies a contract, which exact
  provider carries it, and which public handler implements it.

Topology never grants semantic authority. A capability descriptor advertises a
contract, not implementation health, profile support, or action authorization.
Those states are reported independently.

This contract implements the registry requirements accepted in
[`CRUCIBLE-RUNTIME-SERVICE.md`](../../../docs/architecture/CRUCIBLE-RUNTIME-SERVICE.md).
Its schema is
[`component-capability-registry-v3.schema.json`](../schemas/component-capability-registry-v3.schema.json).

## Relationship to V2

[`component-registry-v2.schema.json`](../schemas/component-registry-v2.schema.json)
is a partial historical topology snapshot. It is not imported, upgraded, or
interpreted as V3.

The V3 registry may describe a partial V2 compatibility gateway. Every exposed
legacy method has an explicit disposition:

- `shared-handler` maps through a named compatibility contract to the same
  application handler used by V3;
- `retained-legacy-path` preserves a bounded historical implementation; or
- `unavailable` is negotiated as unavailable.

The gateway never advertises V3 contexts, object range reads, durable jobs,
progress, cancellation, continuations, or subscriptions merely because V2 and
V3 can share `Content-Length` framing.

## Registry contents

A registry revision contains:

- its exact format, schema version, registry ID, generation, and service
  distribution identity;
- provider identities;
- component topology;
- owner-supplied capability descriptors;
- owner-adapter descriptor authority admissions;
- runtime handler registrations and health state; and
- the explicit V2 gateway declaration.

Arrays are canonical sets ordered by their stable key using unsigned UTF-8
bytes. Duplicate provider, component, capability-key, capability-content, or
registration IDs fail composition. Every reference must resolve within the
same registry. Unknown fields fail schema validation.

`registry_id`, provider ID, component ID, capability ID, handler-registration
ID, and capability-authority-attestation ID are V2-domain content IDs of their
closed embedded records with only that record's ID omitted. Their bodies
declare `workbench-canonical-json-v2`. Composition recomputes every one of
these identities; a well-shaped caller assertion is not accepted as identity.

## Providers and components

A provider binds a stable provider ID and exactly one of:

- an exact installed distribution identity; or
- an exact source-tree identity together with its dependency-lock identity.

Optional package name and semantic version are descriptive only after the exact
identity is verified.

A component binds a stable component ID, component kind, provider, exact
semantic-authority binding (owner, owner revision, and authority adapter),
declared public contract schemas, and allowed component dependencies. A null semantic authority is valid for a
mechanical host, transport, client, or teaching projection. It is not permission
for Shell to infer an authority from a path, package name, or format string.

Every authority-bearing descriptor has exactly one separate authority-admission
row. The row binds the descriptor content ID to the component's exact authority
binding, the owner adapter's explicit validation entry point, and a recomputable
`approved` attestation. Registry composition calls that owner validation port
over a frozen request containing the exact canonical admission and descriptor
bytes and rejects a missing, stale, mismatched, non-boolean, non-approved, or
raising result with a stable diagnostic. The callback never receives the
validator's mutable working objects. Shell cannot compose valid-looking Atlas or
profile policy IDs into a self-authorized descriptor. Mechanical descriptors
have no authority admission and cannot declare a semantic scope policy.

## Capability descriptor

Each capability descriptor binds all of the following:

- stable `capability_key`, descriptor format and semantic version, plus the
  exact content-addressed `capability_id` used by C02 job records;
- finite per-method bindings which name the V3 method, whether its exact
  request schema validates `arguments` or complete `params`, and its exact
  request, plan, progress, result, and failure schemas;
- owning component and optional semantic authority;
- exact provider ID;
- one public handler entry point and handler implementation ID;
- closed request and result schema IDs, and plan, progress, and failure schema
  IDs when the declared behavior uses them;
- required and produced record kinds;
- applicable `ContextRef` fields, context/input-binding requirement, and exact
  scope-compatibility policy plus its descriptor-owner authority binding;
- one V3 operation class and closed side-effect classes;
- preview, consent, idempotency, and freshness behavior;
- supported embedded, local-endpoint, stdio, CLI, and batch invocation modes;
- progress, cancellation, continuation, and subscription behavior;
- resource class, claims, default budgets, and backpressure policy;
- profile applicability, profile-support predicate, and optional exact
  owner-supplied action gate; and
- descriptor maturity independently from selected-profile support.

The initial V3 operation classes are `inspect`, `query`, `compare`, `visualize`,
`experimental-materialize`, `disposable-runtime-execute`,
`developer-world-mutate`, `stable-construct`, and `publish-or-release`.
The V2 `read-only`/`reversible`/`destructive` labels are not silently reused as
V3 operation classes.

Each method binding states whether `request_schema_id` validates the generic
method's `arguments` member or a specialized method's complete `params` value.
The generic service envelope cannot authorize an open parameter map. Every
dynamically selected schema is recursively fail-closed: all reachable object
schemas close unknown properties, all reachable arrays close their item shape,
all union branches are closed, and unresolved or dynamic references fail
composition. A handler receives parameters only after both the protocol
envelope and the exact method-selected schema pass. The same rule applies to
plan, progress, result, and failure values.
Object and array applicators without an explicit excluding `type` are not
closed: JSON Schema ignores object keywords for array instances and array
keywords for object instances. In particular, an untyped schema with
`properties` plus `additionalProperties:false` is rejected even though it looks
object-closed.

Platform and pack profile revisions are distinct typed references; a generic
`profile-revision:*` value is invalid. Profile applicability and implementation
maturity remain distinct:

- applicability says which exact profile revisions or owner predicate may use
  the capability;
- the support predicate names the accepted support states and exact policy;
- the action gate decides whether this exact operation may proceed; and
- maturity says whether the capability implementation is experimental,
  preview, supported, or deprecated.

An experimental capability may operate against a tested-supported profile. A
supported capability may still be blocked for an unsupported profile or
protected action.

Each advertised exact profile revision also has a separate profile-owned policy
admission. It binds the capability, revision kind/ID, profile authority,
profile-adapter validation port, support predicate, optional action gate, and a
recomputable approval attestation. The semantic capability owner cannot
substitute its own decision for that admission or upgrade experimental support.
Owner-predicate descriptors declare the same profile-adapter validation port
and must validate the exact ContextRef profile bindings at dispatch time.
Each response governed by an action-gate policy carries exact
`action_gate_decision_ids`. A separate frozen gate request binds those IDs and
the reported gate state to the exact canonical request, response, and capability
bytes. Support decisions and gate decisions are distinct roles; a support
receipt cannot be replayed as operation authorization.

## Handler registration

Handlers register explicitly at service composition time. Directory scanning,
entry-point discovery by convention, arbitrary `sys.path` mutation, and
last-registration-wins behavior are forbidden.

Exactly one handler registration names each active capability. The registration
must repeat and exactly match the descriptor's provider, public entry point,
handler implementation, and complete per-method schema bindings. Its supported
invocation modes must be a nonempty subset of the descriptor's modes.

Mutation requirements are derived from declared side effects as well as the
operation-class label. `store-append`, reference, target, process,
instrumentation, world, and export effects impose mutating preview, consent,
idempotency, and freshness requirements. Fixed protocol methods additionally
have minimum operation classes and effects, so relabeling `operation/commit`,
`graph/materialize`, `runtime/start`, or another known mutator as a query cannot
bypass the safety contract.

The reported registration state is one of `available`, `degraded`,
`unavailable`, or `blocked`. Ordered reasons, conformance case IDs, and health
receipt IDs explain that state. A descriptor with no healthy registration
remains discoverable as unavailable; it is not silently removed or routed to a
nearby handler.

The public entry point is an exact declared package-and-symbol path. The loader
must verify the provider and implementation identities before importing it.
Loading a public symbol does not let the handler bypass its owner adapter,
Crucible runtime mechanics, or declared ports.

## Ownership boundary

Workbench Shell owns registry aggregation, health projection, consent routing,
and transport composition. It may reject an invalid registration or unavailable
provider. It does not:

- reinterpret Atlas evidence or graph semantics;
- grant Blueprint approval;
- turn Manuals guidance into authorization;
- upgrade profile support;
- invent a profile gate; or
- implement a second mutation path.

Owner adapters own semantic interpretation and action policy. Common application
handlers own use-case mechanics. Crucible owns canonical graph/custody mechanics.
Clients only present validated descriptors and results.

## Executable validation boundary

`workbench_shell.service_contract` is the shared, transport-independent
contract validator. It returns stable diagnostics for registry/schema
composition and correlated protocol values. The conformance tests and future
callers consume this API; they must not reimplement its identity, authority,
ordering, closure, or correlation rules in a transport. Registry admission is
fail-closed unless the caller supplies the exact owner-adapter validation port;
the retained test double exercises that port but is not production authority.
Every port verdict is accepted only when its exact value is boolean `true`;
exceptions become stable port diagnostics. Public validators first take a
canonical ordinary-JSON snapshot, so caller mutation or custom container
behavior cannot alter a checked projection.
The module contains no
listener, handler loader, owner semantics, consent UI, or service dispatch.

## Conformance requirements

The retained conformance slice must reject at least:

- duplicate stable IDs even when the duplicate objects differ elsewhere;
- a missing owner, provider, component, capability, or handler reference;
- handler/descriptor entry-point, implementation, provider, or schema drift;
- an undeclared invocation mode;
- missing plan/progress schemas for behavior that requires them;
- mutating operation classes without the declared preview/consent boundary;
- a support predicate with no exact policy;
- an authority-bearing descriptor whose component declares a different owner;
- a descriptor missing its exact owner-adapter authority admission;
- a scope policy not bound to the descriptor's exact semantic owner;
- a support predicate or action gate missing its exact profile-owner admission;
- a recursively open dynamically selected schema;
- a claimed operation class weaker than its side effects or method minimum;
- an implicit or capability-free V2 mapping; and
- any attempt to advertise V3-only transport features through the V2 gateway.

Passing this contract proves only registry structure and retained vectors. It
does not claim a daemon, loader, handler, profile admission, or service method
is implemented.
