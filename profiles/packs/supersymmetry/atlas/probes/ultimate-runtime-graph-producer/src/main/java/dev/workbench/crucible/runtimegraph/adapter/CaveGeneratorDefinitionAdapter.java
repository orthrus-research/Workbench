package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.personthecat.cavegenerator.Main;
import com.personthecat.cavegenerator.config.CavePreset;
import com.personthecat.cavegenerator.config.ConfigFile;
import com.personthecat.cavegenerator.world.GeneratorController;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.JsonValueNormalization;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.gen.structure.template.Template;
import org.hjson.Stringify;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Expanded Cave Generator presets and templates without creating or executing generators. */
public final class CaveGeneratorDefinitionAdapter implements CaptureAdapter {
    private static final String[] CONTROLLER_LIST_FIELDS = {
        "tunnels", "ravines", "caverns", "deferredCaverns", "burrows", "layers",
        "cavernTunnels", "burrowTunnels", "stalactites", "pillars", "structures"
    };

    @Override public String adapterId() { return "cave-generator-world-definitions"; }
    @Override public String categoryId() { return "cave-generator-world-domain"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Main main = Main.instance;
        if (main == null) throw new IllegalStateException("Cave Generator instance unavailable");
        int presetCount = capturePresets(main.presets, records);
        int templateCount = captureTemplates(main.structures, records, encoder);
        int controllerCount = captureControllers(main.generators, records, encoder);
        int configurationCount = captureConfiguration(records, encoder);
        if (presetCount == 0 || templateCount == 0 || configurationCount == 0) {
            throw new IllegalStateException("Cave Generator definition universe is empty");
        }
        requireExactCounts(presetCount, templateCount, controllerCount, configurationCount);
        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "cave-generator-definition-authority");
        authority.addProperty("preset_count", presetCount);
        authority.addProperty("template_count", templateCount);
        authority.addProperty("world_bound_controller_count", controllerCount);
        authority.addProperty("configuration_value_count", configurationCount);
        authority.addProperty(
            "controller_binding_state", controllerCount == 0 ? "unmaterialized" : "materialized"
        );
        authority.addProperty("expanded_runtime_preset_captured", true);
        authority.addProperty("load_generators_invoked", false);
        authority.addProperty("early_generate_invoked", false);
        authority.addProperty("map_generate_invoked", false);
        authority.addProperty("feature_generate_invoked", false);
        authority.addProperty("template_placement_invoked", false);
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static void requireExactCounts(
        int presetCount,
        int templateCount,
        int controllerCount,
        int configurationCount
    ) {
        if (presetCount != 25 || templateCount != 10
            || (controllerCount != 0 && controllerCount != 18)
            || configurationCount != 14) {
            throw new IllegalStateException(
                "Cave Generator definition counts drifted: presets=" + presetCount
                    + ", templates=" + templateCount + ", controllers=" + controllerCount
                    + ", configuration_values=" + configurationCount
            );
        }
    }

    private static int capturePresets(
        Map<String, CavePreset> presets,
        List<JsonObject> records
    ) {
        List<String> ids = new ArrayList<String>(presets.keySet());
        Collections.sort(ids);
        for (String id : ids) {
            CavePreset preset = presets.get(id);
            if (preset == null || preset.raw == null) {
                throw new IllegalStateException("incomplete Cave Generator preset " + id);
            }
            JsonElement definition = JsonValueNormalization.typedDecimals(
                new JsonParser().parse(preset.raw.toString(Stringify.PLAIN))
            );
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "cave-generator-preset-definition");
            row.addProperty("preset_id", id);
            row.addProperty("enabled", preset.enabled);
            row.addProperty("tunnel_count", preset.tunnels.size());
            row.addProperty("ravine_count", preset.ravines.size());
            row.addProperty("cavern_count", preset.caverns.size());
            row.addProperty("burrow_count", preset.burrows.size());
            row.addProperty("layer_count", preset.layers.size());
            row.addProperty("cluster_count", preset.clusters.size());
            row.addProperty("stalactite_count", preset.stalactites.size());
            row.addProperty("pillar_count", preset.pillars.size());
            row.addProperty("structure_count", preset.structures.size());
            row.add("expanded_definition", definition);
            row.addProperty("definition_sha256", CanonicalJson.sha256(definition));
            row.addProperty("generation_invoked", false);
            records.add(row);
        }
        return ids.size();
    }

    private static int captureTemplates(
        Map<String, Template> templates,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<String> ids = new ArrayList<String>(templates.keySet());
        Collections.sort(ids);
        for (String id : ids) {
            Template template = templates.get(id);
            if (template == null) throw new IllegalStateException("missing cave template " + id);
            NBTTagCompound definition = template.writeToNBT(new NBTTagCompound());
            BlockPos size = template.getSize();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "cave-generator-template-definition");
            row.addProperty("template_id", id);
            row.addProperty("author", template.getAuthor());
            row.addProperty("size_x", size.getX());
            row.addProperty("size_y", size.getY());
            row.addProperty("size_z", size.getZ());
            row.add("definition", encoder.encode(definition));
            row.addProperty("placement_invoked", false);
            records.add(row);
        }
        return ids.size();
    }

    @SuppressWarnings("unchecked")
    private static int captureControllers(
        Map<String, GeneratorController> controllers,
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        List<String> ids = new ArrayList<String>(controllers.keySet());
        Collections.sort(ids);
        for (String id : ids) {
            GeneratorController controller = controllers.get(id);
            if (controller == null) throw new IllegalStateException("missing cave controller " + id);
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "cave-generator-controller-binding");
            row.addProperty("controller_id", id);
            for (String fieldName : CONTROLLER_LIST_FIELDS) {
                Field field = ReflectionAccess.requireAssignableField(
                    GeneratorController.class, fieldName, List.class
                );
                List<Object> values = (List<Object>) ReflectionAccess.read(field, controller);
                row.addProperty(fieldName + "_count", values.size());
            }
            row.addProperty("generation_invoked", false);
            records.add(row);
        }
        return ids.size();
    }

    private static int captureConfiguration(
        List<JsonObject> records,
        StableValueEncoder encoder
    ) {
        Map<String, Object> values = new LinkedHashMap<String, Object>();
        values.put("autoFormat", ConfigFile.autoFormat);
        values.put("autoGenerate", ConfigFile.autoGenerate);
        values.put("biomeRange", ConfigFile.biomeRange);
        values.put("enableLavaLakes", ConfigFile.enableLavaLakes);
        values.put("enableMineshafts", ConfigFile.enableMineshafts);
        values.put("enableVanillaStoneClusters", ConfigFile.enableVanillaStoneClusters);
        values.put("enableWaterLakes", ConfigFile.enableWaterLakes);
        values.put("heightMapDims", ConfigFile.heightMapDims);
        values.put("ignoreInvalidPresets", ConfigFile.ignoreInvalidPresets);
        values.put("mapRange", ConfigFile.mapRange);
        values.put("netherGenerate", ConfigFile.netherGenerate);
        values.put("otherGeneratorEnabled", ConfigFile.otherGeneratorEnabled);
        values.put("strictPresets", ConfigFile.strictPresets);
        values.put("updateImports", ConfigFile.updateImports);
        for (Map.Entry<String, Object> entry : values.entrySet()) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "cave-generator-configuration-value");
            row.addProperty("configuration_id", entry.getKey());
            row.add("value", encoder.encode(entry.getValue()));
            records.add(row);
        }
        return values.size();
    }
}
