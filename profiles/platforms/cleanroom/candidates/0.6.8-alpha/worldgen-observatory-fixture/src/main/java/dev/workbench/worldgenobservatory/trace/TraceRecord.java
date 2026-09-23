package dev.workbench.worldgenobservatory.trace;

/**
 * A deliberately small stable JSON record. Field order is part of this
 * fixture's source contract; no wall-clock or process-local identity appears.
 */
public final class TraceRecord {

    private static final String SCHEMA = "workbench.worldgen-observatory.trace.v1";

    private final String kind;
    private final String stage;
    private final String decision;
    private final TraceContext context;
    private final String rngLane;
    private final long rngSeed;
    private final String checkpoint;
    private final String owner;

    private TraceRecord(
            String kind,
            String stage,
            String decision,
            TraceContext context,
            String rngLane,
            long rngSeed,
            String checkpoint,
            String owner
    ) {
        this.kind = kind;
        this.stage = stage;
        this.decision = decision;
        this.context = context;
        this.rngLane = rngLane;
        this.rngSeed = rngSeed;
        this.checkpoint = checkpoint;
        this.owner = owner;
    }

    public static TraceRecord stage(TraceContext context, String stage, String owner) {
        return new TraceRecord("stage", stage, "", context, "", 0L, "", owner);
    }

    public static TraceRecord decision(
            TraceContext context,
            String stage,
            String decision,
            String owner
    ) {
        return new TraceRecord("decision", stage, decision, context, "", 0L, "", owner);
    }

    public static TraceRecord rng(
            TraceContext context,
            String stage,
            RngLanes.Lane lane,
            String owner
    ) {
        return new TraceRecord(
                "rng",
                stage,
                "observation_only",
                context,
                lane.name().toLowerCase(java.util.Locale.ROOT),
                RngLanes.seed(
                        context.worldSeed(),
                        context.dimension(),
                        context.chunkX(),
                        context.chunkZ(),
                        lane
                ),
                "",
                owner
        );
    }

    public static TraceRecord checkpoint(
            TraceContext context,
            String stage,
            String checkpoint,
            String owner
    ) {
        return new TraceRecord("checkpoint", stage, "", context, "", 0L, checkpoint, owner);
    }

    public String toJson() {
        StringBuilder json = new StringBuilder(320);
        json.append('{');
        field(json, "schema", SCHEMA).append(',');
        field(json, "kind", kind).append(',');
        field(json, "stage", stage).append(',');
        field(json, "decision", decision).append(',');
        number(json, "world_seed", context.worldSeed()).append(',');
        number(json, "dimension", context.dimension()).append(',');
        number(json, "chunk_x", context.chunkX()).append(',');
        number(json, "chunk_z", context.chunkZ()).append(',');
        field(json, "rng_lane", rngLane).append(',');
        number(json, "rng_seed", rngSeed).append(',');
        field(json, "checkpoint", checkpoint).append(',');
        field(json, "owner", owner);
        return json.append('}').toString();
    }

    private static StringBuilder field(StringBuilder json, String name, String value) {
        quote(json, name).append(':');
        return quote(json, value);
    }

    private static StringBuilder number(StringBuilder json, String name, long value) {
        quote(json, name).append(':').append(value);
        return json;
    }

    private static StringBuilder quote(StringBuilder json, String value) {
        json.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            switch (character) {
                case '"':
                    json.append("\\\"");
                    break;
                case '\\':
                    json.append("\\\\");
                    break;
                case '\n':
                    json.append("\\n");
                    break;
                case '\r':
                    json.append("\\r");
                    break;
                case '\t':
                    json.append("\\t");
                    break;
                default:
                    if (character < 0x20) {
                        json.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int) character));
                    } else {
                        json.append(character);
                    }
            }
        }
        return json.append('"');
    }
}
