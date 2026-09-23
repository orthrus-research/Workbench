package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.PackCoreReflection;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import supersymmetry.api.space.CelestialObject;
import supersymmetry.api.space.CelestialObjects;
import supersymmetry.api.space.Galaxy;
import supersymmetry.api.space.Planetoid;
import supersymmetry.api.space.Star;

import java.lang.reflect.Field;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** SuSy celestial hierarchy and dimension-bearing planetoid registry. */
public final class SusyCelestialRegistryAdapter implements CaptureAdapter {
    private static final Field MASS = scalar("mass");
    private static final Field POS_T = scalar("posT");
    private static final Field POS_X = scalar("posX");
    private static final Field POS_Y = scalar("posY");
    private static final Field POS_Z = scalar("posZ");

    @Override public String adapterId() { return "susy-celestial-registry"; }
    @Override public String categoryId() { return "susy-celestial-objects"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        ArrayDeque<CelestialObject> pending = new ArrayDeque<CelestialObject>();
        add(pending, CelestialObjects.MILKY_WAY);
        add(pending, CelestialObjects.SOLAR_SYSTEM);
        add(pending, CelestialObjects.SUN);
        add(pending, CelestialObjects.EARTH);
        add(pending, CelestialObjects.MOON);
        if (Planetoid.PLANETOIDS == null) {
            throw new IllegalStateException("SuSy planetoid registry is null");
        }
        for (Planetoid planetoid : Planetoid.PLANETOIDS.keySet()) add(pending, planetoid);

        Set<CelestialObject> seen = Collections.newSetFromMap(
            new IdentityHashMap<CelestialObject, Boolean>()
        );
        while (!pending.isEmpty()) {
            CelestialObject body = pending.removeFirst();
            if (!seen.add(body)) continue;
            for (CelestialObject child : body.getChildBodies()) add(pending, child);

            JsonObject row = new JsonObject();
            row.addProperty("record_type", "susy-celestial-object");
            row.addProperty("translation_key", body.getTranslationKey());
            row.addProperty("runtime_class", body.getClass().getName());
            row.addProperty("body_type", body.getCelestialBodyType().name());
            row.add("mass", encoder.encode(PackCoreReflection.value(MASS, body)));
            JsonObject position = new JsonObject();
            position.add("t", encoder.encode(PackCoreReflection.value(POS_T, body)));
            position.add("x", encoder.encode(PackCoreReflection.value(POS_X, body)));
            position.add("y", encoder.encode(PackCoreReflection.value(POS_Y, body)));
            position.add("z", encoder.encode(PackCoreReflection.value(POS_Z, body)));
            row.add("position", position);
            CelestialObject parent = body.getParentBody();
            if (parent == null) row.add("parent", JsonNull.INSTANCE);
            else row.addProperty("parent", parent.getTranslationKey());
            JsonArray children = new JsonArray();
            for (CelestialObject child : body.getChildBodies()) {
                children.add(child.getTranslationKey());
            }
            row.add("children", children);
            if (body instanceof Planetoid) {
                Planetoid planetoid = (Planetoid) body;
                row.addProperty("planet_type", planetoid.getPlanetType().name());
                row.addProperty("dimension", planetoid.getDimension());
                Integer registered = Planetoid.PLANETOIDS.get(planetoid);
                if (registered == null) row.add("registered_dimension", JsonNull.INSTANCE);
                else row.addProperty("registered_dimension", registered);
                row.addProperty(
                    "dimension_registry_consistent",
                    registered != null && registered.intValue() == planetoid.getDimension()
                );
            }
            if (body instanceof Galaxy) {
                row.addProperty("galaxy_type", ((Galaxy) body).getGalaxyType().name());
            }
            if (body instanceof Star) {
                row.addProperty("star_type", ((Star) body).getStarType().name());
            }
            records.add(row);
        }
        if (records.isEmpty()) throw new IllegalStateException("SuSy celestial registry is empty");

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "susy-celestial-registry-authority");
        authority.addProperty("object_count", records.size());
        authority.addProperty("planetoid_count", Planetoid.PLANETOIDS.size());
        authority.addProperty("authority", "CelestialObjects roots plus Planetoid.PLANETOIDS");
        records.add(authority);
        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static Field scalar(String name) {
        return PackCoreReflection.field(CelestialObject.class, name, double.class, false);
    }

    private static void add(ArrayDeque<CelestialObject> pending, CelestialObject value) {
        if (value != null) pending.addLast(value);
    }
}
