# Blueprints engine conformance proof v1

Status: implemented experimental contract

Contract ID: `BLUEPRINTS-EXECUTABLE-ENGINE-V1`

The conformance proof exercises the complete generic V1 engine with an explicitly
synthetic, non-admitted standard fixture. It closes engine behavior only. It
does not admit a material, fluid, item, recipe, machine, pack pattern, or
Minecraft runtime result.

The proof has ten required scenarios:

1. no admitted standard produces no plan, candidate, or release;
2. a planned candidate exposes only the closed sealed-candidate metadata;
3. an unavailable required simulation dependency prevents release;
4. instructions release and proof export leave the target unchanged;
5. patch-bundle release and proof export leave the target unchanged;
6. direct apply traverses every application phase, verifies the target, retains
   history, and excludes private payloads from export;
7. target drift rejects application without applying the candidate;
8. an injected partial-application failure restores the exact target and
   closes the transaction;
9. candidate invalidation advances the edit generation, binds every
   invalidated downstream identity, and withholds release; and
10. instructions, patch-bundle, and direct-apply releases retain the same
    operation identity.

`workbench_blueprints.conformance` is independent of the engine producer.
It admits only the exact canonical scenario set, exact state and diagnostic
closures, exact assertions, recomputable scenario evidence digests, the
recomputable proof identity, and the three non-authority limitations.

The executable producer is the test-only
`tests/engine_conformance_fixture.py`. The fixture invokes the real planner,
sealed store, bubblewrap simulator, lifecycle transaction, history store,
proof exporter, and shared application core. The command
`tools/run_engine_conformance.py` writes its canonical proof to an explicit
path.

Every run must reproduce the checked canonical example byte-for-byte. A
schema, example, or validator without the executable scenarios is not a
passing proof.
