package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;

import net.minecraft.block.Block;
import net.minecraftforge.fluids.Fluid;
import net.minecraftforge.fluids.FluidRegistry;

import com.google.common.collect.BiMap;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Complete final Forge fluid registry with public physical properties. */
public final class ForgeFluidAdapter implements CaptureAdapter {
    private static final Field MASTER_FLUID_REFERENCE = ReflectionAccess.requireAssignableField(
        FluidRegistry.class, "masterFluidReference", BiMap.class
    );
    private static final Field DEFAULT_FLUID_NAME = ReflectionAccess.requireAssignableField(
        FluidRegistry.class, "defaultFluidName", BiMap.class
    );
    private static final Field FLUID_IDS = ReflectionAccess.requireAssignableField(
        FluidRegistry.class, "fluidIDs", BiMap.class
    );

    @Override public String adapterId() { return "forge-fluids"; }
    @Override public String categoryId() { return "fluid-registry"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        Map<String, Fluid> registered = new LinkedHashMap<String, Fluid>(
            FluidRegistry.getRegisteredFluids()
        );
        List<String> names = new ArrayList<String>(registered.keySet());
        Collections.sort(names);
        if (names.isEmpty()) throw new IllegalStateException("Forge fluid registry is empty");
        Map<String, Fluid> master = masterFluidReference();
        Map<String, String> defaults = defaultFluidNames();
        Map<Fluid, Integer> ids = fluidIds();
        List<JsonObject> records = new ArrayList<JsonObject>();
        int selectedLocalDefaultCount = 0;
        int selectedNondefaultCount = 0;
        for (String name : names) {
            Fluid fluid = registered.get(name);
            if (fluid == null || !name.equals(fluid.getName())
                || FluidRegistry.getFluid(name) != fluid) {
                throw new IllegalStateException("Forge fluid round trip failed: " + name);
            }
            String selectedRegistrationName = FluidRegistry.getDefaultFluidName(fluid);
            if (selectedRegistrationName == null
                || selectedRegistrationName.indexOf(':') <= 0
                || master.get(selectedRegistrationName) != fluid) {
                throw new IllegalStateException(
                    "Forge selected fluid has no owned registration name: " + name
                );
            }
            String localDefaultRegistrationName = defaults.get(name);
            Fluid localDefault = master.get(localDefaultRegistrationName);
            if (localDefaultRegistrationName == null || localDefault == null
                || !name.equals(localDefault.getName())) {
                throw new IllegalStateException(
                    "Forge fluid has no local default registration: " + name
                );
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "forge-fluid-entry");
            row.addProperty("name", name);
            row.addProperty(
                "local_default_registration_name",
                localDefaultRegistrationName
            );
            row.addProperty("selected_registration_name", selectedRegistrationName);
            row.addProperty(
                "selected_owner_mod_id",
                selectedRegistrationName.substring(0, selectedRegistrationName.indexOf(':'))
            );
            Integer numericId = ids.get(fluid);
            if (numericId == null) row.add("numeric_id", JsonNull.INSTANCE);
            else row.addProperty("numeric_id", numericId.intValue());
            boolean selectedLocalDefault = fluid == localDefault;
            if (selectedLocalDefault) {
                selectedLocalDefaultCount++;
                row.addProperty("selection_state", "selected-local-default");
            } else {
                selectedNondefaultCount++;
                row.addProperty("selection_state", "selected-nondefault");
            }
            row.add(
                "registration_alternatives",
                alternatives(
                    name,
                    fluid,
                    master,
                    localDefaultRegistrationName
                )
            );
            row.addProperty("runtime_class", fluid.getClass().getName());
            row.addProperty("density", fluid.getDensity());
            row.addProperty("gaseous", fluid.isGaseous());
            row.addProperty("lighter_than_air", fluid.isLighterThanAir());
            row.addProperty("luminosity", fluid.getLuminosity());
            row.addProperty("temperature", fluid.getTemperature());
            row.addProperty("viscosity", fluid.getViscosity());
            row.addProperty("color_argb", fluid.getColor());
            row.addProperty("rarity", fluid.getRarity().name());
            row.addProperty("can_be_placed_in_world", fluid.canBePlacedInWorld());
            row.addProperty("has_bucket", FluidRegistry.hasBucket(fluid));
            resource(row, "still_texture", fluid.getStill());
            resource(row, "flowing_texture", fluid.getFlowing());
            resource(row, "overlay_texture", fluid.getOverlay());
            Block block = fluid.getBlock();
            resource(row, "block", block == null ? null : block.getRegistryName());
            records.add(row);
        }
        int accounted = 0;
        for (Map.Entry<String, Fluid> entry : master.entrySet()) {
            Fluid selected = registered.get(entry.getValue().getName());
            if (selected == null) {
                throw new IllegalStateException(
                    "Forge master fluid alternative has no selected name: " + entry.getKey()
                );
            }
            accounted++;
        }
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "forge-fluid-selection-authority");
        authority.addProperty("selected_fluid_count", registered.size());
        authority.addProperty("owned_alternative_count", accounted);
        authority.addProperty("local_default_binding_count", defaults.size());
        authority.addProperty("selected_local_default_count", selectedLocalDefaultCount);
        authority.addProperty("selected_nondefault_count", selectedNondefaultCount);
        authority.addProperty("selection_source", "FluidRegistry.fluids");
        authority.addProperty("local_default_source", "FluidRegistry.defaultFluidName");
        authority.addProperty("alternative_source", "FluidRegistry.masterFluidReference");
        records.add(authority);
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }

    private static JsonArray alternatives(
        String name,
        Fluid selected,
        Map<String, Fluid> master,
        String localDefaultRegistrationName
    ) {
        List<Map.Entry<String, Fluid>> candidates =
            new ArrayList<Map.Entry<String, Fluid>>();
        for (Map.Entry<String, Fluid> entry : master.entrySet()) {
            if (name.equals(entry.getValue().getName())) candidates.add(entry);
        }
        Collections.sort(candidates, new Comparator<Map.Entry<String, Fluid>>() {
            @Override
            public int compare(
                Map.Entry<String, Fluid> left,
                Map.Entry<String, Fluid> right
            ) {
                return left.getKey().compareTo(right.getKey());
            }
        });
        if (candidates.isEmpty()) {
            throw new IllegalStateException("Forge fluid has no owned alternative: " + name);
        }
        JsonArray rows = new JsonArray();
        int selectedCount = 0;
        for (int ordinal = 0; ordinal < candidates.size(); ordinal++) {
            Map.Entry<String, Fluid> candidate = candidates.get(ordinal);
            boolean isSelected = candidate.getValue() == selected;
            if (isSelected) selectedCount++;
            int separator = candidate.getKey().indexOf(':');
            if (separator <= 0) {
                throw new IllegalStateException("Forge fluid alternative has no owner: " + name);
            }
            JsonObject alternative = new JsonObject();
            alternative.addProperty("alternative_ordinal", ordinal);
            alternative.addProperty("owned_registration_name", candidate.getKey());
            alternative.addProperty("owner_mod_id", candidate.getKey().substring(0, separator));
            alternative.addProperty("runtime_class", candidate.getValue().getClass().getName());
            boolean isLocalDefault = candidate.getKey().equals(
                localDefaultRegistrationName
            );
            alternative.addProperty("local_default", isLocalDefault);
            alternative.addProperty("selected", isSelected);
            JsonArray roles = new JsonArray();
            if (isLocalDefault) roles.add("local-default");
            if (isSelected) roles.add("selected-default");
            if (!isLocalDefault && !isSelected) roles.add("owner-alternative");
            alternative.add("selection_roles", roles);
            rows.add(alternative);
        }
        if (selectedCount != 1) {
            throw new IllegalStateException("Forge fluid selection is not unique: " + name);
        }
        return rows;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Fluid> masterFluidReference() {
        return new LinkedHashMap<String, Fluid>(
            (Map<String, Fluid>) ReflectionAccess.read(MASTER_FLUID_REFERENCE, null)
        );
    }

    @SuppressWarnings("unchecked")
    private static Map<String, String> defaultFluidNames() {
        return new LinkedHashMap<String, String>(
            (Map<String, String>) ReflectionAccess.read(DEFAULT_FLUID_NAME, null)
        );
    }

    @SuppressWarnings("unchecked")
    private static Map<Fluid, Integer> fluidIds() {
        return new LinkedHashMap<Fluid, Integer>(
            (Map<Fluid, Integer>) ReflectionAccess.read(FLUID_IDS, null)
        );
    }

    private static void resource(
        JsonObject row,
        String name,
        net.minecraft.util.ResourceLocation value
    ) {
        if (value == null) row.add(name, JsonNull.INSTANCE);
        else row.addProperty(name, value.toString());
    }
}
