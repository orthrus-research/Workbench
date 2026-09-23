package dev.workbench.worldgenprototype.diagnostics;

import java.util.Locale;
import java.util.concurrent.atomic.AtomicLong;
import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import net.minecraft.util.ResourceLocation;
import net.minecraft.util.math.BlockPos;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;

/**
 * Opt-in exact feature trace for the bounded CRUCIBLE-M4-W01 proving route.
 *
 * <p>This is a new V2 diagnostic transport.  It does not change or reinterpret
 * the existing {@code WORLDGEN_PROTOTYPE} records.  The transport is inert
 * unless the launcher supplies one exact prelaunch execution-envelope ID.</p>
 */
public final class WorldgenCausalTrace {

    public static final String PREFIX = "WORLDGEN_PROTOTYPE_CAUSAL_V2";
    public static final String FORMAT = "workbench-world-studio-causal-trace-v2";
    private static final String ENVELOPE_PROPERTY =
            "workbench.worldgen.execution_envelope_id";
    private static final String MIN_CHUNK_X_PROPERTY =
            "workbench.worldgen.causal.min_chunk_x";
    private static final String MIN_CHUNK_Z_PROPERTY =
            "workbench.worldgen.causal.min_chunk_z";
    private static final String MAX_CHUNK_X_PROPERTY =
            "workbench.worldgen.causal.max_chunk_x_exclusive";
    private static final String MAX_CHUNK_Z_PROPERTY =
            "workbench.worldgen.causal.max_chunk_z_exclusive";
    private static final Logger LOGGER = LogManager.getLogger("workbench-worldgen-prototype-causal");
    private static final AtomicLong OCCURRENCE_SEQUENCE = new AtomicLong();
    private static final String ENVELOPE_ID = safeEnvelopeId();
    private static final int MIN_CHUNK_X = safeIntegerProperty(
            MIN_CHUNK_X_PROPERTY,
            Integer.MIN_VALUE
    );
    private static final int MIN_CHUNK_Z = safeIntegerProperty(
            MIN_CHUNK_Z_PROPERTY,
            Integer.MIN_VALUE
    );
    private static final int MAX_CHUNK_X = safeIntegerProperty(
            MAX_CHUNK_X_PROPERTY,
            Integer.MAX_VALUE
    );
    private static final int MAX_CHUNK_Z = safeIntegerProperty(
            MAX_CHUNK_Z_PROPERTY,
            Integer.MAX_VALUE
    );

    private WorldgenCausalTrace() {
    }

    public static boolean enabled() {
        return ENVELOPE_ID != null;
    }

    private static boolean selected(int chunkX, int chunkZ) {
        return enabled()
                && MIN_CHUNK_X < MAX_CHUNK_X
                && MIN_CHUNK_Z < MAX_CHUNK_Z
                && chunkX >= MIN_CHUNK_X
                && chunkX < MAX_CHUNK_X
                && chunkZ >= MIN_CHUNK_Z
                && chunkZ < MAX_CHUNK_Z;
    }

    public static void populationGate(
            long seed,
            int dimension,
            int chunkX,
            int chunkZ,
            boolean allowed
    ) {
        if (!selected(chunkX, chunkZ)) {
            return;
        }
        emit(String.format(
                Locale.ROOT,
                "{\"format\":\"%s\",\"schema_version\":2,"
                        + "\"execution_envelope_id\":\"%s\","
                        + "\"occurrence_ordinal\":%d,\"record_type\":\"gate-decision\","
                        + "\"stage_id\":\"populate.custom\","
                        + "\"rule_id\":\"forge.populate.custom\","
                        + "\"seed\":%d,\"dimension\":%d,\"chunk_x\":%d,\"chunk_z\":%d,"
                        + "\"decision\":\"%s\"}",
                FORMAT,
                escape(ENVELOPE_ID),
                OCCURRENCE_SEQUENCE.getAndIncrement(),
                seed,
                dimension,
                chunkX,
                chunkZ,
                allowed ? "allowed" : "rejected"
        ));
    }

    public static void rejected(
            long seed,
            int dimension,
            int chunkX,
            int chunkZ,
            int eligibilityDraw
    ) {
        if (!selected(chunkX, chunkZ)) {
            return;
        }
        emit(String.format(
                Locale.ROOT,
                "{\"format\":\"%s\",\"schema_version\":2,"
                        + "\"execution_envelope_id\":\"%s\","
                        + "\"occurrence_ordinal\":%d,\"record_type\":\"feature-decision\","
                        + "\"stage_id\":\"populate.custom\","
                        + "\"rule_id\":\"world-studio.boulder.v1\","
                        + "\"seed\":%d,\"dimension\":%d,\"chunk_x\":%d,\"chunk_z\":%d,"
                        + "\"rng_algorithm\":\"java.util.Random.v1\","
                        + "\"seed_derivation_id\":\"world-studio.stage-random.v1\","
                        + "\"stage_seed\":%d,"
                        + "\"eligibility_bound\":10,\"eligibility_draw\":%d,"
                        + "\"eligibility_accepted\":false,\"x_draw\":null,\"z_draw\":null,"
                        + "\"optional_top_draw\":null,\"base_position\":null,"
                        + "\"writes\":[],\"outcome\":\"rejected-by-eligibility-draw\"}",
                FORMAT,
                escape(ENVELOPE_ID),
                OCCURRENCE_SEQUENCE.getAndIncrement(),
                seed,
                dimension,
                chunkX,
                chunkZ,
                stageSeed(seed, chunkX, chunkZ),
                eligibilityDraw
        ));
    }

    public static void placed(
            long seed,
            int dimension,
            int chunkX,
            int chunkZ,
            int eligibilityDraw,
            int xDraw,
            int zDraw,
            boolean optionalTopDraw,
            BlockPos base,
            WriteObservation baseWrite,
            WriteObservation eastWrite,
            WriteObservation topWrite
    ) {
        if (!selected(chunkX, chunkZ)) {
            return;
        }
        emit(String.format(
                Locale.ROOT,
                "{\"format\":\"%s\",\"schema_version\":2,"
                        + "\"execution_envelope_id\":\"%s\","
                        + "\"occurrence_ordinal\":%d,\"record_type\":\"feature-decision\","
                        + "\"stage_id\":\"populate.custom\","
                        + "\"rule_id\":\"world-studio.boulder.v1\","
                        + "\"seed\":%d,\"dimension\":%d,\"chunk_x\":%d,\"chunk_z\":%d,"
                        + "\"rng_algorithm\":\"java.util.Random.v1\","
                        + "\"seed_derivation_id\":\"world-studio.stage-random.v1\","
                        + "\"stage_seed\":%d,"
                        + "\"eligibility_bound\":10,\"eligibility_draw\":%d,"
                        + "\"eligibility_accepted\":true,\"x_draw\":%d,\"z_draw\":%d,"
                        + "\"optional_top_draw\":%s,"
                        + "\"base_position\":{\"x\":%d,\"y\":%d,\"z\":%d},"
                        + "\"writes\":[%s,%s,%s],\"outcome\":\"placed\"}",
                FORMAT,
                escape(ENVELOPE_ID),
                OCCURRENCE_SEQUENCE.getAndIncrement(),
                seed,
                dimension,
                chunkX,
                chunkZ,
                stageSeed(seed, chunkX, chunkZ),
                eligibilityDraw,
                xDraw,
                zDraw,
                optionalTopDraw,
                base.getX(),
                base.getY(),
                base.getZ(),
                baseWrite.toJson(),
                eastWrite.toJson(),
                topWrite.toJson()
        ));
    }

    private static long stageSeed(long seed, int chunkX, int chunkZ) {
        return seed
                ^ 0x6a09e667f3bcc909L
                ^ ((long) chunkX * 341873128712L)
                ^ ((long) chunkZ * 132897987541L);
    }

    public static WriteObservation write(
            String role,
            BlockPos position,
            IBlockState before,
            IBlockState requested,
            IBlockState after,
            boolean invoked,
            boolean result,
            String outcome
    ) {
        return new WriteObservation(
                role,
                position,
                before,
                requested,
                after,
                invoked,
                result,
                outcome
        );
    }

    private static String safeEnvelopeId() {
        try {
            String value = System.getProperty(ENVELOPE_PROPERTY);
            if (value == null || value.trim().isEmpty()) {
                return null;
            }
            return value;
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static int safeIntegerProperty(String name, int fallback) {
        try {
            return Integer.parseInt(System.getProperty(name, Integer.toString(fallback)));
        } catch (RuntimeException ignored) {
            return fallback;
        }
    }

    private static String stateJson(IBlockState state) {
        if (state == null) {
            return "null";
        }
        ResourceLocation name = Block.REGISTRY.getNameForObject(state.getBlock());
        int metadata;
        try {
            metadata = state.getBlock().getMetaFromState(state);
        } catch (RuntimeException ignored) {
            metadata = -1;
        }
        return String.format(
                Locale.ROOT,
                "{\"registry_name\":\"%s\",\"metadata\":%d}",
                escape(String.valueOf(name)),
                metadata
        );
    }

    private static void emit(String record) {
        LOGGER.info(PREFIX + " " + record);
    }

    private static String escape(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r");
    }

    public static final class WriteObservation {

        private final String role;
        private final BlockPos position;
        private final IBlockState before;
        private final IBlockState requested;
        private final IBlockState after;
        private final boolean invoked;
        private final boolean result;
        private final String outcome;

        private WriteObservation(
                String role,
                BlockPos position,
                IBlockState before,
                IBlockState requested,
                IBlockState after,
                boolean invoked,
                boolean result,
                String outcome
        ) {
            this.role = role;
            this.position = position;
            this.before = before;
            this.requested = requested;
            this.after = after;
            this.invoked = invoked;
            this.result = result;
            this.outcome = outcome;
        }

        private String toJson() {
            return String.format(
                    Locale.ROOT,
                    "{\"role\":\"%s\",\"position\":{\"x\":%d,\"y\":%d,\"z\":%d},"
                            + "\"before_state\":%s,\"requested_state\":%s,"
                            + "\"after_state\":%s,\"invoked\":%s,\"result\":%s,"
                            + "\"outcome\":\"%s\"}",
                    escape(role),
                    position.getX(),
                    position.getY(),
                    position.getZ(),
                    stateJson(before),
                    stateJson(requested),
                    stateJson(after),
                    invoked,
                    result,
                    escape(outcome)
            );
        }
    }
}
