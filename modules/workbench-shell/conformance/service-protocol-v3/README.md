# Workbench service protocol V3 conformance

Status: retained contract vectors only; no V3 host, loader, endpoint, stdio
proxy, scheduler, or application handler is implemented here.

The slice binds
[`component-capability-registry-v3.schema.json`](../../schemas/component-capability-registry-v3.schema.json)
and
[`service-protocol-v3.schema.json`](../../schemas/service-protocol-v3.schema.json)
to the canonical Crucible C02 context, input-binding, job, session, and object
identities. All schema references resolve from repository files; the tests do
not fetch schemas from the network.

`valid-vectors.json` contains one recomputable registry revision and
representative messages for every common method family. It includes direct,
accepted-job, unavailable, blocked, and failed outcomes; progress,
cancellation, exact subscription scopes and start/resume/gap, bounded
object-read cases, and a byte-exact V3 stdio frame.

`invalid-vectors.json` applies explicit JSON-pointer mutations to those valid
bases. Each case states whether the closed JSON Schema or the cross-record
semantic composition boundary must reject it. This keeps identity, handler
drift, exact owner-schema dispatch, idempotency, cursor, and byte-range failures
reviewable without implementing a service. The tests call the production
transport-independent `workbench_shell.service_contract` validator rather
than carrying a second semantic validator in the fixture.
Case IDs select and mutate fixtures only inside the test. The production API
receives an ordered list of actual message objects plus an explicit candidate
index; it never consumes `{case_id,value}` wrappers.

Run from the repository root:

```sh
python3 -m unittest modules/workbench-shell/tests/test_service_protocol_v3_contracts.py -v
```

The test also performs strict duplicate-key parsing, Draft 2020-12 meta-schema
checks, offline reference resolution, capability and registry content-ID
recomputation, component-cycle detection, recursive dynamic-schema closure,
owner-adapter descriptor admission, request/response correlation,
descriptor-selected request/result validation, mutation-state reduction, and
cursor/progress monotonicity. Retained exact test doubles exercise the separate
ContextRef/InputBinding, profile-support, action-gate, and lane-B job projection
ports over immutable canonical request objects. They are conformance oracles,
not application authority.
