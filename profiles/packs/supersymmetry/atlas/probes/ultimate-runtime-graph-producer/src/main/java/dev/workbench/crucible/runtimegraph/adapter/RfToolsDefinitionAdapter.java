package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;
import com.google.gson.JsonNull;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import mcjty.rftools.RFTools;
import mcjty.rftools.apiimpl.ScreenModuleRegistry;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** ReFinedTools/RFTools owned content and finite screen-module data factories. */
public final class RfToolsDefinitionAdapter implements CaptureAdapter {
    private static final Field DATA_FACTORIES = ReflectionAccess.requireAssignableField(
        ScreenModuleRegistry.class, "dataFactoryMap", Map.class
    );
    private static final Field SHORT_IDS = ReflectionAccess.requireAssignableField(
        ScreenModuleRegistry.class, "idToIntMap", Map.class
    );

    @Override public String adapterId() { return "rftools-domain-definitions"; }
    @Override public String categoryId() { return "rftools-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<String> namespaces = CompanionDefinitionSupport.namespaces("rftools");
        int blockCount = CompanionDefinitionSupport.captureOwnedBlocks(
            "rftools", namespaces, records, encoder
        );
        int itemCount = CompanionDefinitionSupport.captureOwnedItems(
            "rftools", namespaces, records, encoder
        );
        ScreenCounts screens = captureScreenFactories(records);
        if (RFTools.setup == null) throw new IllegalStateException("RFTools setup is unavailable");
        if (blockCount == 0 || itemCount == 0 || screens.factoryCount == 0) {
            throw new IllegalStateException("RFTools definition universe is empty");
        }
        requireExactCounts(blockCount, itemCount, screens);

        JsonObject compatibility = new JsonObject();
        compatibility.addProperty("record_type", "rftools-compatibility-state");
        compatibility.addProperty("rftools_dimensions_loaded", RFTools.setup.rftoolsDimensions);
        compatibility.addProperty("xnet_loaded", RFTools.setup.xnet);
        compatibility.addProperty("the_one_probe_loaded", RFTools.setup.top);
        records.add(compatibility);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "rftools-definition-authority");
        authority.addProperty("artifact_line", "ReFinedTools-7.78");
        authority.addProperty("owned_block_count", blockCount);
        authority.addProperty("owned_item_count", itemCount);
        authority.addProperty("screen_module_factory_count", screens.factoryCount);
        authority.addProperty(
            "screen_module_short_id_binding_count", screens.shortIdBindingCount
        );
        authority.addProperty(
            "screen_module_short_id_map_materialized", screens.shortIdMapMaterialized
        );
        authority.addProperty(
            "factory_without_short_id_count", screens.factoryWithoutShortIdCount
        );
        authority.addProperty(
            "short_id_without_factory_count", screens.shortIdWithoutFactoryCount
        );
        authority.addProperty("screen_module_execution_invoked", false);
        authority.addProperty("screen_module_short_id_map_creation_invoked", false);
        authority.addProperty("machine_or_storage_operation_invoked", false);
        authority.addProperty("world_or_tile_state_captured", false);
        authority.addProperty("client_behavior_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(
        int blockCount,
        int itemCount,
        ScreenCounts screens
    ) {
        if (blockCount != 75 || itemCount != 134 || screens.factoryCount != 8
            || screens.shortIdMapMaterialized || screens.shortIdBindingCount != 0
            || screens.factoryWithoutShortIdCount != 8
            || screens.shortIdWithoutFactoryCount != 0) {
            throw new IllegalStateException(
                "RFTools definition counts drifted: blocks=" + blockCount
                    + ", items=" + itemCount
                    + ", screen_factories=" + screens.factoryCount
                    + ", short_id_map_materialized=" + screens.shortIdMapMaterialized
                    + ", short_id_bindings=" + screens.shortIdBindingCount
                    + ", factories_without_short_id=" + screens.factoryWithoutShortIdCount
                    + ", short_ids_without_factory=" + screens.shortIdWithoutFactoryCount
            );
        }
    }

    @SuppressWarnings("unchecked")
    private static ScreenCounts captureScreenFactories(List<JsonObject> records) {
        ScreenModuleRegistry registry = RFTools.screenModuleRegistry;
        if (registry == null) throw new IllegalStateException("RFTools screen registry is unavailable");
        Map<String, Object> factories = (Map<String, Object>) ReflectionAccess.read(
            DATA_FACTORIES, registry
        );
        Map<String, Integer> shortIds = (Map<String, Integer>) ReflectionAccess.read(
            SHORT_IDS, registry
        );
        if (factories == null) {
            throw new IllegalStateException("RFTools screen factory registry is unavailable");
        }
        ScreenCounts counts = new ScreenCounts();
        counts.shortIdMapMaterialized = shortIds != null;
        List<String> ids = new ArrayList<String>(factories.keySet());
        Collections.sort(ids);
        for (String id : ids) {
            Object factory = factories.get(id);
            Integer shortId = shortIds == null ? null : shortIds.get(id);
            if (id == null || id.isEmpty() || factory == null) {
                throw new IllegalStateException("invalid RFTools screen module factory");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "rftools-screen-module-data-factory");
            row.addProperty("module_data_id", id);
            if (shortId == null) {
                row.add("short_id", JsonNull.INSTANCE);
                counts.factoryWithoutShortIdCount++;
            } else {
                row.addProperty("short_id", shortId.intValue());
            }
            row.addProperty("short_id_binding_observed", shortId != null);
            row.addProperty(
                "factory_runtime_class", StableValueEncoder.stableClassName(factory.getClass())
            );
            row.addProperty("factory_invoked", false);
            records.add(row);
        }
        counts.factoryCount = ids.size();

        List<String> bindingIds = shortIds == null
            ? new ArrayList<String>()
            : new ArrayList<String>(shortIds.keySet());
        Collections.sort(bindingIds);
        for (String id : bindingIds) {
            Integer shortId = shortIds.get(id);
            Object factory = factories.get(id);
            if (id == null || id.isEmpty() || shortId == null) {
                throw new IllegalStateException("invalid RFTools screen short-ID binding");
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "rftools-screen-module-short-id-binding");
            row.addProperty("module_data_id", id);
            row.addProperty("short_id", shortId.intValue());
            row.addProperty("factory_observed", factory != null);
            if (factory == null) {
                row.add("factory_runtime_class", JsonNull.INSTANCE);
                counts.shortIdWithoutFactoryCount++;
            } else {
                row.addProperty(
                    "factory_runtime_class", StableValueEncoder.stableClassName(factory.getClass())
                );
            }
            records.add(row);
        }
        counts.shortIdBindingCount = bindingIds.size();

        JsonObject shortIdAuthority = new JsonObject();
        shortIdAuthority.addProperty(
            "record_type", "rftools-screen-module-short-id-authority"
        );
        shortIdAuthority.addProperty(
            "binding_state", shortIds == null ? "unmaterialized-at-checkpoint" : "materialized"
        );
        shortIdAuthority.addProperty("binding_count", counts.shortIdBindingCount);
        shortIdAuthority.addProperty("map_creation_invoked", false);
        shortIdAuthority.addProperty(
            "unmaterialized_does_not_mean_absent", shortIds == null
        );
        records.add(shortIdAuthority);
        return counts;
    }

    private static final class ScreenCounts {
        private int factoryCount;
        private int shortIdBindingCount;
        private int factoryWithoutShortIdCount;
        private int shortIdWithoutFactoryCount;
        private boolean shortIdMapMaterialized;
    }
}
