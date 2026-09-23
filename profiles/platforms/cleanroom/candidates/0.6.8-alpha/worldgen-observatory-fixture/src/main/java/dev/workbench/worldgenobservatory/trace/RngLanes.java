package dev.workbench.worldgenobservatory.trace;

/**
 * Stable, observation-only RNG lanes. These seeds describe a stage without
 * consuming or replacing the generator's own Random instance.
 */
public final class RngLanes {

    public enum Lane {
        TERRAIN(0x243F6A8885A308D3L),
        POPULATION(0x13198A2E03707344L),
        FINAL_CHECKPOINT(0xA4093822299F31D0L);

        private final long salt;

        Lane(long salt) {
            this.salt = salt;
        }
    }

    private RngLanes() {
    }

    public static long seed(long worldSeed, int dimension, int chunkX, int chunkZ, Lane lane) {
        long value = mix64(worldSeed ^ lane.salt);
        value = mix64(value ^ (0x9E3779B97F4A7C15L * dimension));
        value = mix64(value ^ (0xC2B2AE3D27D4EB4FL * chunkX));
        return mix64(value ^ (0x165667B19E3779F9L * chunkZ));
    }

    private static long mix64(long value) {
        value ^= value >>> 30;
        value *= 0xBF58476D1CE4E5B9L;
        value ^= value >>> 27;
        value *= 0x94D049BB133111EBL;
        value ^= value >>> 31;
        return value;
    }
}
