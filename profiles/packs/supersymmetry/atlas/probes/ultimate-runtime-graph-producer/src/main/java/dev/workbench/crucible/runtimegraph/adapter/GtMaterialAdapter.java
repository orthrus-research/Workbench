package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.GregTechAPI;
import gregtech.api.unification.material.MarkerMaterial;
import gregtech.api.unification.material.Material;
import gregtech.api.unification.material.info.MaterialFlag;
import gregtech.api.unification.material.info.MaterialFlags;
import gregtech.api.unification.material.properties.IMaterialProperty;
import gregtech.api.unification.material.properties.MaterialProperties;
import gregtech.api.unification.material.properties.PropertyKey;
import gregtech.api.unification.material.registry.IMaterialRegistryManager;
import gregtech.api.unification.material.registry.MaterialRegistry;
import gregtech.api.unification.stack.MaterialStack;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Frozen material identity, composition, property, flag, and element root. */
public final class GtMaterialAdapter implements CaptureAdapter {
    private static final Field MATERIAL_FLAGS = ReflectionAccess.requireField(
        Material.class, "flags", MaterialFlags.class
    );
    private static final Field FLAG_SET = ReflectionAccess.requireAssignableField(
        MaterialFlags.class, "flags", Set.class
    );
    private static final Field PROPERTY_MAP = ReflectionAccess.requireAssignableField(
        MaterialProperties.class, "propertyMap", Map.class
    );

    @Override public String adapterId() { return "gt-materials"; }
    @Override public String categoryId() { return "material-core"; }
    @Override public String checkpointId() { return CaptureCoordinator.MATERIAL_FROZEN; }

    @Override
    public AdapterSnapshot snapshot() {
        IMaterialRegistryManager manager = GregTechAPI.materialManager;
        if (manager == null || manager.getPhase() != IMaterialRegistryManager.Phase.FROZEN) {
            throw new IllegalStateException("GT material manager is not FROZEN");
        }
        List<MaterialRegistry> registries = new ArrayList<MaterialRegistry>(
            manager.getRegistries()
        );
        Collections.sort(registries, new Comparator<MaterialRegistry>() {
            @Override
            public int compare(MaterialRegistry left, MaterialRegistry right) {
                int mod = left.getModid().compareTo(right.getModid());
                return mod != 0 ? mod : Integer.compare(left.getNetworkId(), right.getNetworkId());
            }
        });
        if (registries.isEmpty()) throw new IllegalStateException("GT material registries are empty");

        Set<Material> persistentIdentity = Collections.newSetFromMap(
            new IdentityHashMap<Material, Boolean>()
        );
        Set<String> names = new HashSet<String>();
        List<Material> persistent = new ArrayList<Material>();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Set<Integer> networkIds = new HashSet<Integer>();
        Set<String> modIds = new HashSet<String>();
        for (MaterialRegistry registry : registries) {
            if (registry.getModid() == null || registry.getModid().isEmpty()
                || registry.getNetworkId() < 0 || !networkIds.add(registry.getNetworkId())
                || !modIds.add(registry.getModid())) {
                throw new IllegalStateException("GT material registry identity is invalid");
            }
            List<Material> materials = new ArrayList<Material>(registry.getAllMaterials());
            Collections.sort(materials, Comparator.comparing(Material::getRegistryName));
            JsonObject registryRow = new JsonObject();
            registryRow.addProperty("record_type", "gt-material-registry");
            registryRow.addProperty("registry_kind", "persistent");
            registryRow.addProperty("mod_id", registry.getModid());
            registryRow.addProperty("network_id", registry.getNetworkId());
            JsonArray registryMaterials = new JsonArray();
            for (Material material : materials) {
                validatePersistent(registry, material);
                if (!persistentIdentity.add(material) || !names.add(material.getRegistryName())) {
                    throw new IllegalStateException(
                        "duplicate persistent material " + material.getRegistryName()
                    );
                }
                persistent.add(material);
                registryMaterials.add(material.getRegistryName());
            }
            registryRow.add("materials", registryMaterials);
            records.add(registryRow);
        }
        Set<Material> managerUnion = Collections.newSetFromMap(
            new IdentityHashMap<Material, Boolean>()
        );
        managerUnion.addAll(manager.getRegisteredMaterials());
        if (!managerUnion.equals(persistentIdentity)) {
            throw new IllegalStateException("GT manager/registry material union differs");
        }

        List<Material> markers = new ArrayList<Material>();
        if (GregTechAPI.markerMaterialRegistry == null) {
            throw new IllegalStateException("GT marker material registry is unavailable");
        }
        for (MarkerMaterial marker : GregTechAPI.markerMaterialRegistry.getAll()) {
            if (marker == null || persistentIdentity.contains(marker)
                || !names.add(marker.getRegistryName())) {
                throw new IllegalStateException("GT marker material identity is invalid");
            }
            markers.add(marker);
        }
        Collections.sort(markers, Comparator.comparing(Material::getRegistryName));
        JsonObject markerRegistry = new JsonObject();
        markerRegistry.addProperty("record_type", "gt-material-registry");
        markerRegistry.addProperty("registry_kind", "marker");
        markerRegistry.add("mod_id", JsonNull.INSTANCE);
        markerRegistry.add("network_id", JsonNull.INSTANCE);
        JsonArray markerNames = new JsonArray();
        for (Material marker : markers) markerNames.add(marker.getRegistryName());
        markerRegistry.add("materials", markerNames);
        records.add(markerRegistry);

        List<Material> all = new ArrayList<Material>(persistent);
        all.addAll(markers);
        Collections.sort(all, Comparator.comparing(Material::getRegistryName));
        Set<Material> allIdentity = Collections.newSetFromMap(
            new IdentityHashMap<Material, Boolean>()
        );
        allIdentity.addAll(all);
        StableValueEncoder encoder = new StableValueEncoder();
        for (Material material : all) {
            records.add(material(material, markers.contains(material), allIdentity, encoder));
        }
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static void validatePersistent(MaterialRegistry registry, Material material) {
        if (material == null || material instanceof MarkerMaterial
            || material.getRegistry() != registry
            || material.getRegistryName() == null
            || material.getRegistryName().isEmpty()
            || material.getId() < 0) {
            throw new IllegalStateException("invalid persistent material in " + registry.getModid());
        }
    }

    @SuppressWarnings("unchecked")
    private static JsonObject material(
        Material material,
        boolean marker,
        Set<Material> all,
        StableValueEncoder encoder
    ) {
        JsonObject row = new JsonObject();
        row.addProperty("record_type", "gt-material");
        row.addProperty("registry_kind", marker ? "marker" : "persistent");
        row.addProperty("registry_name", material.getRegistryName());
        row.addProperty("namespace", material.getModid());
        row.addProperty("name", material.getName());
        row.addProperty("numeric_id", material.getId());
        row.addProperty("rgb", material.getMaterialRGB());
        row.addProperty("solid", material.isSolid());
        row.addProperty("has_fluid_property", material.hasProperty(PropertyKey.FLUID));
        if (material.getChemicalFormula() == null) row.add("chemical_formula", JsonNull.INSTANCE);
        else row.addProperty("chemical_formula", material.getChemicalFormula());
        row.add("icon_set", encoder.encode(material.getMaterialIconSet()));
        row.add("element", encoder.encode(material.getElement()));
        if (marker) {
            row.add("registry_mod_id", JsonNull.INSTANCE);
            row.add("registry_network_id", JsonNull.INSTANCE);
            row.add("mass", JsonNull.INSTANCE);
            row.add("protons", JsonNull.INSTANCE);
            row.add("neutrons", JsonNull.INSTANCE);
            row.add("radioactive", JsonNull.INSTANCE);
            row.add("blast_temperature", JsonNull.INSTANCE);
        } else {
            row.addProperty("registry_mod_id", material.getRegistry().getModid());
            row.addProperty("registry_network_id", material.getRegistry().getNetworkId());
            row.addProperty("mass", material.getMass());
            row.addProperty("protons", material.getProtons());
            row.addProperty("neutrons", material.getNeutrons());
            row.addProperty("radioactive", material.isRadioactive());
            row.addProperty("blast_temperature", material.getBlastTemperature());
        }

        JsonArray components = new JsonArray();
        Collection<MaterialStack> rawComponents = material.getMaterialComponents();
        if (rawComponents != null) {
            int ordinal = 0;
            for (MaterialStack component : rawComponents) {
                if (component == null || component.material == null
                    || !all.contains(component.material)) {
                    throw new IllegalStateException(
                        "material component leaves the frozen material universe: "
                            + material.getRegistryName()
                    );
                }
                JsonObject value = new JsonObject();
                value.addProperty("ordinal", ordinal++);
                value.addProperty("material_registry_name", component.material.getRegistryName());
                value.addProperty("amount", component.amount);
                components.add(value);
            }
        }
        row.add("components", components);

        MaterialFlags flags = (MaterialFlags) ReflectionAccess.read(MATERIAL_FLAGS, material);
        Set<MaterialFlag> flagSet = (Set<MaterialFlag>) ReflectionAccess.read(FLAG_SET, flags);
        List<String> flagNames = new ArrayList<String>();
        for (MaterialFlag flag : flagSet) {
            if (flag == null) throw new IllegalStateException("material flag set contains null");
            flagNames.add(flag.toString());
        }
        Collections.sort(flagNames);
        JsonArray flagRows = new JsonArray();
        for (String flag : flagNames) flagRows.add(flag);
        row.add("flags", flagRows);

        Map<PropertyKey<?>, IMaterialProperty> propertyMap =
            (Map<PropertyKey<?>, IMaterialProperty>) ReflectionAccess.read(
                PROPERTY_MAP, material.getProperties()
            );
        List<Map.Entry<PropertyKey<?>, IMaterialProperty>> properties =
            new ArrayList<Map.Entry<PropertyKey<?>, IMaterialProperty>>(propertyMap.entrySet());
        Collections.sort(properties, new Comparator<Map.Entry<PropertyKey<?>, IMaterialProperty>>() {
            @Override
            public int compare(
                Map.Entry<PropertyKey<?>, IMaterialProperty> left,
                Map.Entry<PropertyKey<?>, IMaterialProperty> right
            ) {
                return left.getKey().toString().compareTo(right.getKey().toString());
            }
        });
        JsonArray propertyRows = new JsonArray();
        Set<String> propertyNames = new HashSet<String>();
        for (Map.Entry<PropertyKey<?>, IMaterialProperty> property : properties) {
            if (property.getKey() == null
                || !propertyNames.add(property.getKey().toString())) {
                throw new IllegalStateException("material property map is invalid");
            }
            JsonObject value = new JsonObject();
            value.addProperty("key", property.getKey().toString());
            if (property.getValue() == null) {
                // GTCEu's EMPTY key can legitimately retain a null placeholder:
                // its private EmptyProperty cannot be reflectively constructed.
                value.add("runtime_class", JsonNull.INSTANCE);
                value.addProperty("value_projection", "null-placeholder");
                value.add("value", JsonNull.INSTANCE);
            } else if (property.getKey().equals(PropertyKey.FLUID)) {
                value.addProperty("runtime_class", property.getValue().getClass().getName());
                value.addProperty("value_projection", "material-fluid-relations-category");
            } else {
                value.addProperty("runtime_class", property.getValue().getClass().getName());
                value.addProperty("value_projection", "structural-value");
                value.add("value", encoder.encode(property.getValue()));
            }
            propertyRows.add(value);
        }
        row.add("properties", propertyRows);
        return row;
    }
}
