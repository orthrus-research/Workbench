package dev.workbench.worldgenobservatory.probe;

import dev.workbench.worldgenobservatory.evidence.SourceArtifactDigest;
import dev.workbench.worldgenobservatory.probe.mixin.ASMEventHandlerAccess;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fml.common.Loader;
import net.minecraftforge.fml.common.ModContainer;
import net.minecraftforge.fml.common.eventhandler.Event;
import net.minecraftforge.fml.common.eventhandler.IEventListener;

import javax.annotation.Nullable;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.security.CodeSource;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.Collections;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Synchronous, append-only raw observer used only by the exact-candidate
 * fixture.  Every public entry point is fail-open: observer failures are
 * reported where possible and never replace a Minecraft/Forge throwable.
 *
 * <p>The raw transport is intentionally not the admitted Crucible capture
 * contract.  A normalizer must bind run/profile/snapshot identities and actor
 * evidence before records can enter that contract.</p>
 */
public final class ProbeRuntime {

    public static final String RAW_FORMAT = "workbench-cleanroom-worldgen-observatory-raw-v1";
    public static final String ENABLE_PROPERTY = "workbench.worldgen.observatory.probe.enabled";
    public static final String OUTPUT_PROPERTY = "workbench.worldgen.observatory.probe.output";
    public static final String CAPTURE_ID_PROPERTY = "workbench.worldgen.observatory.probe.capture_id";
    public static final String MODE_PROPERTY = "workbench.worldgen.observatory.probe.mode";
    public static final String DEFER_PROPERTY =
        "workbench.worldgen.observatory.probe.defer_until_fixture_driver";
    public static final String FIXTURE_DRIVER_ENABLE_PROPERTY =
        "workbench.worldgen.observatory.fixture_driver.enabled";
    public static final String ITERATION_AUTO_ENABLE_PROPERTY =
        "workbench.worldgen.observatory.iteration_auto.enabled";
    public static final String ITERATION_MIN_CHUNK_X_PROPERTY =
        "workbench.worldgen.observatory.iteration_auto.min_chunk_x";
    public static final String ITERATION_MIN_CHUNK_Z_PROPERTY =
        "workbench.worldgen.observatory.iteration_auto.min_chunk_z";
    public static final String ITERATION_MAX_CHUNK_X_PROPERTY =
        "workbench.worldgen.observatory.iteration_auto.max_chunk_x_exclusive";
    public static final String ITERATION_MAX_CHUNK_Z_PROPERTY =
        "workbench.worldgen.observatory.iteration_auto.max_chunk_z_exclusive";

    private static final boolean ENABLED = safeBooleanProperty(ENABLE_PROPERTY, false);
    private static final boolean DRIVER_MANAGED =
        safeBooleanProperty(FIXTURE_DRIVER_ENABLE_PROPERTY, false);
    private static final boolean ITERATION_AUTO_MANAGED =
        safeBooleanProperty(ITERATION_AUTO_ENABLE_PROPERTY, false);
    private static volatile boolean CAPTURE_ARMED =
        !ITERATION_AUTO_MANAGED
            && !DRIVER_MANAGED && !safeBooleanProperty(DEFER_PROPERTY, false);
    private static final int ITERATION_MIN_CHUNK_X =
        safeIntegerProperty(ITERATION_MIN_CHUNK_X_PROPERTY, Integer.MIN_VALUE);
    private static final int ITERATION_MIN_CHUNK_Z =
        safeIntegerProperty(ITERATION_MIN_CHUNK_Z_PROPERTY, Integer.MIN_VALUE);
    private static final int ITERATION_MAX_CHUNK_X =
        safeIntegerProperty(ITERATION_MAX_CHUNK_X_PROPERTY, Integer.MAX_VALUE);
    private static final int ITERATION_MAX_CHUNK_Z =
        safeIntegerProperty(ITERATION_MAX_CHUNK_Z_PROPERTY, Integer.MAX_VALUE);
    private static final String CAPTURE_ID = safeNonEmptyProperty(CAPTURE_ID_PROPERTY, "unbound");
    private static final String MODE = safeMode();
    private static final AtomicLong RECORD_SEQUENCE = new AtomicLong();
    private static final AtomicLong SPAN_SEQUENCE = new AtomicLong();
    private static final AtomicLong WRITE_SEQUENCE = new AtomicLong();
    private static final AtomicLong LAMPORT = new AtomicLong();
    private static final ThreadLocal<long[]> THREAD_SEQUENCE = ThreadLocal.withInitial(() -> new long[1]);
    private static final ThreadLocal<Deque<SpanToken>> SPANS = ThreadLocal.withInitial(ArrayDeque::new);
    private static final ThreadLocal<Deque<WriteToken>> WRITES = ThreadLocal.withInitial(ArrayDeque::new);
    private static final ThreadLocal<Boolean> IN_OBSERVER = ThreadLocal.withInitial(() -> Boolean.FALSE);
    private static final Set<String> REACHED = ConcurrentHashMap.newKeySet();
    private static final Set<String> HEALTH_EMITTED = ConcurrentHashMap.newKeySet();
    private static final Map<String, HealthObservation> HEALTH_OBSERVATIONS = new ConcurrentHashMap<>();
    private static final Map<String, TargetBinding> TARGET_BINDINGS = new ConcurrentHashMap<>();
    private static final Map<String, String> CODE_SOURCE_DIGESTS = new ConcurrentHashMap<>();
    private static final Map<String, String> MOD_ID_BY_SOURCE = new ConcurrentHashMap<>();
    private static final Map<String, String> DECLARED_METHOD_DESCRIPTORS = new ConcurrentHashMap<>();
    private static final String AMBIGUOUS = "<ambiguous>";
    private static final RawWriter WRITER = RawWriter.open();

    private ProbeRuntime() {
    }

    private static boolean safeBooleanProperty(String name, boolean fallback) {
        try {
            return Boolean.parseBoolean(System.getProperty(name, Boolean.toString(fallback)));
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static String safeProperty(String name, String fallback) {
        try {
            return System.getProperty(name, fallback);
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static int safeIntegerProperty(String name, int fallback) {
        try {
            return Integer.parseInt(System.getProperty(name, Integer.toString(fallback)));
        } catch (Throwable ignored) {
            return fallback;
        }
    }

    private static String safeNonEmptyProperty(String name, String fallback) {
        String value = safeProperty(name, fallback);
        return value == null || value.trim().isEmpty() ? fallback : value;
    }

    private static String safeMode() {
        String value = safeNonEmptyProperty(MODE_PROPERTY, "lossless-fixture");
        switch (value) {
            case "summary":
            case "trace":
            case "forensic":
            case "lossless-fixture":
                return value;
            default:
                return "lossless-fixture";
        }
    }

    public static boolean enabled() {
        return CAPTURE_ARMED && transportEnabled();
    }

    public static boolean requested() {
        return ENABLED;
    }

    public static boolean transportEnabled() {
        return ENABLED && WRITER.available();
    }

    /**
     * Arm an externally bounded Worldgen Iteration capture.
     *
     * <p>The existing raw V1 stream deliberately receives no synthetic V1
     * fixture-control record here.  A separate V2 adapter validates the
     * prelaunch envelope, process result, raw prefix, and Strata receipt after
     * the process exits.  This preserves the identity-bearing V1 controller
     * meaning verbatim.</p>
     */
    public static boolean armForWorldgenIteration() {
        if (
            !ITERATION_AUTO_MANAGED
                || DRIVER_MANAGED
                || !transportEnabled()
                || IN_OBSERVER.get()
        ) {
            return false;
        }
        if (
            ITERATION_MIN_CHUNK_X >= ITERATION_MAX_CHUNK_X
                || ITERATION_MIN_CHUNK_Z >= ITERATION_MAX_CHUNK_Z
        ) {
            return false;
        }
        CAPTURE_ARMED = true;
        List<String> hookIds = new java.util.ArrayList<>(HEALTH_OBSERVATIONS.keySet());
        Collections.sort(hookIds);
        for (String hookId : hookIds) {
            emitHealth(HEALTH_OBSERVATIONS.get(hookId));
        }
        boolean ready = transportEnabled();
        if (!ready) {
            CAPTURE_ARMED = false;
        }
        return ready;
    }

    public static void stopWorldgenIterationCapture() {
        if (!ITERATION_AUTO_MANAGED) {
            return;
        }
        CAPTURE_ARMED = false;
        SPANS.remove();
        WRITES.remove();
    }

    public static boolean armForFixtureDriver(
        long worldSeed,
        int dimension,
        String routeOrder,
        String selectorSha256,
        String routeSha256,
        List<String> selectedChunks
    ) {
        if (ITERATION_AUTO_MANAGED || !transportEnabled() || IN_OBSERVER.get()) {
            return false;
        }
        CAPTURE_ARMED = true;
        boolean started = emit(
            "capture_control",
            contextualSpan(scope(dimension, null, null), Actor.workbench(
                "dev.workbench.worldgenobservatory.fixture.DedicatedServerFixtureDriver",
                "run",
                "(Lnet/minecraft/server/MinecraftServer;)V"
            )),
            Actor.workbench(
                "dev.workbench.worldgenobservatory.fixture.DedicatedServerFixtureDriver",
                "run",
                "(Lnet/minecraft/server/MinecraftServer;)V"
            ),
            "entered",
            null,
            fields(
                "control", "start",
                "capture_control_id", CAPTURE_ID + ":dedicated_server_fixture_v1",
                "controller", "dedicated_server_fixture_v1",
                "requested_mode", MODE,
                "world_seed_sha256", digestText(worldSeed),
                "route_order", routeOrder,
                "selector_sha256", selectorSha256,
                "route_sha256", routeSha256,
                "selected_chunks", selectedChunks
            )
        );
        if (!started) {
            CAPTURE_ARMED = false;
            return false;
        }

        List<String> hookIds = new java.util.ArrayList<>(HEALTH_OBSERVATIONS.keySet());
        Collections.sort(hookIds);
        for (String hookId : hookIds) {
            emitHealth(HEALTH_OBSERVATIONS.get(hookId));
        }
        boolean ready = transportEnabled();
        if (!ready) {
            CAPTURE_ARMED = false;
        }
        return ready;
    }

    public static boolean stopFixtureCapture(
        int dimension,
        String routeOrder,
        String routeSha256,
        boolean complete
    ) {
        if (!enabled()) {
            CAPTURE_ARMED = false;
            return false;
        }
        int openSpans = SPANS.get().size();
        int openWrites = WRITES.get().size();
        boolean balanced = openSpans == 0 && openWrites == 0;
        boolean emitted;
        try {
            Actor actor = Actor.workbench(
                "dev.workbench.worldgenobservatory.fixture.DedicatedServerFixtureDriver",
                "run",
                "(Lnet/minecraft/server/MinecraftServer;)V"
            );
            emitted = emit(
                "capture_control",
                contextualSpan(scope(dimension, null, null), actor),
                actor,
                complete && balanced ? "returned" : "incomplete",
                null,
                fields(
                    "control", "stop",
                    "capture_control_id", CAPTURE_ID + ":dedicated_server_fixture_v1",
                    "controller", "dedicated_server_fixture_v1",
                    "requested_mode", MODE,
                    "route_order", routeOrder,
                    "route_sha256", routeSha256,
                    "completion_state", complete ? "complete" : "incomplete",
                    "open_span_count", openSpans,
                    "open_write_count", openWrites
                )
            );
        } finally {
            CAPTURE_ARMED = false;
            SPANS.remove();
            WRITES.remove();
        }
        return emitted && balanced && transportEnabled();
    }

    public static void fixtureDriverStarted(
        long worldSeed,
        int dimension,
        String routeOrder,
        String routeSha256,
        List<String> selectedChunks
    ) {
        if (!enabled()) {
            return;
        }
        Actor actor = fixtureDriverActor();
        emit(
            "fixture_driver",
            contextualSpan(scope(dimension, null, null), actor),
            actor,
            "entered",
            null,
            fields(
                "action", "route_started",
                "world_seed_sha256", digestText(worldSeed),
                "route_order", routeOrder,
                "route_sha256", routeSha256,
                "selected_chunks", selectedChunks
            )
        );
    }

    public static boolean fixtureDriverCompleted(
        long worldSeed,
        int dimension,
        String routeOrder,
        String routeSha256,
        String resultIdentitySha256,
        List<String> selectedChunks
    ) {
        if (!enabled()) {
            return false;
        }
        Actor actor = fixtureDriverActor();
        return emit(
            "fixture_driver",
            contextualSpan(scope(dimension, null, null), actor),
            actor,
            "returned",
            null,
            fields(
                "action", "complete",
                "completion_marker", "dedicated_server_fixture_complete_v1",
                "world_seed_sha256", digestText(worldSeed),
                "route_order", routeOrder,
                "route_sha256", routeSha256,
                "result_identity_sha256", resultIdentitySha256,
                "selected_chunks", selectedChunks,
                "save_state", "flushed",
                "shutdown_state", "requested"
            )
        );
    }

    public static void fixtureDriverFailed(
        int dimension,
        String routeOrder,
        String routeSha256,
        Throwable original
    ) {
        if (!enabled()) {
            return;
        }
        Actor actor = fixtureDriverActor();
        emit(
            "fixture_driver",
            contextualSpan(scope(dimension, null, null), actor),
            actor,
            "incomplete",
            original,
            fields(
                "action", "failed",
                "route_order", routeOrder,
                "route_sha256", routeSha256,
                "completion_marker", null
            )
        );
    }

    public static void cooperativeStage(
        String stageId,
        String boundary,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner
    ) {
        cooperativeEmit(
            "cooperative_stage",
            stageId,
            worldSeed,
            dimension,
            chunkX,
            chunkZ,
            owner,
            fields("stage_id", stageId, "boundary", boundary)
        );
    }

    public static void cooperativeDecision(
        String stageId,
        String decision,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner
    ) {
        cooperativeEmit(
            "decision",
            stageId,
            worldSeed,
            dimension,
            chunkX,
            chunkZ,
            owner,
            fields(
                "stage_id", stageId,
                "rule_id", digestParts(stageId, "cooperative-decision"),
                "input_state_sha256", digestParts(worldSeed, dimension, chunkX, chunkZ, stageId),
                "decision", decision,
                "output_state_sha256", digestParts(decision)
            )
        );
    }

    public static void cooperativeRng(
        String stageId,
        String lane,
        long namedSeed,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner
    ) {
        cooperativeEmit(
            "rng_observation",
            stageId,
            worldSeed,
            dimension,
            chunkX,
            chunkZ,
            owner,
            fields(
                "stage_id", stageId,
                "lane_id", lane,
                "stream_id", stageId + ":" + lane,
                "algorithm", "workbench.rng-lanes.mix64.v1",
                "operation", "derive_named_seed_without_consuming_runtime_random",
                "parameters_sha256", digestParts(worldSeed, dimension, chunkX, chunkZ, lane),
                "observed_seed", namedSeed,
                "result_sha256", digestParts(namedSeed),
                "rolling_digest_sha256", digestParts(stageId, lane, namedSeed)
            )
        );
    }

    public static void cooperativeCheckpoint(
        String stageId,
        String semanticStateSha256,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner
    ) {
        cooperativeEmit(
            "checkpoint",
            stageId,
            worldSeed,
            dimension,
            chunkX,
            chunkZ,
            owner,
            fields(
                "checkpoint_id", stageId,
                "stage_id", stageId,
                "canonicalization_id", "chunk-block-state-registry-name-metadata-yzx-v1",
                "semantic_state_sha256", semanticStateSha256
            )
        );
    }

    public static void cooperativeFailure(
        String code,
        String stageId,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner,
        Throwable failure
    ) {
        if (!enabled()) {
            return;
        }
        try {
            Actor actor = cooperativeCaller(owner);
            emit(
                "diagnostic",
                contextualSpan(scope(dimension, chunkX, chunkZ), actor),
                actor,
                "incomplete",
                failure,
                fields(
                    "code", code,
                    "stage_id", stageId,
                    "world_seed_sha256", digestText(worldSeed),
                    "severity", "error"
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW925_COOPERATIVE_DIAGNOSTIC_FAILED", observerFailure);
        }
    }

    public static boolean hasActiveWorldgenSpan() {
        return enabled() && !SPANS.get().isEmpty();
    }

    public static Scope currentScope() {
        Deque<SpanToken> spans = SPANS.get();
        return spans.isEmpty() ? Scope.EMPTY : spans.peek().scope;
    }

    public static SpanToken enter(
        String hookId,
        String spanKind,
        Actor actor,
        Scope requestedScope,
        Object... argumentParts
    ) {
        if (!enabled() || IN_OBSERVER.get()) {
            return SpanToken.NO_OP;
        }
        try {
            Deque<SpanToken> stack = SPANS.get();
            SpanToken parent = stack.peek();
            Scope scope = requestedScope.inherit(parent == null ? Scope.EMPTY : parent.scope);
            if (ITERATION_AUTO_MANAGED && !acceptIterationSpan(hookId)) {
                return SpanToken.NO_OP;
            }
            if (parent == null && !acceptIterationRoot(hookId, scope)) {
                return SpanToken.NO_OP;
            }
            long spanOrdinal = SPAN_SEQUENCE.getAndIncrement();
            String spanId = "cleanroom-worldgen-span:" + spanOrdinal;
            String traceId = parent == null
                ? "cleanroom-worldgen-trace:" + spanOrdinal
                : parent.traceId;
            String rootTriggerId = parent == null ? spanId : parent.rootTriggerId;
            SpanToken token = new SpanToken(
                true,
                hookId,
                spanKind,
                traceId,
                rootTriggerId,
                spanId,
                parent == null ? null : parent.spanId,
                scope,
                actor
            );
            stack.push(token);
            reach(hookId);
            emit(
                "span_enter",
                token,
                actor,
                "entered",
                null,
                fields(
                    "span_kind", spanKind,
                    "operation_id", hookId,
                    "arguments_sha256", digestParts(argumentParts)
                )
            );
            return token;
        } catch (Throwable observerFailure) {
            observerFailure("CRW901_SPAN_ENTER_FAILED", observerFailure);
            return SpanToken.NO_OP;
        }
    }

    private static boolean acceptIterationRoot(String hookId, Scope scope) {
        if (!ITERATION_AUTO_MANAGED) {
            return true;
        }
        if (
            !"cleanroom-worldgen:chunk.populate_neighbors".equals(hookId)
                && !"cleanroom-worldgen:chunk.populate_owned".equals(hookId)
        ) {
            return false;
        }
        return scope.chunkX != null
            && scope.chunkZ != null
            && scope.chunkX >= ITERATION_MIN_CHUNK_X
            && scope.chunkX < ITERATION_MAX_CHUNK_X
            && scope.chunkZ >= ITERATION_MIN_CHUNK_Z
            && scope.chunkZ < ITERATION_MAX_CHUNK_Z;
    }

    private static boolean acceptIterationSpan(String hookId) {
        return "cleanroom-worldgen:chunk.populate_neighbors".equals(hookId)
            || "cleanroom-worldgen:chunk.populate_owned".equals(hookId)
            || "cleanroom-worldgen:chunk.generator_populate_call".equals(hookId)
            || "cleanroom-worldgen:event_bus.post".equals(hookId)
            || "cleanroom-worldgen:event_bus.listener_invoke".equals(hookId);
    }

    public static void returned(SpanToken token, Object... resultParts) {
        if (!token.active) {
            return;
        }
        try {
            emit(
                "span_return",
                token,
                token.actor,
                "returned",
                null,
                fields(
                    "span_kind", token.spanKind,
                    "result_sha256", digestParts(resultParts)
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW902_SPAN_RETURN_FAILED", observerFailure);
        } finally {
            popSpan(token);
        }
    }

    public static void threw(SpanToken token, Throwable original) {
        if (!token.active) {
            return;
        }
        try {
            emit(
                "span_throw",
                token,
                token.actor,
                "threw",
                original,
                fields(
                    "span_kind", token.spanKind,
                    "throwable_state_sha256", digestParts(
                        original.getClass().getName(),
                        original.getMessage()
                    )
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW903_SPAN_THROW_FAILED", observerFailure);
        } finally {
            popSpan(token);
        }
    }

    public static void chunkAccess(
        SpanToken token,
        String accessKind,
        String apiId,
        int requestedX,
        int requestedZ,
        boolean loadedBefore,
        String result
    ) {
        if (!token.active) {
            return;
        }
        try {
            Scope generation = parentScope(token);
            emit(
                "chunk_access",
                token,
                token.actor,
                "observed",
                null,
                fields(
                    "access_kind", accessKind,
                    "api_id", apiId,
                    "current_generation_chunk_x", generation.chunkX,
                    "current_generation_chunk_z", generation.chunkZ,
                    "requested_chunk_x", requestedX,
                    "requested_chunk_z", requestedZ,
                    "loaded_before", loadedBefore,
                    "result", result
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW904_CHUNK_ACCESS_FAILED", observerFailure);
        }
    }

    public static void eventBoundary(
        String hookId,
        String busId,
        Event event,
        String boundary,
        @Nullable IEventListener listener,
        @Nullable Integer listenerOrdinal,
        EventState before,
        EventState after,
        @Nullable Throwable original
    ) {
        if (!hasActiveWorldgenSpan() || IN_OBSERVER.get()) {
            return;
        }
        try {
            reach(hookId);
            SpanToken span = SPANS.get().peek();
            Actor actor = listener == null
                ? Actor.target(
                    "net.minecraftforge.fml.common.eventhandler.EventBus",
                    "post",
                    "(Lnet/minecraftforge/fml/common/eventhandler/Event;)Z"
                )
                : Actor.forListener(listener);
            String outcome = original != null ? "threw" : (after.cancelled ? "canceled" : "observed");
            emit(
                "event_dispatch",
                span,
                actor,
                outcome,
                original,
                fields(
                    "event_class", event.getClass().getName(),
                    "bus_id", busId,
                    "boundary", boundary,
                    "listener_ordinal", listenerOrdinal,
                    "listener", listener == null ? null : safeListenerName(listener),
                    "state_before_sha256", before.digest,
                    "state_after_sha256", after.digest,
                    "cancelled_before", before.cancelled,
                    "cancelled_after", after.cancelled,
                    "result_before", before.result,
                    "result_after", after.result
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW905_EVENT_RECORD_FAILED", observerFailure);
        }
    }

    public static EventState eventState(Event event) {
        try {
            boolean cancelled = event.isCanceled();
            String result = String.valueOf(event.getResult());
            return new EventState(cancelled, result, stableEventStateDigest(event));
        } catch (Throwable observerFailure) {
            observerFailure("CRW906_EVENT_STATE_FAILED", observerFailure);
            return EventState.UNAVAILABLE;
        }
    }

    public static WriteToken beginWrite(
        String hookId,
        String channel,
        int x,
        int y,
        int z,
        @Nullable IBlockState before,
        @Nullable IBlockState requested,
        @Nullable Integer flags,
        String targetClass
    ) {
        if (!hasActiveWorldgenSpan() || IN_OBSERVER.get()) {
            return WriteToken.NO_OP;
        }
        try {
            Deque<WriteToken> stack = WRITES.get();
            WriteToken chainParent = null;
            for (WriteToken candidate : stack) {
                if (candidate.x == x && candidate.y == y && candidate.z == z) {
                    chainParent = candidate;
                    break;
                }
            }
            Actor actor = "chunk_storage".equals(channel)
                ? Actor.target(
                    targetClass,
                    "setBlockState",
                    "(Lnet/minecraft/util/math/BlockPos;Lnet/minecraft/block/state/IBlockState;)"
                        + "Lnet/minecraft/block/state/IBlockState;"
                )
                : Actor.caller(targetClass);
            if (
                ITERATION_AUTO_MANAGED
                    && !(
                        ("world_api".equals(channel)
                            && "dev.workbench.worldgenprototype.world.PrototypeFeature"
                                .equals(actor.className))
                        || ("chunk_storage".equals(channel) && chainParent != null)
                    )
            ) {
                return WriteToken.NO_OP;
            }
            String chainId = chainParent == null
                ? "cleanroom-worldgen-write:" + WRITE_SEQUENCE.getAndIncrement()
                : chainParent.chainId;
            WriteToken token = new WriteToken(
                true,
                hookId,
                channel,
                chainId,
                chainParent == null ? 0 : chainParent.depth + 1,
                x,
                y,
                z,
                stateDigest(before),
                stateDigest(requested),
                flags,
                actor
            );
            if (chainParent != null) {
                chainParent.childSeen = true;
            }
            stack.push(token);
            reach(hookId);
            return token;
        } catch (Throwable observerFailure) {
            observerFailure("CRW907_WRITE_ENTER_FAILED", observerFailure);
            return WriteToken.NO_OP;
        }
    }

    public static void finishWrite(
        WriteToken token,
        @Nullable IBlockState observedBefore,
        @Nullable IBlockState after,
        boolean terminal,
        @Nullable Throwable original
    ) {
        if (!token.active) {
            return;
        }
        try {
            SpanToken span = SPANS.get().peek();
            Scope generation = span == null ? Scope.EMPTY : span.scope;
            String beforeDigest = observedBefore == null ? token.beforeDigest : stateDigest(observedBefore);
            emit(
                "block_write",
                span,
                token.actor,
                original == null ? "observed" : "threw",
                original,
                fields(
                    "hook_id", token.hookId,
                    "write_chain_id", token.chainId,
                    "chain_depth", token.depth,
                    "channel", token.channel,
                    "position_x", token.x,
                    "position_y", token.y,
                    "position_z", token.z,
                    "generation_chunk_x", generation.chunkX,
                    "generation_chunk_z", generation.chunkZ,
                    "target_chunk_x", token.x >> 4,
                    "target_chunk_z", token.z >> 4,
                    "before_state_sha256", beforeDigest,
                    "requested_state_sha256", token.requestedDigest,
                    "after_state_sha256", stateDigest(after),
                    "flags", token.flags,
                    "terminal", terminal && !token.childSeen
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW908_WRITE_RETURN_FAILED", observerFailure);
        } finally {
            popWrite(token);
        }
    }

    public static void registerTargetBinding(
        String targetClass,
        String originalDigest,
        String transformedDigest
    ) {
        try {
            TARGET_BINDINGS.put(targetClass, new TargetBinding(originalDigest, transformedDigest));
        } catch (Throwable observerFailure) {
            observerFailure("CRW909_BINDING_REGISTRATION_FAILED", observerFailure);
        }
    }

    public static void probeHealth(
        String hookId,
        String targetClass,
        String targetMethod,
        String targetDescriptor,
        String originalDigest,
        String transformedDigest,
        int expected,
        int observed
    ) {
        try {
            HealthObservation health = new HealthObservation(
                hookId,
                targetClass,
                targetMethod,
                targetDescriptor,
                originalDigest,
                transformedDigest,
                expected,
                observed
            );
            HEALTH_OBSERVATIONS.put(hookId, health);
            if (enabled()) {
                emitHealth(health);
            }
        } catch (Throwable observerFailure) {
            observerFailure("CRW910_PROBE_HEALTH_FAILED", observerFailure);
        }
    }

    public static Scope scope(@Nullable Integer dimension, @Nullable Integer chunkX, @Nullable Integer chunkZ) {
        return new Scope(dimension, chunkX, chunkZ);
    }

    private static void emitHealth(@Nullable HealthObservation health) {
        if (health == null || !enabled() || !HEALTH_EMITTED.add(health.hookId)) {
            return;
        }
        emit(
            "probe_health",
            SPANS.get().peek(),
            Actor.workbench(
                ProbeRuntime.class.getName(),
                "emitHealth",
                "(Ldev/workbench/worldgenobservatory/probe/ProbeRuntime$HealthObservation;)V"
            ),
            health.expected == health.observed ? "observed" : "incomplete",
            null,
            fields(
                "hook_id", health.hookId,
                "health_state", health.expected == health.observed ? "applied_not_reached" : "failed",
                "target_class", health.targetClass,
                "target_method", health.targetMethod,
                "target_descriptor", health.targetDescriptor,
                "original_class_sha256", health.originalDigest,
                "transformed_class_sha256", health.transformedDigest,
                "expected_injection_count", health.expected,
                "observed_injection_count", health.observed
            )
        );
    }

    public static IBlockState safePrimerState(net.minecraft.world.chunk.ChunkPrimer primer, int x, int y, int z) {
        try {
            return primer.getBlockState(x, y, z);
        } catch (Throwable observerFailure) {
            observerFailure("CRW911_PRIMER_READ_FAILED", observerFailure);
            return null;
        }
    }

    public static int primerWorldX(int localX) {
        try {
            Scope scope = currentScope();
            return scope.chunkX == null ? localX : (scope.chunkX << 4) + localX;
        } catch (Throwable observerFailure) {
            observerFailure("CRW918_PRIMER_X_FAILED", observerFailure);
            return localX;
        }
    }

    public static int primerWorldZ(int localZ) {
        try {
            Scope scope = currentScope();
            return scope.chunkZ == null ? localZ : (scope.chunkZ << 4) + localZ;
        } catch (Throwable observerFailure) {
            observerFailure("CRW919_PRIMER_Z_FAILED", observerFailure);
            return localZ;
        }
    }

    public static String className(@Nullable Object value) {
        try {
            return value == null ? "null" : value.getClass().getName();
        } catch (Throwable observerFailure) {
            observerFailure("CRW920_CLASS_NAME_FAILED", observerFailure);
            return "unavailable";
        }
    }

    public static String digestParts(Object... parts) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (Object part : parts) {
                byte[] bytes = String.valueOf(part).getBytes(StandardCharsets.UTF_8);
                digest.update((byte) (bytes.length >>> 24));
                digest.update((byte) (bytes.length >>> 16));
                digest.update((byte) (bytes.length >>> 8));
                digest.update((byte) bytes.length);
                digest.update(bytes);
            }
            return hex(digest.digest());
        } catch (Throwable failure) {
            return zeros();
        }
    }

    private static String digestText(Object value) {
        try {
            byte[] bytes = String.valueOf(value).getBytes(StandardCharsets.UTF_8);
            return hex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (Throwable failure) {
            return zeros();
        }
    }

    @SuppressWarnings("unchecked")
    public static <T extends Throwable, R> R sneakyThrow(Throwable throwable) throws T {
        throw (T) throwable;
    }

    private static void reach(String hookId) {
        if (!REACHED.add(hookId)) {
            return;
        }
        TargetBinding binding = TARGET_BINDINGS.getOrDefault(hookId, TargetBinding.UNKNOWN);
        SpanToken span = SPANS.get().peek();
        emit(
            "probe_health",
            span,
            span == null ? Actor.UNBOUND : span.actor,
            "observed",
            null,
            fields(
                "hook_id", hookId,
                "health_state", "reached",
                "target_class", binding.targetClass,
                "target_method", binding.targetMethod,
                "target_descriptor", binding.targetDescriptor,
                "original_class_sha256", binding.originalDigest,
                "transformed_class_sha256", binding.transformedDigest,
                "expected_injection_count", binding.expected,
                "observed_injection_count", binding.observed
            )
        );
    }

    public static void registerHookBinding(
        String hookId,
        String targetClass,
        String targetMethod,
        String targetDescriptor,
        String originalDigest,
        String transformedDigest,
        int expected,
        int observed
    ) {
        try {
            TARGET_BINDINGS.put(
                hookId,
                new TargetBinding(
                    targetClass,
                    targetMethod,
                    targetDescriptor,
                    originalDigest,
                    transformedDigest,
                    expected,
                    observed
                )
            );
        } catch (Throwable observerFailure) {
            observerFailure("CRW912_HOOK_BINDING_FAILED", observerFailure);
        }
    }

    private static void popSpan(SpanToken token) {
        try {
            Deque<SpanToken> stack = SPANS.get();
            if (stack.peek() == token) {
                stack.pop();
            } else {
                stack.remove(token);
                observerFailure(
                    "CRW913_UNBALANCED_SPAN",
                    new IllegalStateException("span stack mismatch for " + token.hookId)
                );
            }
            if (stack.isEmpty()) {
                SPANS.remove();
            }
        } catch (Throwable observerFailure) {
            observerFailure("CRW914_SPAN_POP_FAILED", observerFailure);
        }
    }

    private static void popWrite(WriteToken token) {
        try {
            Deque<WriteToken> stack = WRITES.get();
            if (stack.peek() == token) {
                stack.pop();
            } else {
                stack.remove(token);
                observerFailure(
                    "CRW915_UNBALANCED_WRITE_CHAIN",
                    new IllegalStateException("write stack mismatch for " + token.hookId)
                );
            }
            if (stack.isEmpty()) {
                WRITES.remove();
            }
        } catch (Throwable observerFailure) {
            observerFailure("CRW916_WRITE_POP_FAILED", observerFailure);
        }
    }

    private static Scope parentScope(SpanToken token) {
        Deque<SpanToken> stack = SPANS.get();
        boolean found = false;
        for (SpanToken frame : stack) {
            if (found) {
                return frame.scope;
            }
            if (frame == token) {
                found = true;
            }
        }
        return Scope.EMPTY;
    }

    private static String stateDigest(@Nullable IBlockState state) {
        if (state == null) {
            return digestParts("unavailable");
        }
        try {
            Block block = state.getBlock();
            ResourceLocation name = Block.REGISTRY.getNameForObject(block);
            return digestParts(name == null ? "unregistered" : name.toString(), block.getMetaFromState(state));
        } catch (Throwable observerFailure) {
            observerFailure("CRW917_STATE_IDENTITY_FAILED", observerFailure);
            return digestParts("unavailable");
        }
    }

    private static String safeListenerName(IEventListener listener) {
        try {
            return String.valueOf(listener).replaceAll("@[0-9a-fA-F]+", "@<identity>");
        } catch (Throwable ignored) {
            return listener.getClass().getName();
        }
    }

    private static String stableEventStateDigest(Event event) {
        try {
            StringBuilder material = new StringBuilder(event.getClass().getName());
            java.util.List<java.lang.reflect.Field> fields = new java.util.ArrayList<>();
            for (Class<?> type = event.getClass(); type != null; type = type.getSuperclass()) {
                fields.addAll(java.util.Arrays.asList(type.getDeclaredFields()));
            }
            fields.removeIf(field -> java.lang.reflect.Modifier.isStatic(field.getModifiers()));
            fields.sort(java.util.Comparator.comparing(
                field -> field.getDeclaringClass().getName() + "#" + field.getName()
            ));
            java.util.IdentityHashMap<Object, Boolean> seen = new java.util.IdentityHashMap<>();
            seen.put(event, Boolean.TRUE);
            for (java.lang.reflect.Field field : fields) {
                material.append('|')
                    .append(field.getDeclaringClass().getName())
                    .append('#')
                    .append(field.getName())
                    .append('=');
                try {
                    field.setAccessible(true);
                    appendStableValue(material, field.get(event), 0, seen);
                } catch (Throwable unavailable) {
                    material.append("<unavailable:").append(unavailable.getClass().getName()).append('>');
                }
            }
            return digestParts(material);
        } catch (Throwable observerFailure) {
            observerFailure("CRW922_EVENT_DIGEST_FAILED", observerFailure);
            return digestParts(event.getClass().getName(), "unavailable");
        }
    }

    private static void appendStableValue(
        StringBuilder material,
        @Nullable Object value,
        int depth,
        java.util.IdentityHashMap<Object, Boolean> seen
    ) {
        if (value == null) {
            material.append("null");
            return;
        }
        Class<?> type = value.getClass();
        if (value instanceof CharSequence
            || value instanceof Number
            || value instanceof Boolean
            || value instanceof Character
            || value instanceof Enum<?>
            || value instanceof ResourceLocation
            || value instanceof Class<?>) {
            material.append(type.getName()).append(':').append(value);
            return;
        }
        if (value instanceof net.minecraft.util.math.BlockPos) {
            net.minecraft.util.math.BlockPos position = (net.minecraft.util.math.BlockPos) value;
            material.append("BlockPos:")
                .append(position.getX()).append(',')
                .append(position.getY()).append(',')
                .append(position.getZ());
            return;
        }
        if (value instanceof net.minecraft.util.math.ChunkPos) {
            net.minecraft.util.math.ChunkPos chunk = (net.minecraft.util.math.ChunkPos) value;
            material.append("ChunkPos:").append(chunk.x).append(',').append(chunk.z);
            return;
        }
        if (value instanceof IBlockState) {
            material.append("IBlockState:").append(stateDigest((IBlockState) value));
            return;
        }
        if (type.isArray()) {
            if (seen.put(value, Boolean.TRUE) != null) {
                material.append(type.getName()).append(":<cycle>");
                return;
            }
            try {
                int length = java.lang.reflect.Array.getLength(value);
                material.append(type.getName()).append('[').append(length).append(':');
                int limit = Math.min(length, 128);
                for (int index = 0; index < limit; index++) {
                    if (index > 0) {
                        material.append(',');
                    }
                    appendStableValue(material, java.lang.reflect.Array.get(value, index), depth + 1, seen);
                }
                if (length > limit) {
                    material.append(",<truncated>");
                }
                material.append(']');
            } finally {
                seen.remove(value);
            }
            return;
        }
        if (value instanceof Iterable<?>) {
            if (seen.put(value, Boolean.TRUE) != null) {
                material.append(type.getName()).append(":<cycle>");
                return;
            }
            try {
                material.append(type.getName()).append('[');
                int count = 0;
                for (Object element : (Iterable<?>) value) {
                    if (count >= 128) {
                        material.append("<truncated>");
                        break;
                    }
                    if (count++ > 0) {
                        material.append(',');
                    }
                    appendStableValue(material, element, depth + 1, seen);
                }
                material.append(']');
            } finally {
                seen.remove(value);
            }
            return;
        }
        if (value instanceof Map<?, ?>) {
            if (seen.put(value, Boolean.TRUE) != null) {
                material.append(type.getName()).append(":<cycle>");
                return;
            }
            try {
                java.util.List<String> entries = new java.util.ArrayList<>();
                for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                    StringBuilder encoded = new StringBuilder();
                    appendStableValue(encoded, entry.getKey(), depth + 1, seen);
                    encoded.append('=');
                    appendStableValue(encoded, entry.getValue(), depth + 1, seen);
                    entries.add(encoded.toString());
                }
                Collections.sort(entries);
                material.append(type.getName()).append(entries);
            } finally {
                seen.remove(value);
            }
            return;
        }
        if (seen.put(value, Boolean.TRUE) != null) {
            material.append(type.getName()).append(":<cycle>");
            return;
        }
        try {
            Package valuePackage = type.getPackage();
            String packageName = valuePackage == null ? "" : valuePackage.getName();
            if (depth >= 1
                || !(packageName.startsWith("net.minecraft.world.gen")
                || packageName.startsWith("net.minecraftforge.event.terraingen"))) {
                material.append(type.getName());
                return;
            }
            material.append(type.getName()).append('{');
            java.util.List<java.lang.reflect.Field> nested = new java.util.ArrayList<>(
                java.util.Arrays.asList(type.getDeclaredFields())
            );
            nested.removeIf(field -> java.lang.reflect.Modifier.isStatic(field.getModifiers()));
            nested.sort(java.util.Comparator.comparing(java.lang.reflect.Field::getName));
            for (java.lang.reflect.Field field : nested) {
                material.append(field.getName()).append('=');
                try {
                    field.setAccessible(true);
                    appendStableValue(material, field.get(value), depth + 1, seen);
                } catch (Throwable unavailable) {
                    material.append("<unavailable:").append(unavailable.getClass().getName()).append('>');
                }
                material.append(';');
            }
            material.append('}');
        } finally {
            seen.remove(value);
        }
    }

    private static Actor fixtureDriverActor() {
        return Actor.workbench(
            "dev.workbench.worldgenobservatory.fixture.DedicatedServerFixtureDriver",
            "run",
            "(Lnet/minecraft/server/MinecraftServer;)V"
        );
    }

    private static Actor cooperativeCaller(String declaredOwner) {
        Actor actor = Actor.caller(ProbeRuntime.class.getName());
        return declaredOwner.equals(actor.className) ? actor : Actor.UNBOUND;
    }

    private static void cooperativeEmit(
        String recordType,
        String stageId,
        long worldSeed,
        int dimension,
        int chunkX,
        int chunkZ,
        String owner,
        Map<String, Object> payload
    ) {
        if (!enabled()) {
            return;
        }
        try {
            Actor actor = cooperativeCaller(owner);
            payload.put("world_seed_sha256", digestText(worldSeed));
            emit(
                recordType,
                contextualSpan(scope(dimension, chunkX, chunkZ), actor),
                actor,
                "observed",
                null,
                payload
            );
        } catch (Throwable observerFailure) {
            observerFailure(
                "CRW926_COOPERATIVE_RECORD_FAILED_" + digestParts(recordType, stageId),
                observerFailure
            );
        }
    }

    private static SpanToken contextualSpan(Scope requestedScope, Actor actor) {
        SpanToken active = SPANS.get().peek();
        Scope inherited = requestedScope.inherit(active == null ? Scope.EMPTY : active.scope);
        if (active == null) {
            return new SpanToken(
                false,
                "cooperative-observation",
                "cooperative_observation",
                null,
                null,
                null,
                null,
                inherited,
                actor
            );
        }
        return new SpanToken(
            false,
            active.hookId,
            active.spanKind,
            active.traceId,
            active.rootTriggerId,
            active.spanId,
            active.parentSpanId,
            inherited,
            actor
        );
    }

    private static boolean emit(
        String recordType,
        @Nullable SpanToken span,
        Actor actor,
        String state,
        @Nullable Throwable throwable,
        Map<String, Object> payload
    ) {
        if (!enabled() || IN_OBSERVER.get()) {
            return false;
        }
        IN_OBSERVER.set(Boolean.TRUE);
        try {
            long recordSequence = RECORD_SEQUENCE.getAndIncrement();
            long[] local = THREAD_SEQUENCE.get();
            long threadSequence = local[0]++;
            long lamport = LAMPORT.getAndIncrement();
            Scope scope = span == null ? Scope.EMPTY : span.scope;
            Map<String, Object> record = fields(
                "format", RAW_FORMAT,
                "record_type", recordType,
                "sequence", recordSequence,
                "capture_id", CAPTURE_ID,
                "scope", fields(
                    "dimension_id", scope.dimension,
                    "chunk_x", scope.chunkX,
                    "chunk_z", scope.chunkZ
                ),
                "causality", fields(
                    "trace_id", span == null ? null : span.traceId,
                    "root_trigger_id", span == null ? null : span.rootTriggerId,
                    "span_id", span == null ? null : span.spanId,
                    "parent_span_id", span == null ? null : span.parentSpanId
                ),
                "actor", actor.toMap(),
                "order", fields(
                    "thread_name", Thread.currentThread().getName(),
                    "thread_id", Thread.currentThread().threadId(),
                    "thread_sequence", threadSequence,
                    "lamport", lamport,
                    "monotonic_ns", System.nanoTime()
                ),
                "outcome", fields(
                    "state", state,
                    "exception_class", throwable == null ? null : throwable.getClass().getName(),
                    "exception_message_sha256", throwable == null ? null : digestParts(throwable.getMessage())
                ),
                "coverage", fields(
                    "mode", MODE,
                    "detail_state", "complete",
                    "dropped_record_count", 0
                ),
                "payload", payload
            );
            WRITER.append(Json.encode(record));
            return true;
        } catch (Throwable observerFailure) {
            WRITER.fail(observerFailure);
            return false;
        } finally {
            IN_OBSERVER.set(Boolean.FALSE);
        }
    }

    private static void observerFailure(String code, Throwable failure) {
        try {
            if (!ENABLED || IN_OBSERVER.get() || !WRITER.available()) {
                return;
            }
            emit(
                "diagnostic",
                SPANS.get().peek(),
                Actor.workbench(
                    ProbeRuntime.class.getName(),
                    "observerFailure",
                    "(Ljava/lang/String;Ljava/lang/Throwable;)V"
                ),
                "incomplete",
                failure,
                fields(
                    "code", code,
                    "severity", "error",
                    "message_arguments_sha256", digestParts(failure.getClass().getName(), failure.getMessage())
                )
            );
        } catch (Throwable ignored) {
            // Fail open.  The writer also reports the first failure to stderr.
        }
    }

    private static Map<String, Object> fields(Object... pairs) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int index = 0; index < pairs.length; index += 2) {
            result.put(String.valueOf(pairs[index]), pairs[index + 1]);
        }
        return result;
    }

    private static String zeros() {
        return String.join("", Collections.nCopies(64, "0"));
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(Character.forDigit((value >>> 4) & 0x0f, 16));
            result.append(Character.forDigit(value & 0x0f, 16));
        }
        return result.toString();
    }

    public static final class Scope {
        private static final Scope EMPTY = new Scope(null, null, null);
        private final Integer dimension;
        private final Integer chunkX;
        private final Integer chunkZ;

        private Scope(Integer dimension, Integer chunkX, Integer chunkZ) {
            this.dimension = dimension;
            this.chunkX = chunkX;
            this.chunkZ = chunkZ;
        }

        private Scope inherit(Scope parent) {
            return new Scope(
                dimension == null ? parent.dimension : dimension,
                chunkX == null ? parent.chunkX : chunkX,
                chunkZ == null ? parent.chunkZ : chunkZ
            );
        }
    }

    public static final class SpanToken {
        private static final SpanToken NO_OP = new SpanToken(
            false, "", "", "", "", "", null, Scope.EMPTY, Actor.UNBOUND
        );
        private final boolean active;
        private final String hookId;
        private final String spanKind;
        private final String traceId;
        private final String rootTriggerId;
        private final String spanId;
        private final String parentSpanId;
        private final Scope scope;
        private final Actor actor;

        private SpanToken(
            boolean active,
            String hookId,
            String spanKind,
            String traceId,
            String rootTriggerId,
            String spanId,
            String parentSpanId,
            Scope scope,
            Actor actor
        ) {
            this.active = active;
            this.hookId = hookId;
            this.spanKind = spanKind;
            this.traceId = traceId;
            this.rootTriggerId = rootTriggerId;
            this.spanId = spanId;
            this.parentSpanId = parentSpanId;
            this.scope = scope;
            this.actor = actor;
        }
    }

    public static final class EventState {
        private static final EventState UNAVAILABLE = new EventState(false, "UNAVAILABLE", digestParts("unavailable"));
        private final boolean cancelled;
        private final String result;
        private final String digest;

        private EventState(boolean cancelled, String result, String digest) {
            this.cancelled = cancelled;
            this.result = result;
            this.digest = digest;
        }

        @Override
        public String toString() {
            return digest;
        }
    }

    public static final class WriteToken {
        private static final WriteToken NO_OP = new WriteToken(
            false, "", "", "", 0, 0, 0, 0, zeros(), zeros(), null, Actor.UNBOUND
        );
        private final boolean active;
        private final String hookId;
        private final String channel;
        private final String chainId;
        private final int depth;
        private final int x;
        private final int y;
        private final int z;
        private final String beforeDigest;
        private final String requestedDigest;
        private final Integer flags;
        private final Actor actor;
        private boolean childSeen;

        private WriteToken(
            boolean active,
            String hookId,
            String channel,
            String chainId,
            int depth,
            int x,
            int y,
            int z,
            String beforeDigest,
            String requestedDigest,
            Integer flags,
            Actor actor
        ) {
            this.active = active;
            this.hookId = hookId;
            this.channel = channel;
            this.chainId = chainId;
            this.depth = depth;
            this.x = x;
            this.y = y;
            this.z = z;
            this.beforeDigest = beforeDigest;
            this.requestedDigest = requestedDigest;
            this.flags = flags;
            this.actor = actor;
        }
    }

    public static final class Actor {
        private static final Actor UNBOUND = new Actor("unbound", null, null, null, null, null, null, null);
        private final String binding;
        private final String modId;
        private final String className;
        private final String methodName;
        private final String methodDescriptor;
        private final String mappingNamespace;
        private final String codeSourceSha256;
        private final String transformedClassSha256;

        private Actor(
            String binding,
            String modId,
            String className,
            String methodName,
            String methodDescriptor,
            String mappingNamespace,
            String codeSourceSha256,
            String transformedClassSha256
        ) {
            this.binding = binding;
            this.modId = modId;
            this.className = className;
            this.methodName = methodName;
            this.methodDescriptor = methodDescriptor;
            this.mappingNamespace = mappingNamespace;
            this.codeSourceSha256 = codeSourceSha256;
            this.transformedClassSha256 = transformedClassSha256;
        }

        public static Actor workbench(String className, String methodName, String methodDescriptor) {
            return new Actor(
                "workbench",
                "workbench_worldgen_observatory",
                className,
                methodName,
                methodDescriptor,
                "java_source",
                codeSourceDigest(ProbeRuntime.class),
                null
            );
        }

        public static Actor target(String className, String methodName, String methodDescriptor) {
            try {
                TargetBinding binding = TARGET_BINDINGS.get(className);
                Class<?> targetClass = Class.forName(
                    className,
                    false,
                    Thread.currentThread().getContextClassLoader()
                );
                return new Actor(
                    "exact_target",
                    targetModId(className),
                    className,
                    methodName,
                    methodDescriptor,
                    "mcp_stable_39",
                    codeSourceDigest(targetClass),
                    binding == null ? null : binding.transformedDigest
                );
            } catch (Throwable observerFailure) {
                observerFailure("CRW923_TARGET_ACTOR_FAILED", observerFailure);
                return UNBOUND;
            }
        }

        private static String targetModId(String className) {
            if (className.startsWith("net.minecraft.")) {
                return "minecraft";
            }
            if (className.startsWith("net.minecraftforge.")) {
                return "forge";
            }
            return null;
        }

        public static Actor runtime(Object owner, String methodName, String methodDescriptor) {
            if (owner == null) {
                return UNBOUND;
            }
            try {
                Class<?> ownerClass = owner.getClass();
                return new Actor(
                    "runtime_class",
                    null,
                    ownerClass.getName(),
                    methodName,
                    methodDescriptor,
                    "mcp_stable_39",
                    codeSourceDigest(ownerClass),
                    null
                );
            } catch (Throwable observerFailure) {
                observerFailure("CRW924_RUNTIME_ACTOR_FAILED", observerFailure);
                return UNBOUND;
            }
        }

        public static Actor listener(IEventListener listener) {
            try {
                return forListener(listener);
            } catch (Throwable observerFailure) {
                observerFailure("CRW921_LISTENER_ACTOR_FAILED", observerFailure);
                return UNBOUND;
            }
        }

        private static Actor forListener(IEventListener listener) {
            if (listener instanceof ASMEventHandlerAccess) {
                ASMEventHandlerAccess access = (ASMEventHandlerAccess) listener;
                reach("cleanroom-worldgen:event_bus.listener_owner_accessor");
                ModContainer owner = access.workbench$getOwner();
                reach("cleanroom-worldgen:event_bus.listener_readable_accessor");
                String readable = access.workbench$getReadable();
                String stableReadable = readable == null
                    ? "unavailable"
                    : readable.replaceAll("@[0-9a-fA-F]+", "@<identity>");
                int signatureSeparator = stableReadable.lastIndexOf(' ');
                String target = signatureSeparator < 0
                    ? stableReadable
                    : stableReadable.substring(0, signatureSeparator);
                if (target.startsWith("ASM: ")) {
                    target = target.substring("ASM: ".length());
                }
                if (target.startsWith("class ")) {
                    target = target.substring("class ".length());
                }
                int identityMarker = target.indexOf("@<identity>");
                if (identityMarker >= 0) {
                    target = target.substring(0, identityMarker);
                }
                String signature = signatureSeparator < 0
                    ? "invoke(Lnet/minecraftforge/fml/common/eventhandler/Event;)V"
                    : stableReadable.substring(signatureSeparator + 1);
                int descriptorStart = signature.indexOf('(');
                return new Actor(
                    "event_listener",
                    owner == null ? null : owner.getModId(),
                    target,
                    descriptorStart < 0 ? signature : signature.substring(0, descriptorStart),
                    descriptorStart < 0 ? null : signature.substring(descriptorStart),
                    "jvm_descriptor",
                    owner == null ? null : digestModSource(owner),
                    null
                );
            }
            String descriptor = uniqueDeclaredMethodDescriptor(
                listener.getClass(),
                "invoke"
            );
            return new Actor(
                "listener_wrapper",
                null,
                listener.getClass().getName(),
                "invoke",
                descriptor,
                descriptor == null ? "jvm_stack" : "jvm_descriptor",
                codeSourceDigest(listener.getClass()),
                null
            );
        }

        private static Actor caller(String excludedTargetClass) {
            try {
                StackTraceElement[] trace = Thread.currentThread().getStackTrace();
                for (StackTraceElement frame : trace) {
                    String candidate = frame.getClassName();
                    if (candidate.equals(excludedTargetClass)
                        || candidate.startsWith("dev.workbench.worldgenobservatory.probe")
                        || candidate.startsWith("com.llamalad7.mixinextras")
                        || candidate.startsWith("org.spongepowered.asm.mixin")
                        || candidate.equals(Thread.class.getName())) {
                        continue;
                    }
                    Class<?> actorClass = null;
                    try {
                        actorClass = Class.forName(candidate, false, Thread.currentThread().getContextClassLoader());
                    } catch (Throwable ignored) {
                        // Class and source digest remain raw/unbound.
                    }
                    String sourceDigest = actorClass == null ? null : codeSourceDigest(actorClass);
                    String descriptor = actorClass == null
                        ? null
                        : uniqueDeclaredMethodDescriptor(actorClass, frame.getMethodName());
                    String modId = uniqueModIdForSource(sourceDigest);
                    return new Actor(
                        modId != null && descriptor != null
                            ? "stack_source_method"
                            : "stack_candidate",
                        modId,
                        candidate,
                        frame.getMethodName(),
                        descriptor,
                        descriptor == null ? "jvm_stack" : "jvm_descriptor",
                        sourceDigest,
                        null
                    );
                }
            } catch (Throwable ignored) {
                // Fall through to unbound.
            }
            return UNBOUND;
        }

        @Nullable
        private static String uniqueModIdForSource(@Nullable String sourceDigest) {
            if (sourceDigest == null) {
                return null;
            }
            try {
                String cached = MOD_ID_BY_SOURCE.get(sourceDigest);
                if (cached != null) {
                    return AMBIGUOUS.equals(cached) ? null : cached;
                }
                String match = null;
                int matches = 0;
                for (ModContainer container : Loader.instance().getModList()) {
                    String candidateDigest = digestModSource(container);
                    if (sourceDigest.equals(candidateDigest)) {
                        match = container.getModId();
                        matches++;
                    }
                }
                String resolved = matches == 1 ? match : AMBIGUOUS;
                MOD_ID_BY_SOURCE.putIfAbsent(sourceDigest, resolved);
                return AMBIGUOUS.equals(resolved) ? null : resolved;
            } catch (Throwable ignored) {
                return null;
            }
        }

        @Nullable
        private static String uniqueDeclaredMethodDescriptor(Class<?> owner, String methodName) {
            try {
                String key = owner.getName() + "#" + methodName;
                String cached = DECLARED_METHOD_DESCRIPTORS.get(key);
                if (cached != null) {
                    return AMBIGUOUS.equals(cached) ? null : cached;
                }
                java.lang.reflect.Method match = null;
                for (java.lang.reflect.Method method : owner.getDeclaredMethods()) {
                    if (!method.getName().equals(methodName)) {
                        continue;
                    }
                    if (match != null) {
                        DECLARED_METHOD_DESCRIPTORS.putIfAbsent(key, AMBIGUOUS);
                        return null;
                    }
                    match = method;
                }
                if (match == null) {
                    DECLARED_METHOD_DESCRIPTORS.putIfAbsent(key, AMBIGUOUS);
                    return null;
                }
                StringBuilder descriptor = new StringBuilder("(");
                for (Class<?> parameter : match.getParameterTypes()) {
                    descriptor.append(typeDescriptor(parameter));
                }
                String resolved = descriptor.append(')')
                    .append(typeDescriptor(match.getReturnType()))
                    .toString();
                DECLARED_METHOD_DESCRIPTORS.putIfAbsent(key, resolved);
                return resolved;
            } catch (Throwable ignored) {
                return null;
            }
        }

        private static String typeDescriptor(Class<?> type) {
            if (type.isArray()) {
                return type.getName().replace('.', '/');
            }
            if (!type.isPrimitive()) {
                return "L" + type.getName().replace('.', '/') + ";";
            }
            if (type == void.class) {
                return "V";
            }
            if (type == boolean.class) {
                return "Z";
            }
            if (type == byte.class) {
                return "B";
            }
            if (type == char.class) {
                return "C";
            }
            if (type == short.class) {
                return "S";
            }
            if (type == int.class) {
                return "I";
            }
            if (type == long.class) {
                return "J";
            }
            if (type == float.class) {
                return "F";
            }
            if (type == double.class) {
                return "D";
            }
            throw new IllegalArgumentException("Unknown primitive type " + type.getName());
        }

        private Map<String, Object> toMap() {
            return fields(
                "binding", binding,
                "mod_id", modId,
                "class_name", className,
                "method_name", methodName,
                "method_descriptor", methodDescriptor,
                "mapping_namespace", mappingNamespace,
                "code_source_sha256", codeSourceSha256,
                "transformed_class_sha256", transformedClassSha256
            );
        }

        private static String codeSourceDigest(Class<?> type) {
            Path sourcePath = null;
            try {
                CodeSource source = type.getProtectionDomain().getCodeSource();
                if (source != null && source.getLocation() != null) {
                    sourcePath = Paths.get(source.getLocation().toURI());
                }
            } catch (Throwable ignored) {
                // Foundation may define transformed classes without a code source.
            }
            if (sourcePath == null) {
                sourcePath = classResourceSource(type);
            }
            return sourcePath == null ? null : digestPath(sourcePath);
        }

        private static Path classResourceSource(Class<?> type) {
            return SourceArtifactDigest.classSourcePath(type);
        }

        private static String digestModSource(ModContainer owner) {
            try {
                return digestPath(owner.getSource().toPath());
            } catch (Throwable ignored) {
                return null;
            }
        }

        private static String digestPath(Path path) {
            String key = path.toAbsolutePath().normalize().toString();
            String cached = CODE_SOURCE_DIGESTS.get(key);
            if (cached != null) {
                return cached;
            }
            try {
                String value = SourceArtifactDigest.sha256(path);
                if (value == null) {
                    return null;
                }
                CODE_SOURCE_DIGESTS.put(key, value);
                return value;
            } catch (Throwable ignored) {
                return null;
            }
        }
    }

    private static final class HealthObservation {
        private final String hookId;
        private final String targetClass;
        private final String targetMethod;
        private final String targetDescriptor;
        private final String originalDigest;
        private final String transformedDigest;
        private final int expected;
        private final int observed;

        private HealthObservation(
            String hookId,
            String targetClass,
            String targetMethod,
            String targetDescriptor,
            String originalDigest,
            String transformedDigest,
            int expected,
            int observed
        ) {
            this.hookId = hookId;
            this.targetClass = targetClass;
            this.targetMethod = targetMethod;
            this.targetDescriptor = targetDescriptor;
            this.originalDigest = originalDigest;
            this.transformedDigest = transformedDigest;
            this.expected = expected;
            this.observed = observed;
        }
    }

    private static final class TargetBinding {
        private static final TargetBinding UNKNOWN = new TargetBinding(
            "unbound", "unbound", "unbound", zeros(), zeros(), 0, 0
        );
        private final String targetClass;
        private final String targetMethod;
        private final String targetDescriptor;
        private final String originalDigest;
        private final String transformedDigest;
        private final int expected;
        private final int observed;

        private TargetBinding(String originalDigest, String transformedDigest) {
            this("unbound", "unbound", "unbound", originalDigest, transformedDigest, 0, 0);
        }

        private TargetBinding(
            String targetClass,
            String targetMethod,
            String targetDescriptor,
            String originalDigest,
            String transformedDigest,
            int expected,
            int observed
        ) {
            this.targetClass = targetClass;
            this.targetMethod = targetMethod;
            this.targetDescriptor = targetDescriptor;
            this.originalDigest = originalDigest;
            this.transformedDigest = transformedDigest;
            this.expected = expected;
            this.observed = observed;
        }
    }

    private static final class RawWriter {
        private final BufferedWriter writer;
        private volatile boolean failed;
        private volatile boolean firstFailureReported;

        private RawWriter(BufferedWriter writer, boolean failed) {
            this.writer = writer;
            this.failed = failed;
        }

        private static RawWriter open() {
            if (!ENABLED) {
                return new RawWriter(null, true);
            }
            try {
                Path output = Paths.get(System.getProperty(OUTPUT_PROPERTY, "logs/worldgen-observatory.raw.ndjson"));
                Path parent = output.toAbsolutePath().normalize().getParent();
                if (parent != null) {
                    Files.createDirectories(parent);
                }
                BufferedWriter writer = Files.newBufferedWriter(
                    output,
                    StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.WRITE,
                    StandardOpenOption.APPEND
                );
                return new RawWriter(writer, false);
            } catch (Throwable failure) {
                RawWriter result = new RawWriter(null, true);
                result.fail(failure);
                return result;
            }
        }

        private boolean available() {
            return ENABLED && !failed && writer != null;
        }

        private synchronized void append(String line) throws IOException {
            if (!available()) {
                return;
            }
            writer.write(line);
            writer.newLine();
            writer.flush();
        }

        private void fail(Throwable failure) {
            failed = true;
            if (!firstFailureReported) {
                firstFailureReported = true;
                try {
                    System.err.println(
                        "WORLDGEN_OBSERVATORY_RAW_WRITER_FAILED "
                            + failure.getClass().getName()
                            + " message_sha256="
                            + digestParts(failure.getMessage())
                    );
                } catch (Throwable ignored) {
                    // Nothing else can be done without risking the observed run.
                }
            }
        }
    }

    private static final class Json {
        private Json() {
        }

        private static String encode(Object value) {
            StringBuilder output = new StringBuilder(512);
            append(output, value);
            return output.toString();
        }

        private static void append(StringBuilder output, Object value) {
            if (value == null) {
                output.append("null");
            } else if (value instanceof String || value instanceof Character) {
                quote(output, String.valueOf(value));
            } else if (value instanceof Number || value instanceof Boolean) {
                output.append(value);
            } else if (value instanceof Map<?, ?>) {
                output.append('{');
                boolean first = true;
                for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                    if (!first) {
                        output.append(',');
                    }
                    first = false;
                    quote(output, String.valueOf(entry.getKey()));
                    output.append(':');
                    append(output, entry.getValue());
                }
                output.append('}');
            } else if (value instanceof Iterable<?>) {
                output.append('[');
                boolean first = true;
                for (Object item : (Iterable<?>) value) {
                    if (!first) {
                        output.append(',');
                    }
                    first = false;
                    append(output, item);
                }
                output.append(']');
            } else if (value.getClass().isArray()) {
                int length = java.lang.reflect.Array.getLength(value);
                output.append('[');
                for (int index = 0; index < length; index++) {
                    if (index > 0) {
                        output.append(',');
                    }
                    append(output, java.lang.reflect.Array.get(value, index));
                }
                output.append(']');
            } else {
                quote(output, String.valueOf(value));
            }
        }

        private static void quote(StringBuilder output, String value) {
            output.append('"');
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"':
                        output.append("\\\"");
                        break;
                    case '\\':
                        output.append("\\\\");
                        break;
                    case '\b':
                        output.append("\\b");
                        break;
                    case '\f':
                        output.append("\\f");
                        break;
                    case '\n':
                        output.append("\\n");
                        break;
                    case '\r':
                        output.append("\\r");
                        break;
                    case '\t':
                        output.append("\\t");
                        break;
                    default:
                        if (character < 0x20) {
                            output.append(String.format("\\u%04x", (int) character));
                        } else {
                            output.append(character);
                        }
                }
            }
            output.append('"');
        }
    }
}
