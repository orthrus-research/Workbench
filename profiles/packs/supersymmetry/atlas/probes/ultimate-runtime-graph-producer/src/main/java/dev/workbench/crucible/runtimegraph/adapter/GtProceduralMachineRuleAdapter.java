package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.RuntimeMethodSurface;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import gregtech.api.capability.IControllable;
import gregtech.api.capability.IMiner;
import gregtech.api.capability.IWorkable;
import gregtech.api.capability.impl.AbstractRecipeLogic;
import gregtech.api.metatileentity.IMachineHatchMultiblock;
import gregtech.api.metatileentity.MetaTileEntity;
import gregtech.api.metatileentity.multiblock.MultiblockControllerBase;
import gregtech.api.recipes.RecipeMap;

import net.minecraft.item.ItemStack;
import net.minecraft.util.ResourceLocation;
import net.minecraftforge.fluids.FluidStack;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/** Runtime-derived machine behavior classification and unresolved executable boundaries. */
public final class GtProceduralMachineRuleAdapter implements CaptureAdapter {
    private static final String PROCESSING_ARRAY =
        "gregtech.common.metatileentities.multi.electric.MetaTileEntityProcessingArray";
    private static final String[] LOGIC_HOOKS = new String[] {
        "checkRecipe",
        "isRecipeMapValid",
        "prepareRecipe",
        "setupAndConsumeRecipeInputs",
        "calculateOverclock",
        "modifyOverclockPre",
        "modifyOverclockPost",
        "setupRecipe",
        "completeRecipe",
        "outputRecipeOutputs",
        "findRecipe"
    };

    @Override public String adapterId() { return "gt-procedural-machine-rules"; }
    @Override public String categoryId() { return "machine-behavior-classification"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (GtMteCapture.Entry entry : GtMteCapture.entries()) {
            records.add(classify(entry, encoder));
        }
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static JsonObject classify(
        GtMteCapture.Entry entry,
        StableValueEncoder encoder
    ) {
        MetaTileEntity mte = entry.mte;
        AbstractRecipeLogic logic = entry.logic;
        RecipeMap<?> map = logic == null ? null : logic.getRecipeMap();
        JsonArray logicHooks = logic == null
            ? new JsonArray()
            : RuntimeMethodSurface.declaredHooks(logic, AbstractRecipeLogic.class, LOGIC_HOOKS);

        JsonObject row = new JsonObject();
        row.addProperty("record_type", "gt-procedural-machine-classification");
        row.addProperty("machine", entry.key.toString());
        row.addProperty("numeric_id", entry.numericId);
        row.addProperty("prototype_class", mte.getClass().getName());
        if (logic == null) row.add("recipe_logic_class", JsonNull.INSTANCE);
        else row.addProperty("recipe_logic_class", logic.getClass().getName());
        if (map == null) row.add("recipe_map", JsonNull.INSTANCE);
        else row.addProperty("recipe_map", map.getUnlocalizedName());
        row.addProperty("miner_capability", mte instanceof IMiner);
        row.addProperty("workable_capability", mte instanceof IWorkable);
        row.addProperty("controllable_capability", mte instanceof IControllable);
        row.addProperty("multiblock_controller", mte instanceof MultiblockControllerBase);
        row.addProperty("classification", classification(mte, logic, map, logicHooks));
        row.addProperty("world_or_inventory_context_invoked", false);
        row.addProperty(
            "execution_boundary",
            "registered prototype method owners and initial configuration only; no recipe, world, inventory, or structure execution"
        );
        row.add("logic_override_hooks", logicHooks);
        row.add("prototype_configuration", configurationFields(mte, MetaTileEntity.class, encoder));
        if (logic == null) {
            row.add("logic_configuration", new JsonArray());
        } else {
            row.add("logic_configuration", configurationFields(
                logic, AbstractRecipeLogic.class, encoder
            ));
            JsonObject defaults = new JsonObject();
            defaults.addProperty("consumes_energy", logic.consumesEnergy());
            defaults.addProperty("parallel_limit", logic.getParallelLimit());
            defaults.add(
                "eut_discount",
                encoder.encode(Double.valueOf(logic.getEUtDiscount()))
            );
            defaults.add(
                "speed_bonus",
                encoder.encode(Double.valueOf(logic.getSpeedBonus()))
            );
            defaults.addProperty("allow_overclocking", logic.isAllowOverclocking());
            defaults.addProperty(
                "maximum_overclock_voltage",
                logic.getMaximumOverclockVoltage()
            );
            row.add("logic_initial_settings", defaults);
        }
        if (mte instanceof IMachineHatchMultiblock) {
            IMachineHatchMultiblock controller = (IMachineHatchMultiblock) mte;
            String[] blacklist = controller.getBlacklist();
            if (blacklist == null) {
                throw new IllegalStateException("machine-hatch controller blacklist is null");
            }
            List<String> values = new ArrayList<String>();
            Collections.addAll(values, blacklist);
            Collections.sort(values);
            JsonObject delegation = new JsonObject();
            delegation.addProperty("rule_kind", "installed-machine-recipe-map-delegation");
            delegation.addProperty("machine_limit", controller.getMachineLimit());
            delegation.addProperty("parallel_formula", "min(installed_stack_count,machine_limit)");
            delegation.addProperty("recipe_map_selection", "installed_machine.getRecipeMap()");
            JsonArray blacklistRows = new JsonArray();
            for (String value : values) blacklistRows.add(value);
            delegation.add("recipe_map_blacklist", blacklistRows);
            row.add("machine_hatch_delegation", delegation);
        } else {
            row.add("machine_hatch_delegation", JsonNull.INSTANCE);
        }
        return row;
    }

    private static String classification(
        MetaTileEntity mte,
        AbstractRecipeLogic logic,
        RecipeMap<?> map,
        JsonArray logicHooks
    ) {
        if (PROCESSING_ARRAY.equals(mte.getClass().getName())) {
            return "procedural-installed-machine-delegation";
        }
        if (logic == null) {
            if (mte instanceof IMiner || mte instanceof IWorkable) {
                return "procedural-capability-without-abstract-recipe-logic";
            }
            return "infrastructure-no-recipe-logic";
        }
        if (map == null) return "procedural-recipe-logic-without-fixed-map";
        if (logicHooks.size() > 0) return "mapped-with-specialized-runtime-hooks";
        return "mapped-with-base-runtime-envelope";
    }

    private static JsonArray configurationFields(
        Object value,
        Class<?> stopExclusive,
        StableValueEncoder encoder
    ) {
        List<Field> fields = new ArrayList<Field>();
        List<JsonObject> boundaries = new ArrayList<JsonObject>();
        for (Class<?> current = value.getClass(); current != null && current != stopExclusive;
             current = current.getSuperclass()) {
            Field[] declared;
            try {
                declared = current.getDeclaredFields();
            } catch (LinkageError sideIncompatible) {
                JsonObject boundary = new JsonObject();
                boundary.addProperty("declaring_class", current.getName());
                boundary.addProperty("projection_kind", "side-incompatible-field-schema");
                boundary.addProperty("field_values_captured", false);
                boundaries.add(boundary);
                continue;
            }
            for (Field field : declared) {
                if (field.isSynthetic() || Modifier.isStatic(field.getModifiers())) continue;
                field.setAccessible(true);
                fields.add(field);
            }
        }
        Collections.sort(fields, new Comparator<Field>() {
            @Override
            public int compare(Field left, Field right) {
                int owner = left.getDeclaringClass().getName().compareTo(
                    right.getDeclaringClass().getName()
                );
                return owner != 0 ? owner : left.getName().compareTo(right.getName());
            }
        });
        JsonArray result = new JsonArray();
        for (JsonObject boundary : boundaries) result.add(boundary);
        for (Field field : fields) {
            Object fieldValue;
            try {
                fieldValue = field.get(value);
            } catch (IllegalAccessException exception) {
                throw new IllegalStateException("cannot read machine configuration field", exception);
            }
            JsonObject row = new JsonObject();
            row.addProperty("declaring_class", field.getDeclaringClass().getName());
            row.addProperty("name", field.getName());
            row.addProperty("declared_type", field.getType().getName());
            row.addProperty("final", Modifier.isFinal(field.getModifiers()));
            if (fieldValue instanceof RecipeMap<?>) {
                row.addProperty("projection_kind", "recipe-map-reference");
                row.addProperty(
                    "value",
                    ((RecipeMap<?>) fieldValue).getUnlocalizedName()
                );
            } else if (safeScalar(fieldValue)) {
                row.addProperty("projection_kind", "structural-value");
                row.add("value", encoder.encode(fieldValue));
            } else if (fieldValue == null) {
                row.addProperty("projection_kind", "null");
                row.add("value", JsonNull.INSTANCE);
            } else {
                row.addProperty("projection_kind", "opaque-runtime-reference");
                JsonObject reference = new JsonObject();
                reference.addProperty(
                    "runtime_class",
                    StableValueEncoder.stableClassName(fieldValue.getClass())
                );
                reference.addProperty("value_body_captured", false);
                row.add("value", reference);
            }
            result.add(row);
        }
        return result;
    }

    private static boolean safeScalar(Object value) {
        return value instanceof String || value instanceof Character
            || value instanceof Boolean || value instanceof Number
            || value instanceof Enum<?> || value instanceof ResourceLocation
            || value instanceof ItemStack || value instanceof FluidStack
            || value instanceof Class<?>;
    }
}
