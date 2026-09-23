package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import javax.swing.tree.DefaultMutableTreeNode;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** Lazy presentation pages preserve the full report without eagerly materializing every branch. */
final class AtlasCompleteRecipeImpactTree {
    private static final int PAGE_SIZE = 100;
    private AtlasCompleteRecipeImpactTree() { }

    static DefaultMutableTreeNode build(AtlasCompleteRecipeImpact report) {
        DefaultMutableTreeNode root = new DefaultMutableTreeNode("Complete finite recipe impact · " + report.selectionId());
        root.add(new DefaultMutableTreeNode(report.statusText()));
        root.add(new DefaultMutableTreeNode(AtlasRecipeImpact.CLAIM_BOUNDARY));
        for (Map.Entry<String, JsonElement> entry : report.value().entrySet()) {
            root.add(new LazyNode(entry.getKey(), entry.getValue(), 0));
        }
        return root;
    }

    static final class LazyNode extends DefaultMutableTreeNode {
        private final String label;
        private final JsonElement value;
        private final int offset;
        private boolean populated;
        LazyNode(String label, JsonElement value, int offset) {
            super(description(label, value, offset));
            this.label = label; this.value = value; this.offset = offset;
            if (value.isJsonObject() && !value.getAsJsonObject().isEmpty()
                    || value.isJsonArray() && !value.getAsJsonArray().isEmpty()) {
                add(new DefaultMutableTreeNode("Expand to inspect retained values"));
            } else populated = true;
        }
        void populate() {
            if (populated) return;
            populated = true;
            removeAllChildren();
            if (value.isJsonArray()) {
                var array = value.getAsJsonArray();
                int end = Math.min(array.size(), offset + PAGE_SIZE);
                for (int index = offset; index < end; index++) add(new LazyNode("[" + index + "]", array.get(index), 0));
                if (end < array.size()) add(new LazyNode("Remaining " + (array.size() - end) + " values", value, end));
            } else if (value.isJsonObject()) {
                List<Map.Entry<String, JsonElement>> entries = new ArrayList<>(value.getAsJsonObject().entrySet());
                int end = Math.min(entries.size(), offset + PAGE_SIZE);
                for (int index = offset; index < end; index++) {
                    var entry = entries.get(index); add(new LazyNode(entry.getKey(), entry.getValue(), 0));
                }
                if (end < entries.size()) add(new LazyNode("Remaining " + (entries.size() - end) + " fields", value, end));
            }
        }
        private static String description(String name, JsonElement value, int offset) {
            if (value.isJsonArray()) return name + " · " + value.getAsJsonArray().size() + " retained values";
            if (value.isJsonObject()) return name + " · " + value.getAsJsonObject().size() + " fields";
            String text = value.isJsonNull() ? "null" : value.getAsJsonPrimitive().getAsString();
            return name + ": " + (text.length() <= 400 ? text : text.substring(0, 400) + "… (full value in Complete Atlas JSON)");
        }
    }
}
