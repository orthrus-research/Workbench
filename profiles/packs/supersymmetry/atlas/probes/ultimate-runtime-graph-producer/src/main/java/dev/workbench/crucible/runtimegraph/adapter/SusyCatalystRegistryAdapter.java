package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.api.recipes.catalysts.CatalystGroup;
import supersymmetry.api.recipes.catalysts.CatalystInfo;

import net.minecraft.item.ItemStack;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/** Final SuSy catalyst groups and item-specific efficiency classifications. */
public final class SusyCatalystRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-catalyst-registry"; }
    @Override public String categoryId() { return "susy-catalysts"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        List<CatalystGroup> groups = CatalystGroup.getCatalystGroups();
        if (groups == null) throw new IllegalStateException("SuSy catalyst group registry is null");
        for (CatalystGroup group : groups) {
            List<Map.Entry<ItemStack, CatalystInfo>> entries = group.getCatalystInfos()
                .streamEntries().collect(Collectors.toList());
            JsonObject groupRow = new JsonObject();
            groupRow.addProperty("record_type", "susy-catalyst-group");
            groupRow.addProperty("name", group.getName());
            groupRow.addProperty("entry_count", entries.size());
            records.add(groupRow);
            for (Map.Entry<ItemStack, CatalystInfo> entry : entries) {
                CatalystInfo info = entry.getValue();
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "susy-catalyst-entry");
                row.addProperty("group", group.getName());
                row.add("item", encoder.encode(entry.getKey()));
                row.addProperty("tier", info.getTier());
                row.add("yield_efficiency", encoder.encode(Double.valueOf(info.getYieldEfficiency())));
                row.add("energy_efficiency", encoder.encode(Double.valueOf(info.getEnergyEfficiency())));
                row.add("speed_efficiency", encoder.encode(Double.valueOf(info.getSpeedEfficiency())));
                records.add(row);
            }
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-catalyst-registry-authority");
        authority.addProperty("group_count", groups.size());
        authority.addProperty("authority", "CatalystGroup.getCatalystGroups");
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
