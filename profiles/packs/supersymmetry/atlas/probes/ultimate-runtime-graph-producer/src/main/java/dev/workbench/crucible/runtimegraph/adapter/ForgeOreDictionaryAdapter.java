package dev.workbench.crucible.runtimegraph.adapter;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CanonicalJson;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.item.ItemStack;
import net.minecraftforge.oredict.OreDictionary;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Complete ore-key, semantic membership, and registration-precedence observation. */
public final class ForgeOreDictionaryAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "forge-ore-dictionary"; }
    @Override public String categoryId() { return "item-classification"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        String[] rawNames = OreDictionary.getOreNames();
        if (rawNames == null) throw new IllegalStateException("ore dictionary names are null");
        Map<String, Integer> nameOrdinals = new HashMap<String, Integer>();
        for (int index = 0; index < rawNames.length; index++) {
            if (rawNames[index] == null || nameOrdinals.put(rawNames[index], index) != null) {
                throw new IllegalStateException("ore dictionary names are invalid or duplicated");
            }
        }
        List<String> names = new ArrayList<String>(nameOrdinals.keySet());
        Collections.sort(names);
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();
        for (String name : names) {
            int numericId = OreDictionary.getOreID(name);
            if (numericId < 0 || !name.equals(OreDictionary.getOreName(numericId))) {
                throw new IllegalStateException("ore dictionary numeric round trip failed: " + name);
            }
            List<ItemStack> members = OreDictionary.getOres(name, false);
            if (members == null) throw new IllegalStateException("ore dictionary members are null");
            List<Member> semantic = new ArrayList<Member>();
            JsonArray precedence = new JsonArray();
            for (int ordinal = 0; ordinal < members.size(); ordinal++) {
                ItemStack stack = members.get(ordinal);
                if (stack == null || stack.isEmpty()) {
                    throw new IllegalStateException("ore dictionary contains an empty member: " + name);
                }
                JsonElement payload = encoder.encode(stack);
                String identity = CanonicalJson.sha256(payload);
                semantic.add(new Member(ordinal, identity, payload));
                JsonObject occurrence = new JsonObject();
                occurrence.addProperty("member_identity_sha256", identity);
                occurrence.addProperty("registration_ordinal", ordinal);
                precedence.add(occurrence);
            }
            Collections.sort(semantic, new Comparator<Member>() {
                @Override
                public int compare(Member left, Member right) {
                    int payload = CanonicalJson.compareUnsigned(
                        CanonicalJson.bytes(left.payload),
                        CanonicalJson.bytes(right.payload)
                    );
                    return payload != 0
                        ? payload
                        : Integer.compare(left.registrationOrdinal, right.registrationOrdinal);
                }
            });
            Map<String, Integer> duplicateOrdinals = new HashMap<String, Integer>();
            JsonArray semanticMembers = new JsonArray();
            for (int ordinal = 0; ordinal < semantic.size(); ordinal++) {
                Member member = semantic.get(ordinal);
                int duplicate = duplicateOrdinals.containsKey(member.identity)
                    ? duplicateOrdinals.get(member.identity).intValue() : 0;
                duplicateOrdinals.put(member.identity, Integer.valueOf(duplicate + 1));
                JsonObject row = new JsonObject();
                row.addProperty("canonical_ordinal", ordinal);
                row.addProperty("duplicate_ordinal", duplicate);
                row.addProperty("member_identity_sha256", member.identity);
                row.addProperty("registration_ordinal", member.registrationOrdinal);
                row.add("item", member.payload);
                semanticMembers.add(row);
            }
            JsonObject record = new JsonObject();
            record.addProperty("record_type", "ore-dictionary-key");
            record.addProperty("name", name);
            record.addProperty("numeric_id", numericId);
            record.addProperty("name_ordinal", nameOrdinals.get(name).intValue());
            record.add("semantic_members", semanticMembers);
            record.add("registration_precedence", precedence);
            record.addProperty("registration_precedence_sha256", CanonicalJson.sha256(precedence));
            records.add(record);
        }
        return new AdapterSnapshot(
            records,
            encoder.getDiagnostics(),
            encoder.getUnsupportedCount()
        );
    }

    private static final class Member {
        private final int registrationOrdinal;
        private final String identity;
        private final JsonElement payload;

        private Member(int registrationOrdinal, String identity, JsonElement payload) {
            this.registrationOrdinal = registrationOrdinal;
            this.identity = identity;
            this.payload = payload;
        }
    }
}
