package dev.workbench.worldgenprototype.world.plan;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** Immutable, seed-independent definition compiled from defaults or GroovyScript. */
public final class WorldStudioPlan {

    public static final String DEFAULT_PROFILE_ID = "susy_bop_mega_regions";
    private static final String[] REQUIRED_FIELDS = {
            "continentalness",
            "temperature",
            "moisture",
            "relief",
            "surface"
    };

    private final String profileId;
    private final double megaRegionScale;
    private final Map<String, FieldDefinition> fields;
    private final Map<String, LithologyDefinition> lithologies;
    private final List<MegaRegionDefinition> megaRegions;
    private final WatershedDefinition watershed;
    private final boolean cavesEnabled;
    private final boolean ravinesEnabled;
    private final String hash;

    private WorldStudioPlan(Builder builder) {
        this.profileId = builder.profileId;
        this.megaRegionScale = builder.megaRegionScale;
        this.fields = Collections.unmodifiableMap(new LinkedHashMap<>(builder.fields));
        this.lithologies = Collections.unmodifiableMap(
                new LinkedHashMap<>(builder.lithologies)
        );
        this.megaRegions = Collections.unmodifiableList(new ArrayList<>(builder.megaRegions));
        this.watershed = builder.watershed.build();
        this.cavesEnabled = builder.cavesEnabled;
        this.ravinesEnabled = builder.ravinesEnabled;
        validate();
        this.hash = sha256(canonicalForm()).substring(0, 16);
    }

    public static Builder builder() {
        return new Builder();
    }

    public static Builder defaultsBuilder() {
        return builder()
                .profile(DEFAULT_PROFILE_ID)
                .megaRegionScale(3072.0D)
                .field("continentalness", 4096.0D, 5, 2.0D, 0.50D)
                .field("temperature", 2048.0D, 3, 2.0D, 0.52D)
                .field("moisture", 1536.0D, 4, 2.0D, 0.52D)
                .field("relief", 640.0D, 4, 2.0D, 0.50D)
                .field("surface", 96.0D, 3, 2.0D, 0.50D)
                .lithology("granite", 0.12D, 0.25D)
                .lithology("stone", 0.38D, 0.65D)
                .lithology("andesite", 0.22D, 0.38D)
                .watershedGrid(16, 32, 24)
                .watershedRunoff(0.08D, 1.0D, 0.75D, 8.0D, 45.0D)
                .watershedChannels(1.5D, 2.0D, 5.0D, 5.0D, 2.0D, 1.5D)
                .carvers(true, true)
                .megaRegion(
                        "stable_craton",
                        4.0D,
                        67.0D,
                        10.0D,
                        0.02D,
                        -0.04D,
                        "granite",
                        "biomesoplenty:prairie",
                        "biomesoplenty:seasonal_forest",
                        "biomesoplenty:woodland",
                        "minecraft:plains",
                        "minecraft:forest"
                )
                .megaRegion(
                        "sedimentary_basin",
                        3.0D,
                        61.0D,
                        6.0D,
                        0.08D,
                        0.20D,
                        "stone",
                        "biomesoplenty:bayou",
                        "biomesoplenty:bog",
                        "biomesoplenty:marsh",
                        "biomesoplenty:wetland",
                        "minecraft:swampland"
                )
                .megaRegion(
                        "orogenic_belt",
                        3.0D,
                        72.0D,
                        27.0D,
                        -0.18D,
                        0.04D,
                        "andesite",
                        "biomesoplenty:alps",
                        "biomesoplenty:alps_foothills",
                        "biomesoplenty:mountain",
                        "biomesoplenty:coniferous_forest",
                        "minecraft:extreme_hills",
                        "minecraft:taiga"
                );
    }

    public static WorldStudioPlan defaults() {
        return defaultsBuilder().build();
    }

    public String profileId() {
        return profileId;
    }

    public double megaRegionScale() {
        return megaRegionScale;
    }

    public FieldDefinition field(String id) {
        FieldDefinition definition = fields.get(id);
        if (definition == null) {
            throw new IllegalArgumentException("Unknown required field: " + id);
        }
        return definition;
    }

    public Map<String, FieldDefinition> fields() {
        return fields;
    }

    public List<MegaRegionDefinition> megaRegions() {
        return megaRegions;
    }

    public Map<String, LithologyDefinition> lithologies() {
        return lithologies;
    }

    public LithologyDefinition lithology(String id) {
        LithologyDefinition definition = lithologies.get(id);
        if (definition == null) {
            throw new IllegalArgumentException("Unknown lithology: " + id);
        }
        return definition;
    }

    public WatershedDefinition watershed() {
        return watershed;
    }

    public boolean cavesEnabled() {
        return cavesEnabled;
    }

    public boolean ravinesEnabled() {
        return ravinesEnabled;
    }

    public String hash() {
        return hash;
    }

    private void validate() {
        if (profileId == null || profileId.trim().isEmpty()) {
            throw new IllegalArgumentException("profile id must not be empty");
        }
        finitePositive("mega region scale", megaRegionScale);
        for (String field : REQUIRED_FIELDS) {
            if (!fields.containsKey(field)) {
                throw new IllegalArgumentException("Missing required field: " + field);
            }
        }
        if (megaRegions.isEmpty()) {
            throw new IllegalArgumentException("At least one mega region is required");
        }
        if (lithologies.isEmpty()) {
            throw new IllegalArgumentException("At least one lithology is required");
        }
        for (MegaRegionDefinition region : megaRegions) {
            region.validate();
            if (!lithologies.containsKey(region.lithology())) {
                throw new IllegalArgumentException(
                        "Mega region " + region.id() + " uses unknown lithology "
                                + region.lithology()
                );
            }
        }
        watershed.validate();
    }

    private String canonicalForm() {
        StringBuilder value = new StringBuilder();
        value.append("profile=").append(profileId)
                .append(";megaScale=").append(format(megaRegionScale));
        for (FieldDefinition field : fields.values()) {
            value.append(";field=").append(field.canonicalForm());
        }
        for (LithologyDefinition lithology : lithologies.values()) {
            value.append(";lithology=").append(lithology.canonicalForm());
        }
        for (MegaRegionDefinition region : megaRegions) {
            value.append(";region=").append(region.canonicalForm());
        }
        value.append(";watershed=").append(watershed.canonicalForm())
                .append(";caves=").append(cavesEnabled)
                .append(";ravines=").append(ravinesEnabled);
        return value.toString();
    }

    private static String sha256(String value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder encoded = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                encoded.append(String.format(Locale.ROOT, "%02x", item & 0xff));
            }
            return encoded.toString();
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    private static String format(double value) {
        return String.format(Locale.ROOT, "%.12f", value);
    }

    private static void finitePositive(String label, double value) {
        if (!Double.isFinite(value) || value <= 0.0D) {
            throw new IllegalArgumentException(label + " must be finite and positive");
        }
    }

    private static void finiteNonNegative(String label, double value) {
        if (!Double.isFinite(value) || value < 0.0D) {
            throw new IllegalArgumentException(label + " must be finite and non-negative");
        }
    }

    private static void finiteUnit(String label, double value) {
        if (!Double.isFinite(value) || value < 0.0D || value > 1.0D) {
            throw new IllegalArgumentException(label + " must be finite and in [0, 1]");
        }
    }

    private static void powerOfTwo(
            String label,
            int value,
            int minimum,
            int maximum
    ) {
        if (value < minimum || value > maximum || (value & (value - 1)) != 0) {
            throw new IllegalArgumentException(
                    label + " must be a power of two in [" + minimum + ", " + maximum + "]"
            );
        }
    }

    public static final class FieldDefinition {

        private final String id;
        private final double scale;
        private final int octaves;
        private final double lacunarity;
        private final double gain;
        private final long seedSalt;

        public FieldDefinition(
                String id,
                double scale,
                int octaves,
                double lacunarity,
                double gain
        ) {
            if (id == null || id.trim().isEmpty()) {
                throw new IllegalArgumentException("field id must not be empty");
            }
            finitePositive("field scale", scale);
            if (octaves < 1 || octaves > 12) {
                throw new IllegalArgumentException("field octaves must be in [1, 12]");
            }
            finitePositive("field lacunarity", lacunarity);
            finitePositive("field gain", gain);
            this.id = id;
            this.scale = scale;
            this.octaves = octaves;
            this.lacunarity = lacunarity;
            this.gain = gain;
            this.seedSalt = stableHash64(id);
        }

        public String id() {
            return id;
        }

        public double scale() {
            return scale;
        }

        public int octaves() {
            return octaves;
        }

        public double lacunarity() {
            return lacunarity;
        }

        public double gain() {
            return gain;
        }

        public long seedSalt() {
            return seedSalt;
        }

        private String canonicalForm() {
            return id + "," + format(scale) + "," + octaves + ","
                    + format(lacunarity) + "," + format(gain) + ","
                    + Long.toUnsignedString(seedSalt, 16);
        }
    }

    public static final class MegaRegionDefinition {

        private final String id;
        private final double weight;
        private final double baseHeight;
        private final double relief;
        private final double temperatureBias;
        private final double moistureBias;
        private final String lithology;
        private final List<String> biomeIds;

        private MegaRegionDefinition(
                String id,
                double weight,
                double baseHeight,
                double relief,
                double temperatureBias,
                double moistureBias,
                String lithology,
                String[] biomeIds
        ) {
            this.id = id;
            this.weight = weight;
            this.baseHeight = baseHeight;
            this.relief = relief;
            this.temperatureBias = temperatureBias;
            this.moistureBias = moistureBias;
            this.lithology = lithology;
            List<String> palette = new ArrayList<>();
            if (biomeIds != null) {
                Collections.addAll(palette, biomeIds);
            }
            this.biomeIds = Collections.unmodifiableList(palette);
        }

        public String id() {
            return id;
        }

        public double weight() {
            return weight;
        }

        public double baseHeight() {
            return baseHeight;
        }

        public double relief() {
            return relief;
        }

        public double temperatureBias() {
            return temperatureBias;
        }

        public double moistureBias() {
            return moistureBias;
        }

        public String lithology() {
            return lithology;
        }

        public List<String> biomeIds() {
            return biomeIds;
        }

        private void validate() {
            if (id == null || id.trim().isEmpty()) {
                throw new IllegalArgumentException("mega region id must not be empty");
            }
            finitePositive("mega region weight", weight);
            if (!Double.isFinite(baseHeight) || !Double.isFinite(relief)
                    || !Double.isFinite(temperatureBias) || !Double.isFinite(moistureBias)) {
                throw new IllegalArgumentException("mega region numeric values must be finite");
            }
            if (relief < 0.0D) {
                throw new IllegalArgumentException("mega region relief must not be negative");
            }
            if (lithology == null || lithology.trim().isEmpty()) {
                throw new IllegalArgumentException("mega region lithology must not be empty");
            }
            if (biomeIds.isEmpty()) {
                throw new IllegalArgumentException("mega region biome palette must not be empty");
            }
            for (String biomeId : biomeIds) {
                if (biomeId == null || !biomeId.contains(":")) {
                    throw new IllegalArgumentException("Biome ids must be namespaced: " + biomeId);
                }
            }
        }

        private String canonicalForm() {
            return id + "," + format(weight) + "," + format(baseHeight) + ","
                    + format(relief) + "," + format(temperatureBias) + ","
                    + format(moistureBias) + "," + lithology + ","
                    + String.join("|", biomeIds);
        }
    }

    public static final class LithologyDefinition {

        private final String id;
        private final double permeability;
        private final double erodibility;

        private LithologyDefinition(String id, double permeability, double erodibility) {
            this.id = id;
            this.permeability = permeability;
            this.erodibility = erodibility;
            validate();
        }

        public String id() {
            return id;
        }

        public double permeability() {
            return permeability;
        }

        public double erodibility() {
            return erodibility;
        }

        private void validate() {
            if (id == null || id.trim().isEmpty()) {
                throw new IllegalArgumentException("lithology id must not be empty");
            }
            finiteUnit("lithology permeability", permeability);
            finiteUnit("lithology erodibility", erodibility);
        }

        private String canonicalForm() {
            return id + "," + format(permeability) + "," + format(erodibility);
        }
    }

    public static final class WatershedDefinition {

        private final int cellSizeBlocks;
        private final int tileSizeCells;
        private final int haloCells;
        private final double baseRunoff;
        private final double rainfallScale;
        private final double permeabilityInfluence;
        private final double streamDischarge;
        private final double riverDischarge;
        private final double streamHalfWidthBlocks;
        private final double riverHalfWidthBlocks;
        private final double streamDepthBlocks;
        private final double riverDepthBlocks;
        private final double bankBlendBlocks;
        private final double lakeMinimumFillDepth;

        private WatershedDefinition(WatershedBuilder builder) {
            this.cellSizeBlocks = builder.cellSizeBlocks;
            this.tileSizeCells = builder.tileSizeCells;
            this.haloCells = builder.haloCells;
            this.baseRunoff = builder.baseRunoff;
            this.rainfallScale = builder.rainfallScale;
            this.permeabilityInfluence = builder.permeabilityInfluence;
            this.streamDischarge = builder.streamDischarge;
            this.riverDischarge = builder.riverDischarge;
            this.streamHalfWidthBlocks = builder.streamHalfWidthBlocks;
            this.riverHalfWidthBlocks = builder.riverHalfWidthBlocks;
            this.streamDepthBlocks = builder.streamDepthBlocks;
            this.riverDepthBlocks = builder.riverDepthBlocks;
            this.bankBlendBlocks = builder.bankBlendBlocks;
            this.lakeMinimumFillDepth = builder.lakeMinimumFillDepth;
        }

        public int cellSizeBlocks() {
            return cellSizeBlocks;
        }

        public int tileSizeCells() {
            return tileSizeCells;
        }

        public int haloCells() {
            return haloCells;
        }

        public double baseRunoff() {
            return baseRunoff;
        }

        public double rainfallScale() {
            return rainfallScale;
        }

        public double permeabilityInfluence() {
            return permeabilityInfluence;
        }

        public double streamDischarge() {
            return streamDischarge;
        }

        public double riverDischarge() {
            return riverDischarge;
        }

        public double streamHalfWidthBlocks() {
            return streamHalfWidthBlocks;
        }

        public double riverHalfWidthBlocks() {
            return riverHalfWidthBlocks;
        }

        public double streamDepthBlocks() {
            return streamDepthBlocks;
        }

        public double riverDepthBlocks() {
            return riverDepthBlocks;
        }

        public double bankBlendBlocks() {
            return bankBlendBlocks;
        }

        public double lakeMinimumFillDepth() {
            return lakeMinimumFillDepth;
        }

        private void validate() {
            powerOfTwo("watershed cell size", cellSizeBlocks, 4, 64);
            powerOfTwo("watershed tile size", tileSizeCells, 8, 128);
            if (haloCells < 2 || haloCells > 64
                    || tileSizeCells + haloCells * 2 > 256) {
                throw new IllegalArgumentException(
                        "watershed halo must be in [2, 64] with an extended grid at most 256"
                );
            }
            finiteNonNegative("watershed base runoff", baseRunoff);
            finitePositive("watershed rainfall scale", rainfallScale);
            finiteUnit("watershed permeability influence", permeabilityInfluence);
            finitePositive("watershed stream discharge", streamDischarge);
            finitePositive("watershed river discharge", riverDischarge);
            if (riverDischarge <= streamDischarge) {
                throw new IllegalArgumentException(
                        "watershed river discharge must exceed stream discharge"
                );
            }
            finitePositive("watershed stream half width", streamHalfWidthBlocks);
            finitePositive("watershed river half width", riverHalfWidthBlocks);
            if (riverHalfWidthBlocks < streamHalfWidthBlocks) {
                throw new IllegalArgumentException(
                        "watershed river width must not be below stream width"
                );
            }
            finitePositive("watershed stream depth", streamDepthBlocks);
            finitePositive("watershed river depth", riverDepthBlocks);
            finitePositive("watershed bank blend", bankBlendBlocks);
            finitePositive("watershed lake fill depth", lakeMinimumFillDepth);
        }

        private String canonicalForm() {
            return cellSizeBlocks + "," + tileSizeCells + "," + haloCells + ","
                    + format(baseRunoff) + "," + format(rainfallScale) + ","
                    + format(permeabilityInfluence) + "," + format(streamDischarge) + ","
                    + format(riverDischarge) + "," + format(streamHalfWidthBlocks) + ","
                    + format(riverHalfWidthBlocks) + "," + format(streamDepthBlocks) + ","
                    + format(riverDepthBlocks) + "," + format(bankBlendBlocks) + ","
                    + format(lakeMinimumFillDepth);
        }
    }

    public static final class Builder {

        private String profileId = DEFAULT_PROFILE_ID;
        private double megaRegionScale = 3072.0D;
        private final Map<String, FieldDefinition> fields = new LinkedHashMap<>();
        private final Map<String, LithologyDefinition> lithologies = new LinkedHashMap<>();
        private final List<MegaRegionDefinition> megaRegions = new ArrayList<>();
        private final WatershedBuilder watershed = new WatershedBuilder();
        private boolean cavesEnabled = true;
        private boolean ravinesEnabled = true;

        private Builder() {
        }

        public Builder profile(String profileId) {
            this.profileId = profileId;
            return this;
        }

        public Builder megaRegionScale(double scale) {
            this.megaRegionScale = scale;
            return this;
        }

        public Builder field(
                String id,
                double scale,
                int octaves,
                double lacunarity,
                double gain
        ) {
            this.fields.put(id, new FieldDefinition(id, scale, octaves, lacunarity, gain));
            return this;
        }

        public Builder clearMegaRegions() {
            this.megaRegions.clear();
            return this;
        }

        public Builder lithology(String id, double permeability, double erodibility) {
            this.lithologies.put(
                    id,
                    new LithologyDefinition(id, permeability, erodibility)
            );
            return this;
        }

        public Builder megaRegion(
                String id,
                double weight,
                double baseHeight,
                double relief,
                double temperatureBias,
                double moistureBias,
                String lithology,
                String... biomeIds
        ) {
            this.megaRegions.add(new MegaRegionDefinition(
                    id,
                    weight,
                    baseHeight,
                    relief,
                    temperatureBias,
                    moistureBias,
                    lithology,
                    biomeIds
            ));
            return this;
        }

        public Builder watershedGrid(int cellSizeBlocks, int tileSizeCells, int haloCells) {
            watershed.cellSizeBlocks = cellSizeBlocks;
            watershed.tileSizeCells = tileSizeCells;
            watershed.haloCells = haloCells;
            return this;
        }

        public Builder watershedRunoff(
                double baseRunoff,
                double rainfallScale,
                double permeabilityInfluence,
                double streamDischarge,
                double riverDischarge
        ) {
            watershed.baseRunoff = baseRunoff;
            watershed.rainfallScale = rainfallScale;
            watershed.permeabilityInfluence = permeabilityInfluence;
            watershed.streamDischarge = streamDischarge;
            watershed.riverDischarge = riverDischarge;
            return this;
        }

        public Builder watershedChannels(
                double streamHalfWidthBlocks,
                double streamDepthBlocks,
                double riverHalfWidthBlocks,
                double riverDepthBlocks,
                double bankBlendBlocks,
                double lakeMinimumFillDepth
        ) {
            watershed.streamHalfWidthBlocks = streamHalfWidthBlocks;
            watershed.streamDepthBlocks = streamDepthBlocks;
            watershed.riverHalfWidthBlocks = riverHalfWidthBlocks;
            watershed.riverDepthBlocks = riverDepthBlocks;
            watershed.bankBlendBlocks = bankBlendBlocks;
            watershed.lakeMinimumFillDepth = lakeMinimumFillDepth;
            return this;
        }

        public Builder carvers(boolean cavesEnabled, boolean ravinesEnabled) {
            this.cavesEnabled = cavesEnabled;
            this.ravinesEnabled = ravinesEnabled;
            return this;
        }

        public WorldStudioPlan build() {
            return new WorldStudioPlan(this);
        }
    }

    private static final class WatershedBuilder {

        private int cellSizeBlocks = 16;
        private int tileSizeCells = 32;
        private int haloCells = 24;
        private double baseRunoff = 0.08D;
        private double rainfallScale = 1.0D;
        private double permeabilityInfluence = 0.75D;
        private double streamDischarge = 8.0D;
        private double riverDischarge = 45.0D;
        private double streamHalfWidthBlocks = 1.5D;
        private double riverHalfWidthBlocks = 5.0D;
        private double streamDepthBlocks = 2.0D;
        private double riverDepthBlocks = 5.0D;
        private double bankBlendBlocks = 2.0D;
        private double lakeMinimumFillDepth = 1.5D;

        private WatershedDefinition build() {
            return new WatershedDefinition(this);
        }
    }

    public static long stableHash64(String value) {
        long hash = 0xcbf29ce484222325L;
        byte[] bytes = value.getBytes(StandardCharsets.UTF_8);
        for (byte item : bytes) {
            hash ^= item & 0xffL;
            hash *= 0x100000001b3L;
        }
        return hash;
    }
}
