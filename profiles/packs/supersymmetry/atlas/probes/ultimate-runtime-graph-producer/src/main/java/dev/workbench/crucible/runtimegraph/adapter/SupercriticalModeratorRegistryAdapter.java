package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.PackCoreReflection;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.block.Block;
import net.minecraft.block.state.IBlockState;
import supercritical.api.nuclear.fission.IModeratorStats;
import supercritical.api.nuclear.fission.ModeratorRegistry;
import supercritical.api.nuclear.fission.ModeratorRegistry.ModeratorInfo;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Supercritical moderator block-state bindings and physics. */
public final class SupercriticalModeratorRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "supercritical-moderator-registry"; }
    @Override public String categoryId() { return "supercritical-moderators"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<ModeratorInfo, IModeratorStats> registry = PackCoreReflection.staticMap(
            ModeratorRegistry.class, "MODERATORS"
        );
        for (Map.Entry<ModeratorInfo, IModeratorStats> entry : registry.entrySet()) {
            ModeratorInfo info = entry.getKey();
            IModeratorStats stats = entry.getValue();
            if (stats == null) throw new IllegalStateException("Supercritical moderator stats are null");
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "supercritical-moderator");
            row.addProperty("registry_name", info.getRegistryName().toString());
            row.addProperty("meta", info.getMeta());
            row.addProperty("runtime_class", stats.getClass().getName());
            row.addProperty("maximum_temperature", stats.getMaxTemperature());
            row.add("moderation_factor", encoder.encode(Double.valueOf(stats.getModerationFactor())));
            row.add("absorption_factor", encoder.encode(Double.valueOf(stats.getAbsorptionFactor())));
            Block block = Block.REGISTRY.getObject(info.getRegistryName());
            if (block == null) {
                row.add("block_state", JsonNull.INSTANCE);
                row.addProperty("public_lookup_consistent", false);
            } else {
                IBlockState state = block.getStateFromMeta(info.getMeta());
                row.add("block_state", encoder.encode(state));
                row.addProperty("public_lookup_consistent", ModeratorRegistry.getModerator(state) == stats);
            }
            records.add(row);
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "supercritical-moderator-registry-authority");
        authority.addProperty("moderator_count", registry.size());
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
