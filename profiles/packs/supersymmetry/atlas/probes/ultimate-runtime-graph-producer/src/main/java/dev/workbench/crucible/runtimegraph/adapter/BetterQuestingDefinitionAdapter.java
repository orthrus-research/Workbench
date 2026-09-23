package dev.workbench.crucible.runtimegraph.adapter;

import betterquesting.api.questing.IQuest;
import betterquesting.api.questing.IQuestLine;
import betterquesting.api.questing.IQuestLineEntry;
import betterquesting.api.questing.rewards.IReward;
import betterquesting.api.questing.tasks.ITask;
import betterquesting.api2.storage.DBEntry;
import betterquesting.questing.QuestDatabase;
import betterquesting.questing.QuestLineDatabase;

import com.google.gson.JsonObject;

import dev.workbench.crucible.runtimegraph.AdapterSnapshot;
import dev.workbench.crucible.runtimegraph.CaptureAdapter;
import dev.workbench.crucible.runtimegraph.CaptureCoordinator;
import dev.workbench.crucible.runtimegraph.StableValueEncoder;

import net.minecraft.nbt.NBTTagCompound;
import net.minecraft.nbt.NBTTagList;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Definition-only BetterQuesting projection; player progress and behavior stay outside capture. */
public final class BetterQuestingDefinitionAdapter implements CaptureAdapter {
    @Override public String adapterId() { return "betterquesting-definitions"; }
    @Override public String categoryId() { return "pack-progression-definitions"; }
    @Override public String checkpointId() { return CaptureCoordinator.POST_START_END_TICK; }

    @Override
    public AdapterSnapshot snapshot() {
        StableValueEncoder encoder = new StableValueEncoder();
        List<JsonObject> records = new ArrayList<JsonObject>();

        List<DBEntry<IQuest>> quests = sorted(QuestDatabase.INSTANCE.getEntries());
        Set<Integer> questIds = new HashSet<Integer>();
        for (DBEntry<IQuest> entry : quests) {
            if (!questIds.add(Integer.valueOf(entry.getID()))) {
                throw new IllegalStateException("duplicate BetterQuesting quest ID " + entry.getID());
            }
        }

        int taskCount = 0;
        int rewardCount = 0;
        int prerequisiteCount = 0;
        int danglingPrerequisiteCount = 0;
        int itemRequirementCount = 0;
        int fluidRequirementCount = 0;
        int rewardItemCount = 0;
        for (DBEntry<IQuest> questEntry : quests) {
            int questId = questEntry.getID();
            IQuest quest = questEntry.getValue();
            if (quest == null) {
                throw new IllegalStateException("null BetterQuesting quest " + questId);
            }

            NBTTagCompound definition = quest.writeToNBT(new NBTTagCompound(), false);
            JsonObject questRow = new JsonObject();
            questRow.addProperty("record_type", "betterquesting-quest-definition");
            questRow.addProperty("quest_id", questId);
            questRow.addProperty("runtime_class", quest.getClass().getName());
            questRow.add("definition_nbt", encoder.encode(definition));
            records.add(questRow);

            int[] requirements = quest.getRequirements();
            if (requirements == null) {
                throw new IllegalStateException("null BetterQuesting prerequisite array " + questId);
            }
            for (int ordinal = 0; ordinal < requirements.length; ordinal++) {
                int requiredQuestId = requirements[ordinal];
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "betterquesting-prerequisite-occurrence");
                row.addProperty("quest_id", questId);
                row.addProperty("ordinal", ordinal);
                row.addProperty("required_quest_id", requiredQuestId);
                row.addProperty(
                    "requirement_type",
                    quest.getRequirementType(requiredQuestId).name()
                );
                boolean present = questIds.contains(Integer.valueOf(requiredQuestId));
                row.addProperty("target_present", present);
                if (!present) danglingPrerequisiteCount++;
                records.add(row);
                prerequisiteCount++;
            }

            for (DBEntry<ITask> taskEntry : sorted(quest.getTasks().getEntries())) {
                ITask task = taskEntry.getValue();
                if (task == null || task.getFactoryID() == null) {
                    throw new IllegalStateException(
                        "invalid BetterQuesting task " + questId + '/' + taskEntry.getID()
                    );
                }
                NBTTagCompound taskDefinition = task.writeToNBT(new NBTTagCompound());
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "betterquesting-task-occurrence");
                row.addProperty("quest_id", questId);
                row.addProperty("task_id", taskEntry.getID());
                row.addProperty("task_type", task.getFactoryID().toString());
                row.addProperty("runtime_class", task.getClass().getName());
                row.add("definition_nbt", encoder.encode(taskDefinition));
                records.add(row);
                taskCount++;

                NBTTagList itemRequirements = taskDefinition.getTagList("requiredItems", 10);
                for (int ordinal = 0; ordinal < itemRequirements.tagCount(); ordinal++) {
                    JsonObject item = new JsonObject();
                    item.addProperty(
                        "record_type", "betterquesting-item-requirement-occurrence"
                    );
                    item.addProperty("quest_id", questId);
                    item.addProperty("task_id", taskEntry.getID());
                    item.addProperty("ordinal", ordinal);
                    item.add("item_stack_nbt", encoder.encode(itemRequirements.getCompoundTagAt(ordinal)));
                    records.add(item);
                    itemRequirementCount++;
                }
                NBTTagList fluidRequirements = taskDefinition.getTagList("requiredFluids", 10);
                for (int ordinal = 0; ordinal < fluidRequirements.tagCount(); ordinal++) {
                    JsonObject fluid = new JsonObject();
                    fluid.addProperty(
                        "record_type", "betterquesting-fluid-requirement-occurrence"
                    );
                    fluid.addProperty("quest_id", questId);
                    fluid.addProperty("task_id", taskEntry.getID());
                    fluid.addProperty("ordinal", ordinal);
                    fluid.add(
                        "fluid_stack_nbt", encoder.encode(fluidRequirements.getCompoundTagAt(ordinal))
                    );
                    records.add(fluid);
                    fluidRequirementCount++;
                }
            }

            for (DBEntry<IReward> rewardEntry : sorted(quest.getRewards().getEntries())) {
                IReward reward = rewardEntry.getValue();
                if (reward == null || reward.getFactoryID() == null) {
                    throw new IllegalStateException(
                        "invalid BetterQuesting reward " + questId + '/' + rewardEntry.getID()
                    );
                }
                NBTTagCompound rewardDefinition = reward.writeToNBT(new NBTTagCompound());
                JsonObject row = new JsonObject();
                row.addProperty("record_type", "betterquesting-reward-occurrence");
                row.addProperty("quest_id", questId);
                row.addProperty("reward_id", rewardEntry.getID());
                row.addProperty("reward_type", reward.getFactoryID().toString());
                row.addProperty("runtime_class", reward.getClass().getName());
                row.add("definition_nbt", encoder.encode(rewardDefinition));
                records.add(row);
                rewardCount++;
                rewardItemCount += rewardItems(
                    records, encoder, questId, rewardEntry.getID(), rewardDefinition,
                    "rewards", "grant-all"
                );
                rewardItemCount += rewardItems(
                    records, encoder, questId, rewardEntry.getID(), rewardDefinition,
                    "choices", "choose-one-option"
                );
            }
        }

        List<DBEntry<IQuestLine>> lines = sorted(QuestLineDatabase.INSTANCE.getEntries());
        Set<Integer> lineIds = new HashSet<Integer>();
        int placementCount = 0;
        for (DBEntry<IQuestLine> lineEntry : lines) {
            int lineId = lineEntry.getID();
            IQuestLine line = lineEntry.getValue();
            if (line == null || !lineIds.add(Integer.valueOf(lineId))) {
                throw new IllegalStateException("invalid BetterQuesting line ID " + lineId);
            }
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "betterquesting-quest-line-definition");
            row.addProperty("line_id", lineId);
            row.addProperty("order_index", QuestLineDatabase.INSTANCE.getOrderIndex(lineId));
            row.addProperty("runtime_class", line.getClass().getName());
            row.add(
                "definition_nbt",
                encoder.encode(line.writeToNBT(new NBTTagCompound(), null, false))
            );
            records.add(row);

            for (DBEntry<IQuestLineEntry> placement : sorted(line.getEntries())) {
                IQuestLineEntry value = placement.getValue();
                if (value == null) {
                    throw new IllegalStateException(
                        "null BetterQuesting placement " + lineId + '/' + placement.getID()
                    );
                }
                JsonObject placementRow = new JsonObject();
                placementRow.addProperty(
                    "record_type", "betterquesting-line-placement-occurrence"
                );
                placementRow.addProperty("line_id", lineId);
                placementRow.addProperty("quest_id", placement.getID());
                placementRow.addProperty("quest_present", questIds.contains(placement.getID()));
                placementRow.addProperty("x", value.getPosX());
                placementRow.addProperty("y", value.getPosY());
                placementRow.addProperty("size_x", value.getSizeX());
                placementRow.addProperty("size_y", value.getSizeY());
                records.add(placementRow);
                placementCount++;
            }
        }

        if (quests.size() != 1061 || lines.size() != 30 || taskCount != 1210
            || rewardCount != 51 || prerequisiteCount != 1354 || placementCount != 1181
            || itemRequirementCount != 1238 || fluidRequirementCount != 178
            || rewardItemCount != 65 || danglingPrerequisiteCount != 1) {
            throw new IllegalStateException(
                "BetterQuesting definition universe drifted: quests=" + quests.size()
                    + " lines=" + lines.size() + " tasks=" + taskCount
                    + " rewards=" + rewardCount + " prerequisites=" + prerequisiteCount
                    + " placements=" + placementCount
                    + " itemRequirements=" + itemRequirementCount
                    + " fluidRequirements=" + fluidRequirementCount
                    + " rewardItems=" + rewardItemCount
                    + " danglingPrerequisites=" + danglingPrerequisiteCount
            );
        }

        JsonObject authority = new JsonObject();
        authority.addProperty("record_type", "betterquesting-definition-authority");
        authority.addProperty("quest_count", quests.size());
        authority.addProperty("quest_line_count", lines.size());
        authority.addProperty("task_occurrence_count", taskCount);
        authority.addProperty("reward_occurrence_count", rewardCount);
        authority.addProperty("prerequisite_occurrence_count", prerequisiteCount);
        authority.addProperty("dangling_prerequisite_count", danglingPrerequisiteCount);
        authority.addProperty("line_placement_occurrence_count", placementCount);
        authority.addProperty("item_requirement_occurrence_count", itemRequirementCount);
        authority.addProperty("fluid_requirement_occurrence_count", fluidRequirementCount);
        authority.addProperty("reward_item_occurrence_count", rewardItemCount);
        authority.addProperty("definition_only", true);
        authority.addProperty("player_progress_captured", false);
        authority.addProperty("task_detection_invoked", false);
        authority.addProperty("reward_execution_invoked", false);
        authority.addProperty("client_gui_invoked", false);
        records.add(authority);

        return new AdapterSnapshot(records, encoder.getDiagnostics(), encoder.getUnsupportedCount());
    }

    private static int rewardItems(
        List<JsonObject> records,
        StableValueEncoder encoder,
        int questId,
        int rewardId,
        NBTTagCompound definition,
        String listName,
        String selectionSemantics
    ) {
        NBTTagList values = definition.getTagList(listName, 10);
        for (int ordinal = 0; ordinal < values.tagCount(); ordinal++) {
            JsonObject row = new JsonObject();
            row.addProperty("record_type", "betterquesting-reward-item-occurrence");
            row.addProperty("quest_id", questId);
            row.addProperty("reward_id", rewardId);
            row.addProperty("ordinal", ordinal);
            row.addProperty("selection_semantics", selectionSemantics);
            row.add("item_stack_nbt", encoder.encode(values.getCompoundTagAt(ordinal)));
            records.add(row);
        }
        return values.tagCount();
    }

    private static <T> List<DBEntry<T>> sorted(List<DBEntry<T>> entries) {
        List<DBEntry<T>> result = new ArrayList<DBEntry<T>>(entries);
        Collections.sort(result, new Comparator<DBEntry<T>>() {
            @Override
            public int compare(DBEntry<T> left, DBEntry<T> right) {
                return Integer.compare(left.getID(), right.getID());
            }
        });
        return result;
    }
}
