package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.Hashing;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.capability.GregtechCapabilities;

import net.minecraft.creativetab.CreativeTabs;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.NonNullList;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.energy.CapabilityEnergy;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;
import net.minecraftforge.fluids.FluidStack;
import net.minecraftforge.fluids.capability.CapabilityFluidHandler;
import net.minecraftforge.items.CapabilityItemHandler;

import java.util.AbstractList;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Bounded complete public serialization samples, independent from occurrence capture. */
public final class SerializedRuntimeValueAdapter implements CaptureAdapter {
    private static final int BASELINE_ITEM_LIMIT = 64;
    private static final int ITEM_REGISTRY_LIMIT = 65536;
    private static final int CAPABILITY_ITEM_LIMIT = 256;
    private static final int CAPABILITY_CANDIDATE_LIMIT = 65536;
    private static final int VARIANT_PROVIDER_ITEM_LIMIT = 1024;
    private static final int FLUID_LIMIT = 64;
    private static final int FLUID_REGISTRY_LIMIT = 16384;
    private static final int VALUE_BYTE_LIMIT = 8 * 1024 * 1024;
    private static final int TOTAL_BYTE_LIMIT = 64 * 1024 * 1024;
    private static final int TOTAL_NODE_LIMIT = 1000000;
    private static final int TOTAL_TEXT_CODE_POINT_LIMIT = 8 * 1024 * 1024;
    private static final int RECORD_DEPTH_LIMIT = 128;
    private static final int RECORD_NODE_LIMIT = 65536;
    private static final int RECORD_TEXT_CODE_POINT_LIMIT = 1024 * 1024;
    private static final int NBT_NODE_LIMIT = 60000;
    private static final int NBT_TEXT_CODE_POINT_LIMIT = 1000000;

    @Override public String adapterId() { return "serialized-runtime-values"; }
    @Override public String categoryId() { return "runtime-value-serialization"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<ResourceLocation> itemKeys = Item.REGISTRY.getKeys();
        if (itemKeys.size() > ITEM_REGISTRY_LIMIT) {
            throw new IllegalStateException("item registry exceeds capture census bound");
        }
        List<ResourceLocation> items = new ArrayList<ResourceLocation>(itemKeys);
        Collections.sort(items, new Comparator<ResourceLocation>() {
            @Override
            public int compare(ResourceLocation left, ResourceLocation right) {
                return left.compareTo(right);
            }
        });
        Set<String> selected = new HashSet<String>();
        Set<String> capabilitySelected = new HashSet<String>();
        int baselineCount = 0;
        int capabilityCount = 0;
        int capabilityCandidateCount = 0;
        int variantProviderFailureCount = 0;
        int variantProviderTruncationCount = 0;
        int nondeterministicWithheldCount = 0;
        int boundedWithheldCount = 0;
        CorpusBudget corpus = new CorpusBudget();
        for (ResourceLocation name : items) {
            if (baselineCount >= BASELINE_ITEM_LIMIT) break;
            Item item = Item.REGISTRY.getObject(name);
            ItemStack stack = new ItemStack(item, 1, 0);
            if (stack.isEmpty()) continue;
            JsonObject record = itemRecord(
                name,
                stack,
                "registry-baseline",
                publicCapabilityIds(stack),
                encoder
            );
            if (record.get("serialization_withheld").getAsBoolean()) {
                if (isBoundedWithheld(record)) boundedWithheldCount++;
                else nondeterministicWithheldCount++;
            }
            corpus.add(record);
            records.add(record);
            selected.add(selectionKey(name, stack));
            baselineCount++;
        }
        capabilitySearch:
        for (ResourceLocation name : items) {
            if (capabilityCount >= CAPABILITY_ITEM_LIMIT) break;
            int remainingCandidates = CAPABILITY_CANDIDATE_LIMIT - capabilityCandidateCount;
            if (remainingCandidates <= 0) break;
            Item item = Item.REGISTRY.getObject(name);
            BoundedItemStackBackingList candidateBacking =
                new BoundedItemStackBackingList(
                    Math.min(VARIANT_PROVIDER_ITEM_LIMIT, remainingCandidates)
                );
            NonNullList<ItemStack> candidates = new BoundedItemStackList(candidateBacking);
            candidates.add(new ItemStack(item, 1, 0));
            if (item.getHasSubtypes() && item.getCreativeTab() != null) {
                try {
                    item.getSubItems(CreativeTabs.SEARCH, candidates);
                } catch (RuntimeException failure) {
                    variantProviderFailureCount++;
                }
            }
            if (candidateBacking.wasTruncated()) variantProviderTruncationCount++;
            Collections.sort(candidates, new Comparator<ItemStack>() {
                @Override
                public int compare(ItemStack left, ItemStack right) {
                    return Integer.compare(left.getMetadata(), right.getMetadata());
                }
            });
            for (ItemStack stack : candidates) {
                if (capabilityCandidateCount >= CAPABILITY_CANDIDATE_LIMIT) {
                    break capabilitySearch;
                }
                capabilityCandidateCount++;
                if (stack.isEmpty()) continue;
                ItemStack normalized = new ItemStack(item, 1, stack.getMetadata());
                JsonArray capabilityIds = publicCapabilityIds(normalized);
                if (capabilityIds.size() == 0) continue;
                String key = selectionKey(name, normalized);
                if (!capabilitySelected.add(key)) continue;
                if (selected.add(key)) {
                    JsonObject record = itemRecord(
                        name,
                        normalized,
                        "public-capability-bearing",
                        capabilityIds,
                        encoder
                    );
                    if (record.get("serialization_withheld").getAsBoolean()) {
                        if (isBoundedWithheld(record)) boundedWithheldCount++;
                        else nondeterministicWithheldCount++;
                    }
                    corpus.add(record);
                    records.add(record);
                }
                capabilityCount++;
                if (capabilityCount >= CAPABILITY_ITEM_LIMIT) break capabilitySearch;
            }
        }
        if (baselineCount != BASELINE_ITEM_LIMIT || capabilityCount == 0) {
            throw new IllegalStateException(
                "bounded serialized item corpus is unavailable: baseline="
                    + baselineCount + " capability=" + capabilityCount
            );
        }

        java.util.Map<String, Fluid> registeredFluids =
            FluidRegistry.getRegisteredFluids();
        if (registeredFluids.size() > FLUID_REGISTRY_LIMIT) {
            throw new IllegalStateException("fluid registry exceeds capture census bound");
        }
        List<java.util.Map.Entry<String, Fluid>> fluids =
            new ArrayList<java.util.Map.Entry<String, Fluid>>(
                registeredFluids.entrySet()
            );
        Collections.sort(fluids, new Comparator<java.util.Map.Entry<String, Fluid>>() {
            @Override
            public int compare(
                java.util.Map.Entry<String, Fluid> left,
                java.util.Map.Entry<String, Fluid> right
            ) {
                return left.getKey().compareTo(right.getKey());
            }
        });
        int fluidCount = 0;
        for (java.util.Map.Entry<String, Fluid> entry : fluids) {
            if (fluidCount >= FLUID_LIMIT) break;
            final Fluid fluid = entry.getValue();
            JsonObject row = serialized(
                "net.minecraftforge.fluids.FluidStack",
                new NbtSupplier() {
                    @Override
                    public NBTTagCompound get() {
                        return new FluidStack(fluid, 1000).writeToNBT(
                            new NBTTagCompound()
                        );
                    }
                },
                new NbtSupplier() {
                    @Override
                    public NBTTagCompound get() {
                        return new FluidStack(fluid, 1000).writeToNBT(
                            new NBTTagCompound()
                        );
                    }
                },
                encoder
            );
            row.addProperty(
                "record_type",
                row.get("serialization_withheld").getAsBoolean()
                    ? "serialized-runtime-value-withheld"
                    : "serialized-runtime-value"
            );
            row.addProperty("selection_role", "fluid-registry-baseline");
            row.addProperty("fluid_name", entry.getKey());
            if (row.get("serialization_withheld").getAsBoolean()) {
                if (isBoundedWithheld(row)) boundedWithheldCount++;
                else nondeterministicWithheldCount++;
            }
            corpus.add(row);
            records.add(row);
            fluidCount++;
        }
        if (fluidCount != FLUID_LIMIT) {
            throw new IllegalStateException("bounded serialized fluid corpus is unavailable");
        }

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "serialized-runtime-value-authority");
        authority.addProperty("item_registry_size", items.size());
        authority.addProperty("item_registry_limit", ITEM_REGISTRY_LIMIT);
        authority.addProperty("baseline_item_count", baselineCount);
        authority.addProperty("capability_bearing_item_count", capabilityCount);
        authority.addProperty("fluid_registry_size", fluids.size());
        authority.addProperty("fluid_registry_limit", FLUID_REGISTRY_LIMIT);
        authority.addProperty("fluid_count", fluidCount);
        authority.addProperty("baseline_item_limit", BASELINE_ITEM_LIMIT);
        authority.addProperty("capability_item_limit", CAPABILITY_ITEM_LIMIT);
        authority.addProperty("capability_candidate_limit", CAPABILITY_CANDIDATE_LIMIT);
        authority.addProperty("capability_candidate_count", capabilityCandidateCount);
        authority.addProperty("variant_provider_item_limit", VARIANT_PROVIDER_ITEM_LIMIT);
        authority.addProperty("variant_provider_failure_count", variantProviderFailureCount);
        authority.addProperty(
            "variant_provider_truncation_count",
            variantProviderTruncationCount
        );
        authority.addProperty(
            "nondeterministic_value_withheld_count",
            nondeterministicWithheldCount
        );
        authority.addProperty("bounded_value_withheld_count", boundedWithheldCount);
        authority.addProperty("fluid_limit", FLUID_LIMIT);
        authority.addProperty(
            "capability_discovery",
            "sorted-item-registry-default-plus-output-bounded-lowest-metadata-search-tab-subtypes-until-count-or-candidate-bound"
        );
        authority.addProperty(
            "capability_selection_order",
            "registry-name-then-retained-metadata-ascending"
        );
        JsonArray capabilityDetectionIds = new JsonArray();
        capabilityDetectionIds.add("forge:energy-item");
        capabilityDetectionIds.add("forge:fluid-handler-item");
        capabilityDetectionIds.add("forge:item-handler-item");
        capabilityDetectionIds.add("gregtech:electric-item");
        authority.add("public_capability_detection_ids", capabilityDetectionIds);
        authority.addProperty("all_mod_capabilities_discovered", false);
        authority.addProperty("provider_internal_allocation_bounded", false);
        authority.addProperty(
            "provider_internal_allocation_scope",
            "capture-output-bounded-after-provider-return"
        );
        authority.addProperty("serialized_value_byte_limit", VALUE_BYTE_LIMIT);
        authority.addProperty("serialized_corpus_byte_limit", TOTAL_BYTE_LIMIT);
        authority.addProperty("serialized_corpus_byte_count", corpus.byteCount);
        authority.addProperty("serialized_corpus_node_limit", TOTAL_NODE_LIMIT);
        authority.addProperty("serialized_corpus_node_count", corpus.nodeCount);
        authority.addProperty(
            "serialized_corpus_text_codepoint_limit",
            TOTAL_TEXT_CODE_POINT_LIMIT
        );
        authority.addProperty(
            "serialized_corpus_text_codepoint_count",
            corpus.textCodePointCount
        );
        authority.addProperty("repeated_sample_retained_tree_multiplier", 3);
        authority.addProperty("serialized_record_max_depth", RECORD_DEPTH_LIMIT);
        authority.addProperty("serialized_record_max_nodes", RECORD_NODE_LIMIT);
        authority.addProperty(
            "serialized_record_max_text_codepoints",
            RECORD_TEXT_CODE_POINT_LIMIT
        );
        authority.addProperty("serialized_nbt_max_nodes", NBT_NODE_LIMIT);
        authority.addProperty(
            "serialized_nbt_max_text_codepoints",
            NBT_TEXT_CODE_POINT_LIMIT
        );
        authority.addProperty("arbitrary_runtime_type_support", false);
        authority.addProperty("occurrence_serialization_inlined", false);
        authority.addProperty("repeat_policy", "two-byte-identical-category-samples");
        records.add(authority);
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static JsonObject itemRecord(
        ResourceLocation name,
        ItemStack stack,
        String role,
        JsonArray capabilityIds,
        StableValueEncoder encoder
    ) {
        ItemStack independentlyReconstructed = new ItemStack(
            stack.getItem(),
            1,
            stack.getMetadata()
        );
        JsonArray reconstructedCapabilityIds = publicCapabilityIds(
            independentlyReconstructed
        );
        final boolean[] firstForgeCapsPresent = new boolean[] {false};
        JsonObject row = serialized(
            "net.minecraft.item.ItemStack",
            new NbtSupplier() {
                @Override
                public NBTTagCompound get() {
                    NBTTagCompound value = stack.serializeNBT();
                    firstForgeCapsPresent[0] = value.hasKey("ForgeCaps", 10);
                    return value;
                }
            },
            new NbtSupplier() {
                @Override
                public NBTTagCompound get() {
                    return independentlyReconstructed.serializeNBT();
                }
            },
            encoder
        );
        if (!capabilityIds.toString().equals(reconstructedCapabilityIds.toString())) {
            row = withheld("net.minecraft.item.ItemStack", "unstable-public-capability-set");
        }
        row.addProperty(
            "record_type",
            row.get("serialization_withheld").getAsBoolean()
                ? "serialized-runtime-value-withheld"
                : "serialized-runtime-value"
        );
        row.addProperty("selection_role", role);
        row.addProperty("item_registry_name", name.toString());
        row.addProperty("metadata", stack.getMetadata());
        row.addProperty(
            "serialized_forge_caps_present",
            firstForgeCapsPresent[0]
        );
        row.addProperty("public_capability_count", capabilityIds.size());
        row.add("public_capability_ids", capabilityIds);
        return row;
    }

    private static String selectionKey(ResourceLocation name, ItemStack stack) {
        return name.toString() + "|" + stack.getMetadata();
    }

    private static JsonArray publicCapabilityIds(ItemStack stack) {
        JsonArray ids = new JsonArray();
        if (
            CapabilityEnergy.ENERGY != null
                && stack.hasCapability(CapabilityEnergy.ENERGY, null)
        ) {
            ids.add("forge:energy-item");
        }
        if (
            CapabilityFluidHandler.FLUID_HANDLER_ITEM_CAPABILITY != null
                && stack.hasCapability(
                    CapabilityFluidHandler.FLUID_HANDLER_ITEM_CAPABILITY,
                    null
                )
        ) {
            ids.add("forge:fluid-handler-item");
        }
        if (
            CapabilityItemHandler.ITEM_HANDLER_CAPABILITY != null
                && stack.hasCapability(CapabilityItemHandler.ITEM_HANDLER_CAPABILITY, null)
        ) {
            ids.add("forge:item-handler-item");
        }
        if (
            GregtechCapabilities.CAPABILITY_ELECTRIC_ITEM != null
                && stack.hasCapability(
                    GregtechCapabilities.CAPABILITY_ELECTRIC_ITEM,
                    null
                )
        ) {
            ids.add("gregtech:electric-item");
        }
        return ids;
    }

    private static JsonObject serialized(
        String runtimeType,
        NbtSupplier firstSerialized,
        NbtSupplier secondSerialized,
        StableValueEncoder encoder
    ) {
        JsonElement canonicalValue;
        JsonElement repeatedCanonicalValue;
        byte[] bytes;
        byte[] repeatedBytes;
        try {
            NBTTagCompound firstValue = firstSerialized.get();
            canonicalValue = encoder.encodeBoundedNbt(
                firstValue,
                RECORD_DEPTH_LIMIT,
                NBT_NODE_LIMIT,
                NBT_TEXT_CODE_POINT_LIMIT
            );
            firstValue = null;
            bytes = CanonicalJson.bytes(canonicalValue);
            if (bytes.length > VALUE_BYTE_LIMIT) {
                return withheld(runtimeType, "bounded-byte-limit");
            }
            repeatedCanonicalValue = encoder.encodeBoundedNbt(
                secondSerialized.get(),
                RECORD_DEPTH_LIMIT,
                NBT_NODE_LIMIT,
                NBT_TEXT_CODE_POINT_LIMIT
            );
            repeatedBytes = CanonicalJson.bytes(repeatedCanonicalValue);
            if (repeatedBytes.length > VALUE_BYTE_LIMIT) {
                return withheld(runtimeType, "bounded-byte-limit");
            }
        } catch (StableValueEncoder.EncodingLimitException bounded) {
            return withheld(runtimeType, "bounded-" + bounded.getCode());
        }
        if (!java.util.Arrays.equals(bytes, repeatedBytes)) {
            return withheld(runtimeType, "unstable-independent-reconstruction");
        }
        JsonObject row = new JsonObject();
        row.addProperty("runtime_type", runtimeType);
        row.addProperty("serialization_encoding", "minecraft-nbt-canonical-json-v1");
        row.addProperty("content_sha256", Hashing.sha256(bytes));
        row.addProperty("byte_count", bytes.length);
        row.addProperty("redaction_state", "none");
        row.addProperty(
            "nondeterminism_state",
            "stable-independent-reconstruction-and-repeated-category-sample"
        );
        row.addProperty("serialization_withheld", false);
        row.add("canonical_value", canonicalValue);
        row.add("unsupported_type_ids", new JsonArray());
        return row;
    }

    private static JsonObject withheld(String runtimeType, String reason) {
        JsonObject row = new JsonObject();
        row.addProperty("runtime_type", runtimeType);
        row.addProperty("serialization_encoding", "minecraft-nbt-canonical-json-v1");
        row.addProperty("redaction_state", "none");
        row.addProperty("nondeterminism_state", "withheld-" + reason);
        row.addProperty("serialization_withheld", true);
        row.addProperty("withheld_reason", reason);
        row.add("unsupported_type_ids", new JsonArray());
        return row;
    }

    private static boolean isBoundedWithheld(JsonObject record) {
        return record.has("withheld_reason")
            && record.get("withheld_reason").getAsString().startsWith("bounded-");
    }

    private interface NbtSupplier {
        NBTTagCompound get();
    }

    private static final class BoundedItemStackBackingList
        extends AbstractList<ItemStack> {
        private final List<ItemStack> values = new ArrayList<ItemStack>();
        private final int limit;
        private boolean truncated;

        private BoundedItemStackBackingList(int limit) {
            if (limit < 1) throw new IllegalArgumentException("list limit must be positive");
            this.limit = limit;
        }

        @Override public ItemStack get(int index) { return values.get(index); }
        @Override public int size() { return values.size(); }
        @Override public ItemStack set(int index, ItemStack value) {
            return values.set(index, value);
        }
        @Override public ItemStack remove(int index) { return values.remove(index); }
        @Override public void clear() { values.clear(); }

        @Override
        public void add(int index, ItemStack value) {
            int metadata = value.getMetadata();
            for (ItemStack existing : values) {
                if (existing.getMetadata() == metadata) return;
            }
            if (values.size() < limit) {
                values.add(value);
                return;
            }
            truncated = true;
            int greatestIndex = 0;
            for (int candidate = 1; candidate < values.size(); candidate++) {
                if (values.get(candidate).getMetadata()
                    > values.get(greatestIndex).getMetadata()) {
                    greatestIndex = candidate;
                }
            }
            if (metadata < values.get(greatestIndex).getMetadata()) {
                values.set(greatestIndex, value);
            }
        }

        private boolean wasTruncated() {
            return truncated;
        }
    }

    private static final class BoundedItemStackList extends NonNullList<ItemStack> {
        private BoundedItemStackList(BoundedItemStackBackingList values) {
            super(values, null);
        }
    }

    private static final class CorpusBudget {
        private long byteCount;
        private long nodeCount;
        private long textCodePointCount;

        private void add(JsonObject record) {
            RecordMetrics metrics = new RecordMetrics();
            measure(record, 0, metrics);
            if (metrics.maximumDepth > RECORD_DEPTH_LIMIT
                || metrics.nodeCount > RECORD_NODE_LIMIT
                || metrics.textCodePointCount > RECORD_TEXT_CODE_POINT_LIMIT) {
                throw new IllegalStateException(
                    "serialized runtime value exceeds a structural record bound"
                );
            }
            byte[] bytes = CanonicalJson.bytes(record);
            if (bytes.length > VALUE_BYTE_LIMIT) {
                throw new IllegalStateException(
                    "serialized runtime value exceeds the record byte bound"
                );
            }
            if (bytes.length > TOTAL_BYTE_LIMIT - byteCount
                || metrics.nodeCount > TOTAL_NODE_LIMIT - nodeCount
                || metrics.textCodePointCount
                > TOTAL_TEXT_CODE_POINT_LIMIT - textCodePointCount) {
                throw new IllegalStateException(
                    "serialized runtime value corpus exceeds a retained-tree bound"
                );
            }
            byteCount += bytes.length;
            nodeCount += metrics.nodeCount;
            textCodePointCount += metrics.textCodePointCount;
        }
    }

    private static final class RecordMetrics {
        private long nodeCount;
        private long textCodePointCount;
        private int maximumDepth;
    }

    private static void measure(JsonElement value, int depth, RecordMetrics metrics) {
        metrics.nodeCount++;
        metrics.maximumDepth = Math.max(metrics.maximumDepth, depth);
        if (metrics.nodeCount > RECORD_NODE_LIMIT || depth > RECORD_DEPTH_LIMIT) {
            throw new IllegalStateException(
                "serialized runtime value exceeds a structural record bound"
            );
        }
        if (value.isJsonObject()) {
            for (java.util.Map.Entry<String, JsonElement> entry
                : value.getAsJsonObject().entrySet()) {
                metrics.textCodePointCount += entry.getKey().codePointCount(
                    0,
                    entry.getKey().length()
                );
                measure(entry.getValue(), depth + 1, metrics);
            }
        } else if (value.isJsonArray()) {
            for (JsonElement child : value.getAsJsonArray()) {
                measure(child, depth + 1, metrics);
            }
        } else if (value.isJsonPrimitive()) {
            JsonPrimitive primitive = value.getAsJsonPrimitive();
            if (primitive.isString()) {
                String text = primitive.getAsString();
                metrics.textCodePointCount += text.codePointCount(0, text.length());
            }
        }
        if (metrics.textCodePointCount > RECORD_TEXT_CODE_POINT_LIMIT) {
            throw new IllegalStateException(
                "serialized runtime value exceeds a structural record bound"
            );
        }
    }
}
