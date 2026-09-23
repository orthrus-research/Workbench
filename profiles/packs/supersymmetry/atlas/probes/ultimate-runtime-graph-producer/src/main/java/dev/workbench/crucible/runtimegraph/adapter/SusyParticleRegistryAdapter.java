package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.api.particle.Particle;
import supersymmetry.api.particle.Particles;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** SuSy particle registry with intrinsic physics and explicit relationships. */
public final class SusyParticleRegistryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "susy-particle-registry"; }
    @Override public String categoryId() { return "susy-particles"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        Map<String, Particle> registry = Particles.particleRegistry;
        if (registry == null || registry.isEmpty()) {
            throw new IllegalStateException("SuSy particle registry is unavailable");
        }
        for (Map.Entry<String, Particle> entry : registry.entrySet()) {
            Particle particle = entry.getValue();
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-particle");
            row.addProperty("registry_name", entry.getKey());
            row.addProperty("name", particle.getName());
            row.addProperty("registry_name_matches", entry.getKey().equals(particle.getName()));
            row.add("mass", encoder.encode(Double.valueOf(particle.getMass())));
            row.add("charge", encoder.encode(Double.valueOf(particle.getCharge())));
            row.add("spin", encoder.encode(Double.valueOf(particle.getSpin())));
            row.add("width", encoder.encode(Double.valueOf(particle.getWidth())));
            row.add("mean_lifetime", encoder.encode(Double.valueOf(particle.getMeanLifetime())));
            row.addProperty("coloured", particle.isColoured());
            row.addProperty("weak_interaction", particle.isWeakInt());
            row.addProperty("electromagnetic_interaction", particle.hasEMInteraction());
            row.addProperty("fundamental", particle.isFundamental());
            row.addProperty("texture", particle.getTexture().toString());
            Particle anti = particle.getAntiParticle();
            if (anti == null) row.add("anti_particle", com.google.gson.JsonNull.INSTANCE);
            else row.addProperty("anti_particle", anti.getName());
            JsonArray components = new JsonArray();
            for (Map.Entry<Particle, Integer> component : particle.getComponents().entrySet()) {
                JsonObject value = new JsonObject();
                value.addProperty("particle", component.getKey().getName());
                value.addProperty("amount", component.getValue());
                components.add(value);
            }
            row.add("components", components);
            records.add(row);
        }
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }
}
