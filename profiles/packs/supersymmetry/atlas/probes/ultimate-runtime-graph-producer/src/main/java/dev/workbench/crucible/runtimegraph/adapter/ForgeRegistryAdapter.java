package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

import com.google.common.collect.BiMap;
import com.google.common.collect.Multimap;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.ReflectionAccess;

import net.minecraft.util.ResourceLocation;
import net.minecraftforge.registries.ForgeRegistry;
import net.minecraftforge.registries.IForgeRegistryEntry;
import net.minecraftforge.registries.RegistryManager;

import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Complete ACTIVE Forge registry identity, aliases, owners, and numeric bindings. */
public final class ForgeRegistryAdapter implements CaptureAdapter {
    private static final Field OWNERS = ReflectionAccess.requireAssignableField(
        ForgeRegistry.class, "owners", BiMap.class
    );
    private static final Field OVERRIDES = ReflectionAccess.requireAssignableField(
        ForgeRegistry.class, "overrides", Multimap.class
    );
    private static final Field DEFAULT_KEY = ReflectionAccess.requireField(
        ForgeRegistry.class, "defaultKey", ResourceLocation.class
    );
    private static final Field DEFAULT_VALUE = ReflectionAccess.requireAssignableField(
        ForgeRegistry.class, "defaultValue", IForgeRegistryEntry.class
    );
    private static final Field IS_FROZEN = ReflectionAccess.requireField(
        ForgeRegistry.class, "isFrozen", boolean.class
    );
    private static final Field ALLOW_OVERRIDES = ReflectionAccess.requireField(
        ForgeRegistry.class, "allowOverrides", boolean.class
    );
    private static final Field IS_MODIFIABLE = ReflectionAccess.requireField(
        ForgeRegistry.class, "isModifiable", boolean.class
    );
    private static final Field MIN_ID = ReflectionAccess.requireField(
        ForgeRegistry.class, "min", int.class
    );
    private static final Field MAX_ID = ReflectionAccess.requireField(
        ForgeRegistry.class, "max", int.class
    );

    @Override public String adapterId() { return "forge-registries"; }
    @Override public String categoryId() { return "registry-foundation"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        Map<ResourceLocation, ForgeRegistry.Snapshot> snapshots =
            RegistryManager.ACTIVE.takeSnapshot(false);
        List<ResourceLocation> names = new ArrayList<ResourceLocation>(snapshots.keySet());
        Collections.sort(names);
        if (names.isEmpty()) throw new IllegalStateException("ACTIVE Forge registry is empty");
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (ResourceLocation registryName : names) {
            ForgeRegistry.Snapshot snapshot = snapshots.get(registryName);
            ForgeRegistry<?> registry = RegistryManager.ACTIVE.getRegistry(registryName);
            if (snapshot == null || registry == null) {
                throw new IllegalStateException("Forge registry snapshot has no live registry");
            }
            List<ResourceLocation> keys = new ArrayList<ResourceLocation>(snapshot.ids.keySet());
            Collections.sort(keys);
            if (!new HashSet<ResourceLocation>(registry.getKeys()).equals(
                new HashSet<ResourceLocation>(keys)
            )) {
                throw new IllegalStateException("Forge registry key set drifted: " + registryName);
            }
            JsonArray entries = new JsonArray();
            Set<Integer> numericIds = new HashSet<Integer>();
            for (ResourceLocation key : keys) {
                Integer numericId = snapshot.ids.get(key);
                Object value = registry.getValue(key);
                if (numericId == null || numericId.intValue() < 0
                    || !numericIds.add(numericId) || value == null
                    || registry.getID(key) != numericId.intValue()) {
                    throw new IllegalStateException("invalid Forge registry entry " + registryName + '/' + key);
                }
                if (!(value instanceof IForgeRegistryEntry<?>)
                    || !key.equals(((IForgeRegistryEntry<?>) value).getRegistryName())) {
                    throw new IllegalStateException("Forge entry identity mismatch " + registryName + '/' + key);
                }
                Owner owner = owner(registry, value);
                if (!key.equals(owner.key)) {
                    throw new IllegalStateException("Forge entry owner key mismatch " + registryName + '/' + key);
                }
                JsonObject row = new JsonObject();
                row.addProperty("name", key.toString());
                row.addProperty("numeric_id", numericId.intValue());
                row.addProperty("runtime_class", value.getClass().getName());
                row.addProperty("owner_mod_id", owner.modId);
                row.addProperty("dummied", snapshot.dummied.contains(key));
                row.addProperty(
                    "registration_state",
                    snapshot.dummied.contains(key) ? "dummy-selected" : "selected"
                );
                String override = snapshot.overrides.get(key);
                if (override == null) row.add("override_owner", JsonNull.INSTANCE);
                else row.addProperty("override_owner", override);
                row.add("alternatives", alternatives(registry, key, value));
                entries.add(row);
            }
            List<Map.Entry<ResourceLocation, ResourceLocation>> aliases =
                new ArrayList<Map.Entry<ResourceLocation, ResourceLocation>>(
                    snapshot.aliases.entrySet()
                );
            Collections.sort(aliases, new Comparator<Map.Entry<ResourceLocation, ResourceLocation>>() {
                @Override
                public int compare(
                    Map.Entry<ResourceLocation, ResourceLocation> left,
                    Map.Entry<ResourceLocation, ResourceLocation> right
                ) {
                    return left.getKey().compareTo(right.getKey());
                }
            });
            JsonArray aliasRows = new JsonArray();
            for (Map.Entry<ResourceLocation, ResourceLocation> alias : aliases) {
                ResourceLocation terminal = terminal(snapshot, alias.getKey());
                if (registry.getValue(alias.getKey()) != registry.getValue(terminal)) {
                    throw new IllegalStateException("Forge alias round trip failed: " + alias.getKey());
                }
                JsonObject row = new JsonObject();
                row.addProperty("name", alias.getKey().toString());
                row.addProperty("target", alias.getValue().toString());
                row.addProperty("terminal_target", terminal.toString());
                aliasRows.add(row);
            }
            List<Integer> blocked = new ArrayList<Integer>(snapshot.blocked);
            Collections.sort(blocked);
            JsonArray blockedRows = new JsonArray();
            for (Integer blockedId : blocked) blockedRows.add(blockedId.intValue());

            JsonObject record = new JsonObject();
            record.addProperty("record_type", "forge-registry");
            record.addProperty("registry_name", registryName.toString());
            record.addProperty("entry_super_type", registry.getRegistrySuperType().getName());
            record.addProperty("frozen", ((Boolean) ReflectionAccess.read(IS_FROZEN, registry)).booleanValue());
            record.addProperty("allow_overrides", ((Boolean) ReflectionAccess.read(ALLOW_OVERRIDES, registry)).booleanValue());
            record.addProperty("modifiable", ((Boolean) ReflectionAccess.read(IS_MODIFIABLE, registry)).booleanValue());
            record.addProperty("minimum_numeric_id", ((Integer) ReflectionAccess.read(MIN_ID, registry)).intValue());
            record.addProperty("maximum_numeric_id", ((Integer) ReflectionAccess.read(MAX_ID, registry)).intValue());
            ResourceLocation defaultKey = (ResourceLocation) ReflectionAccess.read(DEFAULT_KEY, registry);
            Object defaultValue = ReflectionAccess.read(DEFAULT_VALUE, registry);
            if (defaultKey == null) record.add("default_key", JsonNull.INSTANCE);
            else record.addProperty("default_key", defaultKey.toString());
            if (defaultValue == null) record.add("default_value_registry_name", JsonNull.INSTANCE);
            else {
                ResourceLocation defaultName = ((IForgeRegistryEntry<?>) defaultValue).getRegistryName();
                if (defaultName == null) record.add("default_value_registry_name", JsonNull.INSTANCE);
                else record.addProperty("default_value_registry_name", defaultName.toString());
            }
            record.addProperty(
                "default_value_matches_selected_key",
                defaultKey == null ? defaultValue == null : registry.getValue(defaultKey) == defaultValue
            );
            record.add("entries", entries);
            record.add("aliases", aliasRows);
            record.add("blocked_numeric_ids", blockedRows);
            records.add(record);
        }
        return new AdapterSnapshot(records, Collections.<String>emptyList(), 0);
    }

    @SuppressWarnings("unchecked")
    private static JsonArray alternatives(
        ForgeRegistry<?> registry,
        ResourceLocation key,
        Object selected
    ) {
        Multimap<ResourceLocation, Object> overrides =
            (Multimap<ResourceLocation, Object>) ReflectionAccess.read(OVERRIDES, registry);
        Collection<Object> displaced = overrides.get(key);
        List<Object> values = new ArrayList<Object>();
        values.addAll(displaced);
        values.add(selected);
        IdentityHashMap<Object, Boolean> seen = new IdentityHashMap<Object, Boolean>();
        JsonArray rows = new JsonArray();
        int ordinal = 0;
        for (Object value : values) {
            if (value == null || seen.put(value, Boolean.TRUE) != null) continue;
            if (!(value instanceof IForgeRegistryEntry<?>)) {
                throw new IllegalStateException("Forge override alternative is not a registry entry");
            }
            IForgeRegistryEntry<?> entry = (IForgeRegistryEntry<?>) value;
            if (!key.equals(entry.getRegistryName())) {
                throw new IllegalStateException("Forge override alternative identity mismatch " + key);
            }
            Owner owner = owner(registry, value);
            if (!key.equals(owner.key)) {
                throw new IllegalStateException("Forge override alternative owner mismatch " + key);
            }
            JsonObject row = new JsonObject();
            row.addProperty("alternative_ordinal", ordinal++);
            row.addProperty("selection_role", value == selected ? "selected" : "displaced-override");
            row.addProperty("owner_mod_id", owner.modId);
            row.addProperty("runtime_class", value.getClass().getName());
            row.addProperty("registry_name", key.toString());
            rows.add(row);
        }
        return rows;
    }

    @SuppressWarnings("unchecked")
    private static Owner owner(ForgeRegistry<?> registry, Object value) {
        BiMap<Object, Object> owners =
            (BiMap<Object, Object>) ReflectionAccess.read(OWNERS, registry);
        Object raw = owners.inverse().get(value);
        if (raw == null) throw new IllegalStateException("Forge registry entry lacks owner");
        try {
            Field owner = raw.getClass().getDeclaredField("owner");
            Field key = raw.getClass().getDeclaredField("key");
            owner.setAccessible(true);
            key.setAccessible(true);
            Object ownerValue = owner.get(raw);
            Object keyValue = key.get(raw);
            if (!(ownerValue instanceof String) || !(keyValue instanceof ResourceLocation)) {
                throw new IllegalStateException("Forge owner row has unexpected fields");
            }
            return new Owner((String) ownerValue, (ResourceLocation) keyValue);
        } catch (ReflectiveOperationException exception) {
            throw new IllegalStateException("Forge owner row shape changed", exception);
        }
    }

    private static ResourceLocation terminal(
        ForgeRegistry.Snapshot snapshot,
        ResourceLocation start
    ) {
        ResourceLocation current = start;
        Set<ResourceLocation> seen = new HashSet<ResourceLocation>();
        while (snapshot.aliases.containsKey(current)) {
            if (!seen.add(current)) throw new IllegalStateException("cyclic Forge alias " + start);
            current = snapshot.aliases.get(current);
        }
        if (!snapshot.ids.containsKey(current)) {
            throw new IllegalStateException("Forge alias lacks a primary target " + start);
        }
        return current;
    }

    private static final class Owner {
        private final String modId;
        private final ResourceLocation key;

        private Owner(String modId, ResourceLocation key) {
            this.modId = modId;
            this.key = key;
        }
    }
}
