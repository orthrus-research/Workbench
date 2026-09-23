import json
import re
import unittest
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[1]
JAVA = FIXTURE / "src" / "main" / "java"
OBSERVER = JAVA / "dev" / "workbench" / "worldgenobservatory"
SYNTHETIC = JAVA / "dev" / "workbench" / "syntheticworldgen"
PROBE = OBSERVER / "probe"
MIXINS = PROBE / "mixin"
RESOURCES = FIXTURE / "src" / "main" / "resources"


def source_tree(root: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.java")))


class SourceContractTest(unittest.TestCase):

    def test_exact_cleanroom_candidate_build_binding(self):
        properties = (FIXTURE / "gradle.properties").read_text(encoding="utf-8")
        build = (FIXTURE / "build.gradle").read_text(encoding="utf-8")

        self.assertIn("minecraft_version=1.12.2", properties)
        self.assertIn("mcp_version=39-1.12", properties)
        self.assertIn("cleanroom_version=0.6.8-alpha", properties)
        self.assertIn("loader cleanroom_version", build)
        self.assertIn("languageVersion = JavaLanguageVersion.of(25)", build)
        self.assertIn("com.cleanroommc:mixinextras-common:0.5.5", build)
        self.assertIn(
            "tasks.matching { task -> task.name in ['runServer', 'runClient'] }",
            build,
        )
        self.assertIn("workbenchServerRunDir", build)
        self.assertIn("must be an absolute path", build)
        self.assertIn("-Dmixin.env.disableRefMap=true", build)
        self.assertIn("crl.dev.extrapath", build)
        self.assertIn("workbench-disabled-dev-extra", build)

    def test_early_probe_is_an_exact_cleanroom_coremod(self):
        build = (FIXTURE / "build.gradle").read_text(encoding="utf-8")
        loader = (PROBE / "ObservatoryLoadingPlugin.java").read_text(encoding="utf-8")
        config = json.loads(
            (RESOURCES / "mixins.workbench_worldgen_observatory.early.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn("FMLCorePlugin", build)
        self.assertIn("ObservatoryLoadingPlugin", build)
        self.assertIn("FMLCorePluginContainsFMLMod", build)
        self.assertIn("MixinConfigs", build)
        self.assertIn("implements IFMLLoadingPlugin, IEarlyMixinLoader", loader)
        self.assertIn("mixins.workbench_worldgen_observatory.early.json", loader)
        self.assertTrue(config["required"])
        self.assertEqual("JAVA_25", config["compatibilityLevel"])
        self.assertEqual(
            "dev.workbench.worldgenobservatory.probe.mixin",
            config["package"],
        )
        self.assertEqual(1, config["injectors"]["defaultRequire"])
        self.assertEqual(
            "dev.workbench.worldgenobservatory.probe.ProbeMixinPlugin",
            config["plugin"],
        )
        self.assertEqual(
            "mixins.workbench_worldgen_observatory.early-refmap.json",
            config["refmap"],
        )

    def test_exact_mcp_to_srg_refmap_covers_every_minecraft_selector(self):
        refmap = json.loads(
            (RESOURCES / "mixins.workbench_worldgen_observatory.early-refmap.json").read_text(
                encoding="utf-8"
            )
        )
        mappings = refmap["mappings"]
        self.assertEqual(mappings, refmap["data"]["searge"])

        flattened = "\n".join(
            f"{selector} -> {target}"
            for entries in mappings.values()
            for selector, target in entries.items()
        )
        for exact_srg_name in (
            "func_186025_d",
            "func_186028_c",
            "func_185932_a",
            "func_186030_a",
            "func_186034_a",
            "func_185931_b",
            "func_177436_a",
            "func_177855_a",
            "func_180501_a",
        ):
            self.assertIn(exact_srg_name, flattened)

        mixin_sources = source_tree(MIXINS)
        minecraft_selectors = set(
            re.findall(r'method = "([A-Za-z]+\([^\"]+)"', mixin_sources)
        )
        mapped_selectors = {
            selector
            for entries in mappings.values()
            for selector in entries
            if not selector.startswith("L")
        }
        self.assertTrue(mapped_selectors.issubset(minecraft_selectors))

    def test_two_independent_mod_containers_are_declared(self):
        metadata = json.loads(
            (FIXTURE / "src" / "main" / "resources" / "mcmod.info").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {
                "workbench_worldgen_observatory",
                "workbench_synthetic_worldgen",
            },
            {entry["modid"] for entry in metadata},
        )

    def test_synthetic_decorator_has_no_observer_dependency(self):
        synthetic = source_tree(SYNTHETIC)
        observer = source_tree(OBSERVER)

        self.assertNotIn("dev.workbench.worldgenobservatory", synthetic)
        self.assertNotIn("dev.workbench.syntheticworldgen", observer)
        for forbidden in ("ivorius", "reccomplex", "recurrentcomplex"):
            self.assertNotIn(forbidden, (synthetic + observer).lower())

    def test_probe_targets_all_required_low_level_boundaries(self):
        provider = (MIXINS / "MixinChunkProviderServer.java").read_text(encoding="utf-8")
        chunk = (MIXINS / "MixinChunk.java").read_text(encoding="utf-8")
        primer = (MIXINS / "MixinChunkPrimer.java").read_text(encoding="utf-8")
        world = (MIXINS / "MixinWorld.java").read_text(encoding="utf-8")
        event_bus = (MIXINS / "MixinEventBus.java").read_text(encoding="utf-8")

        for selector in (
            "provideChunk(II)",
            "loadChunk(II)",
            "loadChunk(IILjava/lang/Runnable;)",
            "IChunkGenerator;generateChunk(II)",
        ):
            self.assertIn(selector, provider)
        for selector in (
            "populate(Lnet/minecraft/world/chunk/IChunkProvider;",
            "populate(Lnet/minecraft/world/gen/IChunkGenerator;)V",
            "IChunkGenerator;populate(II)V",
            "setBlockState(Lnet/minecraft/util/math/BlockPos;",
        ):
            self.assertIn(selector, chunk)
        self.assertIn("setBlockState(IIILnet/minecraft/block/state/IBlockState;)V", primer)
        self.assertIn("setBlockState(Lnet/minecraft/util/math/BlockPos;", world)
        self.assertIn("IEventListener;invoke", event_bus)
        for bus in ("EVENT_BUS", "TERRAIN_GEN_BUS", "ORE_GEN_BUS"):
            self.assertIn(f"MinecraftForge.{bus}", event_bus)

    def test_every_injection_is_required_and_exactly_bounded(self):
        mixins = source_tree(MIXINS)
        injection_count = mixins.count("@WrapMethod(") + mixins.count("@WrapOperation(")
        self.assertEqual(injection_count, mixins.count("require = 1"))
        self.assertEqual(injection_count, mixins.count("expect = 1"))
        self.assertEqual(injection_count, mixins.count("allow = 1"))

        plugin = (PROBE / "ProbeMixinPlugin.java").read_text(encoding="utf-8")
        self.assertIn("preApply", plugin)
        self.assertIn("postApply", plugin)
        self.assertIn("ClassWriter", plugin)
        self.assertIn('MessageDigest.getInstance("SHA-256")', plugin)
        self.assertIn("observed != 1", plugin)
        self.assertIn("Exact probe binding failed", plugin)
        for field in (
            "original_class_sha256",
            "transformed_class_sha256",
            "expected_injection_count",
            "observed_injection_count",
        ):
            self.assertIn(field, (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8"))

    def test_disabled_probe_calls_original_without_observation(self):
        for path in sorted(MIXINS.glob("*.java")):
            source = path.read_text(encoding="utf-8")
            if "@Wrap" not in source:
                continue
            self.assertRegex(
                source,
                r"if \(!ProbeRuntime\.(?:enabled|hasActiveWorldgenSpan)\(\)[^}]*original\.call",
                msg=path.name,
            )

    def test_original_throwables_are_recorded_then_rethrown_unchanged(self):
        mixins = source_tree(MIXINS)
        catches = mixins.count("catch (Throwable originalFailure)")
        self.assertGreaterEqual(catches, 10)
        self.assertEqual(catches, mixins.count("ProbeRuntime.sneakyThrow(originalFailure)"))
        self.assertNotIn("catch (Exception originalFailure)", mixins)

        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        self.assertIn("public static <T extends Throwable, R> R sneakyThrow", runtime)
        self.assertIn("throw (T) throwable", runtime)

    def test_direct_application_observer_failures_never_escape_callbacks(self):
        agent_root = (
            FIXTURE / "src" / "cleanmixTraceAgent" / "java" / "dev"
            / "workbench" / "crucible" / "cleanmixtrace"
        )
        runtime = (agent_root / "CleanMixDirectApplicationRuntime.java").read_text(
            encoding="utf-8"
        )
        agent = (agent_root / "CleanMixDiscoveryTraceAgent.java").read_text(
            encoding="utf-8"
        )

        for callback in (
            "application_started_callback",
            "application_completed_callback",
            "application_threw_callback",
        ):
            self.assertIn(f'suppressObserverFailure("{callback}"', runtime)
        self.assertIn("never replace the observed outcome", runtime)
        self.assertIn("writeFailure = true;", runtime)
        self.assertIn("The failed raw stream remains inadmissible without a footer", runtime)
        self.assertNotIn("direct-application trace write failed", runtime)

        application_threw = agent.index('"applicationThrew"')
        duplicated_original = agent.rfind("dup();", 0, application_threw)
        self.assertNotEqual(-1, duplicated_original)
        self.assertLess(duplicated_original, application_threw)
        self.assertLess(application_threw, agent.index("throwException();", application_threw))

    def test_event_listener_records_before_after_cancel_and_result_state(self):
        event_bus = (MIXINS / "MixinEventBus.java").read_text(encoding="utf-8")
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        for boundary in (
            "post_enter",
            "post_return",
            "listener_enter",
            "listener_return",
            "listener_throw",
        ):
            self.assertIn(f'"{boundary}"', event_bus)
        self.assertIn("@Local(index = 3) int listenerOrdinal", event_bus)
        for state in (
            "cancelled_before",
            "cancelled_after",
            "result_before",
            "result_after",
        ):
            self.assertIn(state, runtime)
        self.assertIn("stableEventStateDigest(event)", runtime)
        self.assertIn("field.getDeclaringClass().getName()", runtime)
        self.assertIn("fields.sort", runtime)
        self.assertNotIn("System.identityHashCode", runtime)
        self.assertIn("ASMEventHandlerAccess", runtime)
        self.assertIn("workbench$getOwner", runtime)
        self.assertIn("workbench$getReadable", runtime)

    def test_block_write_channels_share_nested_chain_and_capture_caller(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        mixins = source_tree(MIXINS)
        for channel in ("chunk_primer", "world_api", "chunk_storage"):
            self.assertIn(f'"{channel}"', mixins)
        self.assertIn("candidate.x == x && candidate.y == y && candidate.z == z", runtime)
        self.assertIn("chainParent.chainId", runtime)
        self.assertIn("chainParent.childSeen = true", runtime)
        self.assertIn("chainParent == null ? 0 : chainParent.depth + 1", runtime)
        self.assertIn('"write_chain_id"', runtime)
        self.assertIn('"chain_depth"', runtime)
        self.assertIn("Actor.caller(targetClass)", runtime)
        self.assertIn('"terminal", terminal && !token.childSeen', runtime)

    def test_terminal_storage_actor_and_external_caller_are_exact_when_unique(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")

        self.assertIn('"chunk_storage".equals(channel)', runtime)
        self.assertIn('Actor.target(', runtime)
        self.assertIn('"setBlockState"', runtime)
        self.assertIn('"exact_target"', runtime)
        self.assertIn("Loader.instance().getModList()", runtime)
        self.assertIn("matches == 1 ? match : AMBIGUOUS", runtime)
        self.assertIn("uniqueDeclaredMethodDescriptor", runtime)
        self.assertIn("owner.getDeclaredMethods()", runtime)
        self.assertIn("if (match != null)", runtime)
        self.assertIn('"stack_source_method"', runtime)
        self.assertIn("classResourceSource(type)", runtime)
        self.assertIn("SourceArtifactDigest.classSourcePath(type)", runtime)

    def test_raw_actor_receipts_use_real_classes_and_jvm_descriptors(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")

        self.assertNotIn('"(Event)boolean"', runtime)
        self.assertNotIn('"(Event)void"', runtime)
        self.assertNotIn('"(String,Throwable)void"', runtime)
        self.assertIn(
            '"(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"',
            runtime,
        )
        self.assertIn(
            '"(Ljava/lang/String;Ljava/lang/Throwable;)V"',
            runtime,
        )
        self.assertIn(
            '"(Ldev/workbench/worldgenobservatory/probe/'
            'ProbeRuntime$HealthObservation;)V"',
            runtime,
        )
        self.assertIn(
            'String descriptor = uniqueDeclaredMethodDescriptor(',
            runtime,
        )
        self.assertIn(
            'descriptor == null ? "jvm_stack" : "jvm_descriptor"',
            runtime,
        )

    def test_raw_transport_is_append_only_lossless_and_not_canonical_capture(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        raw_schema = json.loads(
            (RESOURCES / "workbench-worldgen-observatory-raw-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("workbench-cleanroom-worldgen-observatory-raw-v1", runtime)
        self.assertEqual(
            "workbench-cleanroom-worldgen-observatory-raw-v1",
            raw_schema["properties"]["format"]["const"],
        )
        self.assertEqual(
            {
                "format",
                "record_type",
                "sequence",
                "capture_id",
                "scope",
                "causality",
                "actor",
                "order",
                "outcome",
                "coverage",
                "payload",
            },
            set(raw_schema["required"]),
        )
        self.assertNotIn("workbench-crucible-worldgen-observatory-record-v1", runtime)
        self.assertNotIn("WORKBENCH-CRUCIBLE-WORLDGEN-OBSERVATORY-CAPTURE-V1", runtime)
        self.assertIn("StandardOpenOption.APPEND", runtime)
        self.assertNotIn("StandardOpenOption.TRUNCATE_EXISTING", runtime)
        self.assertIn("writer.flush()", runtime)
        self.assertIn('"dropped_record_count", 0', runtime)
        for queue_type in ("ArrayBlockingQueue", "LinkedBlockingQueue", "ExecutorService"):
            self.assertNotIn(queue_type, runtime)

    def test_driver_managed_capture_has_closed_control_pair_and_deferred_health(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        schema = json.loads(
            (RESOURCES / "workbench-worldgen-observatory-raw-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        record_types = set(schema["properties"]["record_type"]["enum"])

        self.assertTrue(
            {
                "capture_control",
                "fixture_driver",
                "cooperative_stage",
                "decision",
                "rng_observation",
                "checkpoint",
            }.issubset(record_types)
        )
        self.assertIn("DRIVER_MANAGED", runtime)
        self.assertIn("private static volatile boolean CAPTURE_ARMED", runtime)
        self.assertIn("!DRIVER_MANAGED && !safeBooleanProperty(DEFER_PROPERTY, false)", runtime)
        self.assertIn("HEALTH_OBSERVATIONS.put(hookId, health)", runtime)
        self.assertIn("Collections.sort(hookIds)", runtime)
        self.assertIn('"capture_control_id"', runtime)
        self.assertIn('"requested_mode"', runtime)
        self.assertIn('"control", "start"', runtime)
        self.assertIn('"control", "stop"', runtime)
        self.assertIn("CAPTURE_ARMED = false", runtime)

        condition_types = {
            clause["if"]["properties"]["record_type"]["const"]
            for clause in schema["allOf"]
        }
        self.assertEqual(record_types, condition_types)
        for clause in schema["allOf"]:
            payload_ref = clause["then"]["properties"]["payload"]["$ref"]
            payload = schema["$defs"][payload_ref.rsplit("/", 1)[-1]]
            if "oneOf" in payload:
                for variant in payload["oneOf"]:
                    closed = schema["$defs"][variant["$ref"].rsplit("/", 1)[-1]]
                    self.assertFalse(closed["additionalProperties"])
            else:
                self.assertFalse(payload["additionalProperties"])

        arm = runtime[runtime.index("public static boolean armForFixtureDriver"):]
        self.assertLess(arm.index('"control", "start"'), arm.index("emitHealth("))

    def test_iteration_capture_is_bounded_opt_in_and_mutually_exclusive(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        mod = (OBSERVER / "WorldgenObservatoryMod.java").read_text(encoding="utf-8")
        synthetic = (SYNTHETIC / "SyntheticWorldgenMod.java").read_text(
            encoding="utf-8"
        )

        for property_name in (
            "workbench.worldgen.observatory.iteration_auto.enabled",
            "workbench.worldgen.observatory.iteration_auto.min_chunk_x",
            "workbench.worldgen.observatory.iteration_auto.min_chunk_z",
            "workbench.worldgen.observatory.iteration_auto.max_chunk_x_exclusive",
            "workbench.worldgen.observatory.iteration_auto.max_chunk_z_exclusive",
        ):
            self.assertIn(property_name, runtime)
        self.assertIn("DRIVER_MANAGED", runtime[runtime.index(
            "public static boolean armForWorldgenIteration"
        ):runtime.index("public static void stopWorldgenIterationCapture")])
        self.assertIn("ITERATION_AUTO_MANAGED", runtime[runtime.index(
            "public static boolean armForFixtureDriver"
        ):])
        self.assertIn('"cleanroom-worldgen:chunk.populate_neighbors"', runtime)
        self.assertIn('"cleanroom-worldgen:chunk.populate_owned"', runtime)
        for hook_id in (
            "cleanroom-worldgen:chunk.generator_populate_call",
            "cleanroom-worldgen:event_bus.post",
            "cleanroom-worldgen:event_bus.listener_invoke",
        ):
            self.assertIn(f'"{hook_id}".equals(hookId)', runtime)
        self.assertIn("ITERATION_AUTO_MANAGED && !acceptIterationSpan(hookId)", runtime)
        self.assertIn(
            '"dev.workbench.worldgenprototype.world.PrototypeFeature"', runtime
        )
        self.assertIn(
            '("chunk_storage".equals(channel) && chainParent != null)', runtime
        )
        self.assertIn("scope.chunkX >= ITERATION_MIN_CHUNK_X", runtime)
        self.assertIn("scope.chunkZ < ITERATION_MAX_CHUNK_Z", runtime)
        self.assertIn("ProbeRuntime.armForWorldgenIteration()", mod)
        self.assertIn("ProbeRuntime.stopWorldgenIterationCapture()", mod)

        self.assertIn("workbench.worldgen.observatory.synthetic.enabled", synthetic)
        self.assertIn('"true"', synthetic)
        self.assertLess(
            synthetic.index("workbench.worldgen.observatory.synthetic.enabled"),
            synthetic.index("GameRegistry.registerWorldGenerator"),
        )

    def test_dedicated_driver_preserves_v1_and_adds_bounded_v2_route(self):
        driver = (
            OBSERVER / "fixture" / "DedicatedServerFixtureDriver.java"
        ).read_text(encoding="utf-8")
        mod = (OBSERVER / "WorldgenObservatoryMod.java").read_text(encoding="utf-8")

        self.assertIn("FMLServerStartedEvent", mod)
        self.assertIn("FMLCommonHandler.instance().getMinecraftServerInstance()", mod)
        self.assertIn('System.getProperty(ENABLE_PROPERTY, "false")', driver)
        self.assertIn("instanceof DedicatedServer", driver)
        self.assertIn("EXPECTED_SEED_PROPERTY", driver)
        self.assertIn("world.getSeed() != expectedSeed", driver)
        self.assertIn("Collections.reverse(route)", driver)
        self.assertIn("new RouteSelection(false, 64, 64, 2)", driver)
        self.assertIn("positions.add(new ChunkPos(anchorX + xOffset, anchorZ + zOffset))", driver)
        for property_name in (
            "route_anchor_x",
            "route_anchor_z",
            "route_size",
        ):
            self.assertIn(property_name, driver)
        self.assertIn("MIN_SIZE = 2", driver)
        self.assertIn("MAX_SIZE = 32", driver)
        self.assertIn("zOffset", driver)
        self.assertIn("xOffset", driver)
        for lifecycle_call in (
            "provider.provideChunk(position.x, position.z)",
            "chunk.populate(provider, provider.chunkGenerator)",
            "server.saveAllWorlds(true)",
            "world.flush()",
            "server.initiateShutdown()",
            "writeResult(resultJson)",
            "ProbeRuntime.fixtureDriverCompleted",
            "ProbeRuntime.stopFixtureCapture",
        ):
            self.assertIn(lifecycle_call, driver)

        ordered_calls = (
            "ProbeRuntime.armForFixtureDriver",
            "provider.provideChunk(position.x, position.z)",
            "chunk.populate(provider, provider.chunkGenerator)",
            "server.saveAllWorlds(true)",
            "world.flush()",
            "ChunkCheckpoint.blockStateSha256(chunk)",
            "server.initiateShutdown()",
            "writeResult(resultJson)",
            "ProbeRuntime.fixtureDriverCompleted",
            "ProbeRuntime.stopFixtureCapture",
        )
        offsets = [driver.index(call) for call in ordered_calls]
        self.assertEqual(sorted(offsets), offsets)

        for result_field in (
            "workbench.worldgen-observatory.fixture-result.v1",
            "workbench.worldgen-observatory.fixture-result.v2",
            "dedicated_server_fixed_region_v1",
            "dedicated_server_rectangular_region_v2",
            '\\"selection\\"',
            '\\"anchor_chunk_x\\"',
            '\\"anchor_chunk_z\\"',
            '\\"route_size\\"',
            '"completion_state"',
            '"shutdown_state"',
            '"world_seed_sha256"',
            '"selector_sha256"',
            '"route_sha256"',
            '"semantic_state_sha256"',
            "runtime_mod_inventory",
            '"mod_id"',
            '"source_sha256"',
            '"mod_class_name"',
        ):
            self.assertIn(result_field, driver)
        self.assertIn("Loader.instance().getModList()", driver)
        self.assertIn("container.getMod()", driver)
        self.assertIn("File source = container.getSource()", driver)
        self.assertIn("SourceArtifactDigest.sha256(source.toPath())", driver)
        self.assertIn("CleanroomModDiscoverer.instance()", driver)
        self.assertIn("equalsIgnoreCase(container.getModId())", driver)
        self.assertIn("discoveredSources.size() == 1", driver)
        self.assertIn("discoveredSources.iterator().next().toPath()", driver)
        self.assertIn("SourceArtifactDigest.sha256ClassSource(Chunk.class)", driver)
        self.assertIn("SourceArtifactDigest.sha256ClassSource(EventBus.class)", driver)
        self.assertIn('return "unavailable"', driver)
        self.assertIn("result.sort(java.util.Comparator", driver)
        for unstable_identity in (
            "System.currentTimeMillis",
            "System.nanoTime",
            "Instant.now",
            "UUID.randomUUID",
        ):
            self.assertNotIn(unstable_identity, driver)

    def test_cooperative_observations_enter_raw_transport_independently(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        self.assertIn('"world_seed_sha256", digestText(worldSeed)', runtime)
        self.assertNotIn('"world_seed_sha256", digestParts(worldSeed)', runtime)
        wrapper = (OBSERVER / "world" / "ObservingChunkGenerator.java").read_text(
            encoding="utf-8"
        )
        final = (OBSERVER / "world" / "FinalCheckpointWorldGenerator.java").read_text(
            encoding="utf-8"
        )
        checkpoint = (OBSERVER / "trace" / "ChunkCheckpoint.java").read_text(
            encoding="utf-8"
        )

        for method in (
            "cooperativeStage",
            "cooperativeDecision",
            "cooperativeRng",
            "cooperativeCheckpoint",
        ):
            self.assertIn(f"ProbeRuntime.{method}", wrapper + final)
            self.assertIn(f"public static void {method}", runtime)
        self.assertIn("Actor actor = cooperativeCaller(owner)", runtime)
        self.assertIn("declaredOwner.equals(actor.className)", runtime)
        self.assertNotIn('Actor.workbench(owner, "cooperativeObservation", null)', runtime)
        self.assertIn('"derive_named_seed_without_consuming_runtime_random"', runtime)
        self.assertIn('"semantic_state_sha256"', runtime)
        self.assertIn('"chunk-block-state-registry-name-metadata-yzx-v1"', runtime)
        self.assertIn('MessageDigest.getInstance("SHA-256")', checkpoint)
        self.assertIn("for (int y = 0; y < 256; y++)", checkpoint)
        self.assertIn("for (int z = 0; z < 16; z++)", checkpoint)
        self.assertIn("for (int x = 0; x < 16; x++)", checkpoint)
        self.assertRegex(checkpoint, r"return hex\(digest\.digest\(\)\)")

    def test_source_artifact_identity_is_shared_and_path_independent(self):
        runtime = (PROBE / "ProbeRuntime.java").read_text(encoding="utf-8")
        driver = (
            OBSERVER / "fixture" / "DedicatedServerFixtureDriver.java"
        ).read_text(encoding="utf-8")
        digest = (
            OBSERVER / "evidence" / "SourceArtifactDigest.java"
        ).read_text(encoding="utf-8")

        self.assertIn("SourceArtifactDigest.sha256(path)", runtime)
        self.assertIn("SourceArtifactDigest.sha256(source.toPath())", driver)
        self.assertIn("discoveredSources.iterator().next().toPath()", driver)
        self.assertIn("Files.isRegularFile(normalized, LinkOption.NOFOLLOW_LINKS)", digest)
        self.assertIn("Files.walk(normalized)", digest)
        self.assertIn("entries.sort", digest)
        self.assertIn("relativeUtf8.length", digest)
        self.assertIn("updateLong(digest, length)", digest)
        self.assertIn("digest.update(entry.relativeUtf8)", digest)
        self.assertNotIn("digest.update(normalized.toString()", digest)

    def test_only_standard_forge_worldgen_registration_connects_the_fixture(self):
        synthetic_mod = (SYNTHETIC / "SyntheticWorldgenMod.java").read_text(encoding="utf-8")
        synthetic_generator = (SYNTHETIC / "SyntheticWorldGenerator.java").read_text(
            encoding="utf-8"
        )
        synthetic_listener = (SYNTHETIC / "SyntheticOreEventListener.java").read_text(
            encoding="utf-8"
        )
        observer_mod = (OBSERVER / "WorldgenObservatoryMod.java").read_text(encoding="utf-8")
        final_checkpoint = (
            OBSERVER / "world" / "FinalCheckpointWorldGenerator.java"
        ).read_text(encoding="utf-8")

        self.assertIn("GameRegistry.registerWorldGenerator", synthetic_mod)
        self.assertIn("MinecraftForge.ORE_GEN_BUS.register", synthetic_mod)
        self.assertIn("implements IWorldGenerator", synthetic_generator)
        self.assertIn("@SubscribeEvent", synthetic_listener)
        self.assertIn("event.setResult(Event.Result.ALLOW)", synthetic_listener)
        self.assertIn("GameRegistry.registerWorldGenerator", observer_mod)
        self.assertIn("implements IWorldGenerator", final_checkpoint)
        self.assertNotIn("setBlockState", final_checkpoint)

    def test_wrapper_delegates_lifecycle_without_reposting_hooks(self):
        wrapper = (OBSERVER / "world" / "ObservingChunkGenerator.java").read_text(
            encoding="utf-8"
        )
        world_type = (OBSERVER / "world" / "ObservatoryWorldType.java").read_text(
            encoding="utf-8"
        )

        for required_call in (
            "delegate.generateChunk(chunkX, chunkZ)",
            "delegate.populate(chunkX, chunkZ)",
            "delegate.generateStructures(chunk, chunkX, chunkZ)",
            "delegate.recreateStructures(chunk, chunkX, chunkZ)",
        ):
            self.assertIn(required_call, wrapper)

        self.assertIn("new ChunkGeneratorOverworld", world_type)
        for duplicate_hook in (
            "MinecraftForge.EVENT_BUS.post",
            "ForgeEventFactory.onChunkPopulate",
            "TerrainGen.populate",
            "new PopulateChunkEvent",
            "new DecorateBiomeEvent",
        ):
            self.assertNotIn(duplicate_hook, wrapper)

    def test_observer_delegate_override_is_strict_and_harness_only(self):
        world_type = (OBSERVER / "world" / "ObservatoryWorldType.java").read_text(
            encoding="utf-8"
        )
        wrapper = (OBSERVER / "world" / "ObservingChunkGenerator.java").read_text(
            encoding="utf-8"
        )

        self.assertIn('DELEGATE_OPTION = "workbench-generator-class="', world_type)
        self.assertIn("generatorOptions.isEmpty()", world_type)
        self.assertIn("new ChunkGeneratorOverworld", world_type)
        self.assertIn("Loader.instance().getModClassLoader()", world_type)
        self.assertIn("rawClass.asSubclass", world_type)
        self.assertIn("getConstructor(\n                    World.class", world_type)
        self.assertNotIn("setAccessible", world_type)
        self.assertIn(
            'this.delegateDecision = "delegate=" + delegate.getClass().getName()',
            wrapper,
        )

    def test_trace_records_have_stable_kinds_and_field_order(self):
        record = (OBSERVER / "trace" / "TraceRecord.java").read_text(encoding="utf-8")
        cooperative_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(OBSERVER.rglob("*.java"))
            if PROBE not in path.parents
        )

        for kind in ('"stage"', '"decision"', '"rng"', '"checkpoint"'):
            self.assertIn(kind, record)

        fields = (
            '"schema"',
            '"kind"',
            '"stage"',
            '"decision"',
            '"world_seed"',
            '"dimension"',
            '"chunk_x"',
            '"chunk_z"',
            '"rng_lane"',
            '"rng_seed"',
            '"checkpoint"',
            '"owner"',
        )
        offsets = [record.index(field, record.index("public String toJson()")) for field in fields]
        self.assertEqual(sorted(offsets), offsets)

        for unstable_identity in (
            "System.currentTimeMillis",
            "System.nanoTime",
            "Instant.now",
            "UUID.randomUUID",
        ):
            self.assertNotIn(unstable_identity, cooperative_sources)

    def test_disabled_tracer_is_a_real_no_op(self):
        config = (OBSERVER / "config" / "ObservatoryConfig.java").read_text(
            encoding="utf-8"
        )
        sinks = (OBSERVER / "trace" / "TraceSinks.java").read_text(encoding="utf-8")

        self.assertIn("public static boolean enabled = false", config)
        self.assertIn("return ObservatoryConfig.enabled ? LOGGING : NO_OP", sinks)
        self.assertIn("class NoOpTraceSink", sinks)
        self.assertIn("return false", sinks)

    def test_rng_lanes_match_fixed_vectors(self):
        source = (OBSERVER / "trace" / "RngLanes.java").read_text(encoding="utf-8")
        constants = dict(
            (name, int(value, 16))
            for name, value in re.findall(r"^\s*([A-Z_]+)\(0x([0-9A-F]+)L\)", source, re.MULTILINE)
        )
        self.assertEqual(
            {"TERRAIN", "POPULATION", "FINAL_CHECKPOINT"},
            set(constants),
        )
        self.assertEqual(3, len(set(constants.values())))

        mask = (1 << 64) - 1

        def mix64(value):
            value &= mask
            value ^= value >> 30
            value = (value * 0xBF58476D1CE4E5B9) & mask
            value ^= value >> 27
            value = (value * 0x94D049BB133111EB) & mask
            value ^= value >> 31
            return value & mask

        def lane_seed(salt):
            value = mix64((-571123474424848392 & mask) ^ salt)
            value = mix64(value ^ (0x9E3779B97F4A7C15 * 0 & mask))
            value = mix64(value ^ (0xC2B2AE3D27D4EB4F * (-17 & mask) & mask))
            return mix64(value ^ (0x165667B19E3779F9 * (23 & mask) & mask))

        self.assertEqual(
            {
                "TERRAIN": 0xDB1A9A3B1A510536,
                "POPULATION": 0x813227CDEC9EB442,
                "FINAL_CHECKPOINT": 0x898D329FF1E268C1,
            },
            {name: lane_seed(salt) for name, salt in constants.items()},
        )

    def test_synthetic_write_has_generic_attribution(self):
        record = (SYNTHETIC / "SyntheticWriteRecord.java").read_text(encoding="utf-8")
        generator = (SYNTHETIC / "SyntheticWorldGenerator.java").read_text(encoding="utf-8")

        self.assertIn("workbench.worldgen-write.v1", record)
        self.assertIn('\\"owner\\"', record)
        self.assertIn('\\"source\\":\\"forge.iworldgenerator\\"', record)
        self.assertIn("world.setBlockState(position, marker, 2)", generator)


if __name__ == "__main__":
    unittest.main()
