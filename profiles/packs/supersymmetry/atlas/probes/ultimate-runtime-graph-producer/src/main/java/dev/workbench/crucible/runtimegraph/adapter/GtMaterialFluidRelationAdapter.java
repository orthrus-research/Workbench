package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;

import gregtech.api.GregTechAPI;
import gregtech.api.fluids.store.FluidStorageImpl;
import gregtech.api.fluids.store.FluidStorageKey;
import gregtech.api.unification.FluidUnifier;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.properties.FluidProperty;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.material.registry.IMaterialRegistryManager;
import gregtech.api.unification.material.registry.MaterialRegistry;

import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Actual frozen GT storage-key to Forge-fluid relationships. */
public final class GtMaterialFluidRelationAdapter implements CaptureAdapter {
    private static final Field FLUID_STORAGE = ReflectionAccess.requireField(
        FluidProperty.class, "storage", FluidStorageImpl.class
    );
    private static final Field FLUID_MAP = ReflectionAccess.requireAssignableField(
        FluidStorageImpl.class, "map", Map.class
    );

    @Override public String adapterId() { return "gt-material-fluid-relations"; }
    @Override public String categoryId() { return "material-fluid-relations"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    @SuppressWarnings("unchecked")
    public AdapterSnapshot snapshot() {
        IMaterialRegistryManager manager = GregTechAPI.materialManager;
        if (manager == null || manager.getPhase() != IMaterialRegistryManager.Phase.FROZEN) {
            throw new IllegalStateException("GT material manager is not FROZEN");
        }
        List<Material> materials = persistentMaterials(manager.getRegistries());
        List<JsonObject> records = new ArrayList<JsonObject>();
        int relationshipCount = 0;
        for (Material material : materials) {
            if (!material.hasProperty(PropertyKey.FLUID)) continue;
            FluidProperty property = material.getProperty(PropertyKey.FLUID);
            if (property == null) {
                throw new IllegalStateException("material declares FLUID without a property value");
            }
            FluidStorageImpl storage = (FluidStorageImpl) ReflectionAccess.read(
                FLUID_STORAGE, property
            );
            Map<FluidStorageKey, Fluid> values = (Map<FluidStorageKey, Fluid>)
                ReflectionAccess.read(FLUID_MAP, storage);
            List<Map.Entry<FluidStorageKey, Fluid>> relations =
                new ArrayList<Map.Entry<FluidStorageKey, Fluid>>(values.entrySet());
            Collections.sort(relations, new Comparator<Map.Entry<FluidStorageKey, Fluid>>() {
                @Override
                public int compare(
                    Map.Entry<FluidStorageKey, Fluid> left,
                    Map.Entry<FluidStorageKey, Fluid> right
                ) {
                    return left.getKey().getResourceLocation().compareTo(
                        right.getKey().getResourceLocation()
                    );
                }
            });
            for (Map.Entry<FluidStorageKey, Fluid> relation : relations) {
                FluidStorageKey key = relation.getKey();
                Fluid fluid = relation.getValue();
                if (key == null || fluid == null || storage.get(key) != fluid
                    || FluidRegistry.getFluid(fluid.getName()) != fluid) {
                    throw new IllegalStateException(
                        "material fluid relationship does not round trip: "
                            + material.getRegistryName()
                    );
                }
                String defaultName = FluidRegistry.getDefaultFluidName(fluid);
                if (defaultName == null || defaultName.indexOf(':') <= 0) {
                    throw new IllegalStateException("material fluid lacks owned Forge identity");
                }
                Material reverse = FluidUnifier.getMaterialFromFluid(fluid);
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "material-fluid-relation");
                row.addProperty("material", material.getRegistryName());
                row.addProperty("storage_key", key.getResourceLocation().toString());
                row.addProperty("storage_key_priority", key.getRegistrationPriority());
                if (key.getDefaultFluidState() == null) {
                    row.add("default_fluid_state", com.google.gson.JsonNull.INSTANCE);
                } else {
                    row.addProperty("default_fluid_state", key.getDefaultFluidState().name());
                }
                row.addProperty("fluid_name", fluid.getName());
                row.addProperty("default_registration_name", defaultName);
                row.addProperty("owner_mod_id", defaultName.substring(0, defaultName.indexOf(':')));
                row.addProperty("primary_storage_key", key.equals(property.getPrimaryKey()));
                row.addProperty(
                    "reverse_material",
                    reverse == null ? "" : reverse.getRegistryName()
                );
                row.addProperty("reverse_consistent", reverse == null || reverse == material);
                records.add(row);
                relationshipCount++;
            }
        }
        JsonObject summary = new JsonObject();
        summary.addProperty("record_type", "material-fluid-summary");
        summary.addProperty("enumerated_material_count", materials.size());
        summary.addProperty("relationship_count", relationshipCount);
        summary.addProperty("registered_forge_fluid_count", FluidRegistry.getRegisteredFluids().size());
        records.add(summary);
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }

    private static List<Material> persistentMaterials(Collection<MaterialRegistry> registries) {
        List<Material> result = new ArrayList<Material>();
        Set<String> names = new HashSet<String>();
        for (MaterialRegistry registry : registries) {
            for (Material material : registry.getAllMaterials()) {
                if (material == null || !names.add(material.getRegistryName())) {
                    throw new IllegalStateException("persistent material universe is invalid");
                }
                result.add(material);
            }
        }
        Collections.sort(result, Comparator.comparing(Material::getRegistryName));
        return result;
    }
}
