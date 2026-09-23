package dev.workbench.worldgenprototype.world.field;

import dev.workbench.worldgenprototype.world.plan.WorldStudioPlan;

/** Deterministic Java hot-path implementation for a configured octave field. */
public final class FractalValueField {

    private final long seed;
    private final WorldStudioPlan.FieldDefinition definition;

    public FractalValueField(long worldSeed, WorldStudioPlan.FieldDefinition definition) {
        this.seed = worldSeed ^ definition.seedSalt();
        this.definition = definition;
    }

    public double sample(double blockX, double blockZ) {
        double frequency = 1.0D / definition.scale();
        double amplitude = 1.0D;
        double sum = 0.0D;
        double weight = 0.0D;
        for (int octave = 0; octave < definition.octaves(); octave++) {
            sum += valueNoise(
                    blockX * frequency,
                    blockZ * frequency,
                    octave
            ) * amplitude;
            weight += amplitude;
            frequency *= definition.lacunarity();
            amplitude *= definition.gain();
        }
        return weight == 0.0D ? 0.0D : sum / weight;
    }

    public double[] octaveContributions(double blockX, double blockZ) {
        double[] values = new double[definition.octaves()];
        double frequency = 1.0D / definition.scale();
        double amplitude = 1.0D;
        for (int octave = 0; octave < values.length; octave++) {
            values[octave] = valueNoise(
                    blockX * frequency,
                    blockZ * frequency,
                    octave
            ) * amplitude;
            frequency *= definition.lacunarity();
            amplitude *= definition.gain();
        }
        return values;
    }

    private double valueNoise(double x, double z, int octave) {
        long cellX = floor(x);
        long cellZ = floor(z);
        double localX = x - cellX;
        double localZ = z - cellZ;
        double xBlend = fade(localX);
        double zBlend = fade(localZ);
        double north = lerp(
                lattice(cellX, cellZ, octave),
                lattice(cellX + 1L, cellZ, octave),
                xBlend
        );
        double south = lerp(
                lattice(cellX, cellZ + 1L, octave),
                lattice(cellX + 1L, cellZ + 1L, octave),
                xBlend
        );
        return lerp(north, south, zBlend);
    }

    private double lattice(long cellX, long cellZ, int octave) {
        long value = seed;
        value ^= cellX * 0x632be59bd9b4e019L;
        value ^= cellZ * 0x9e3779b97f4a7c15L;
        value ^= (long) octave * 0xd6e8feb86659fd93L;
        value = mix64(value);
        long magnitude = (value >>> 11) & ((1L << 53) - 1L);
        return magnitude * 0x1.0p-52 - 1.0D;
    }

    private static long floor(double value) {
        long integral = (long) value;
        return value < integral ? integral - 1L : integral;
    }

    private static long mix64(long value) {
        value = (value ^ (value >>> 30)) * 0xbf58476d1ce4e5b9L;
        value = (value ^ (value >>> 27)) * 0x94d049bb133111ebL;
        return value ^ (value >>> 31);
    }

    private static double fade(double value) {
        return value * value * value * (value * (value * 6.0D - 15.0D) + 10.0D);
    }

    private static double lerp(double left, double right, double amount) {
        return left + (right - left) * amount;
    }
}
