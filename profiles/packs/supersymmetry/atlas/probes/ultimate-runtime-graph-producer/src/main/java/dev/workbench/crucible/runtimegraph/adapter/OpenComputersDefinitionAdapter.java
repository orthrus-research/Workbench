package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import li.cil.oc.api.API;
import li.cil.oc.api.Driver;
import li.cil.oc.api.Machine;
import li.cil.oc.api.driver.DriverItem;
import li.cil.oc.api.machine.Architecture;

import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import java.util.Set;

/** OpenComputers definitions, public item-driver universe, and architectures. */
public final class OpenComputersDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "opencomputers-domain-definitions"; }
    @Override public String categoryId() { return "opencomputers-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        if (API.driver == null || API.machine == null || API.items == null) {
            throw new IllegalStateException("OpenComputers public APIs are unavailable");
        }
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<String> namespaces = CompanionDefinitionSupport.namespaces("opencomputers");
        int blockCount = CompanionDefinitionSupport.captureOwnedBlocks(
            "opencomputers", namespaces, records, encoder
        );
        int itemCount = CompanionDefinitionSupport.captureOwnedItems(
            "opencomputers", namespaces, records, encoder
        );
        int itemDriverCount = captureItemDrivers(records);
        int architectureCount = captureArchitectures(records);
        if (blockCount == 0 || itemCount == 0 || itemDriverCount == 0
            || architectureCount == 0) {
            throw new IllegalStateException("OpenComputers definition universe is empty");
        }
        requireExactCounts(blockCount, itemCount, itemDriverCount, architectureCount);

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "opencomputers-definition-authority");
        authority.addProperty("owned_block_count", blockCount);
        authority.addProperty("owned_item_count", itemCount);
        authority.addProperty("public_item_driver_count", itemDriverCount);
        authority.addProperty("architecture_count", architectureCount);
        authority.addProperty("block_driver_universe_exposed_by_public_api", false);
        authority.addProperty("converter_universe_exposed_by_public_api", false);
        authority.addProperty("driver_matching_invoked", false);
        authority.addProperty("architecture_initialized", false);
        authority.addProperty("machine_or_network_created", false);
        authority.addProperty("filesystem_opened", false);
        authority.addProperty("world_or_inventory_captured", false);
        authority.addProperty("client_behavior_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(
        int blockCount,
        int itemCount,
        int itemDriverCount,
        int architectureCount
    ) {
        if (blockCount != 36 || itemCount != 45 || itemDriverCount != 59
            || architectureCount != 2) {
            throw new IllegalStateException(
                "OpenComputers definition counts drifted: blocks=" + blockCount
                    + ", items=" + itemCount
                    + ", item_drivers=" + itemDriverCount
                    + ", architectures=" + architectureCount
            );
        }
    }

    private static int captureItemDrivers(List<JsonObject> records) {
        List<DriverItem> drivers = new ArrayList<DriverItem>(Driver.itemDrivers());
        Collections.sort(drivers, new Comparator<DriverItem>() {
            @Override
            public int compare(DriverItem left, DriverItem right) {
                return left.getClass().getName().compareTo(right.getClass().getName());
            }
        });
        for (int ordinal = 0; ordinal < drivers.size(); ordinal++) {
            DriverItem driver = drivers.get(ordinal);
            if (driver == null) throw new IllegalStateException("null OpenComputers item driver");
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "opencomputers-item-driver-definition");
            row.addProperty("driver_ordinal", ordinal);
            row.addProperty("driver_runtime_class", driver.getClass().getName());
            row.add("class_hierarchy", CompanionDefinitionSupport.classHierarchy(driver.getClass()));
            row.addProperty("matching_or_environment_creation_invoked", false);
            records.add(row);
        }
        return drivers.size();
    }

    private static int captureArchitectures(List<JsonObject> records) {
        List<Class<? extends Architecture>> architectures =
            new ArrayList<Class<? extends Architecture>>(Machine.architectures());
        Collections.sort(architectures, new Comparator<Class<? extends Architecture>>() {
            @Override
            public int compare(
                Class<? extends Architecture> left,
                Class<? extends Architecture> right
            ) {
                return left.getName().compareTo(right.getName());
            }
        });
        for (Class<? extends Architecture> architecture : architectures) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "opencomputers-architecture-definition");
            row.addProperty("architecture_class", architecture.getName());
            row.addProperty("architecture_name", Machine.getArchitectureName(architecture));
            row.addProperty("architecture_initialized", false);
            records.add(row);
        }
        return architectures.size();
    }
}
