package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class AtlasRecipeBrowseTest {
    private JsonObject fixture() throws Exception {
        return AtlasRecipeContract.parseRoot(Files.readString(Path.of("../testing/fixtures/atlas-recipe-browse-v1.json")), "fixture");
    }
    private JsonObject parse(JsonObject value) {
        return AtlasRecipeBrowse.parse(value.toString(), "/graph", value.getAsJsonObject("context").get("graph_set_id").getAsString(),
                value.getAsJsonObject("selection").get("selection_id").getAsString(), 0);
    }
    @Test void preservesCapturedValuesAndRefusesBrokenLinksAndPaging() throws Exception {
        JsonObject value = fixture();
        assertEquals(200, parse(value).getAsJsonObject("selection").getAsJsonObject("properties").get("duration").getAsInt());
        var wrongLink = value.deepCopy();
        wrongLink.getAsJsonArray("links").get(0).getAsJsonObject().getAsJsonObject("node").addProperty("selection_id", "changed");
        assertThrows(IllegalArgumentException.class, () -> parse(wrongLink));
        var hidden = value.deepCopy(); hidden.getAsJsonObject("page").addProperty("next_offset", 50);
        assertThrows(IllegalArgumentException.class, () -> parse(hidden));
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeBrowse.parse(value.toString(), "/other", "changed", "changed", 0));
    }

    @Test void labelsPreserveObservationMeaningAndExactInput() throws Exception {
        JsonObject item = AtlasRecipeContract.parseRoot("""
                {"kind":"item-variant","semantic_key":"gregtech:meta_item_1|627|627","properties":{
                "observed_item_names":[{"name":"circuit.microprocessor"},{"name":"circuit.microprocessor"}]}}
                """, "item");
        var before = item.deepCopy();
        assertEquals("circuit.microprocessor", AtlasRecipeBrowse.nodeLabel(item));
        assertEquals(before, item);
        assertEquals("final · 200 ticks · 120 EU/t", AtlasRecipeBrowse.nodeLabel(fixture().getAsJsonObject("selection")));
        var selector = AtlasRecipeContract.parseRoot("""
                {"kind":"gt-recipe-input-selector","semantic_key":"input","properties":{
                "ordinal":0,"amount":1,"non_consumable":true,"acceptance_complete":false}}
                """, "input");
        assertEquals("Input 1 · amount 1 · reusable · matching incomplete", AtlasRecipeBrowse.nodeLabel(selector));
        var relation = AtlasRecipeContract.parseRoot("""
                {"direction":"incoming","relationship":{"relation":"accepts-gt-item-alternative","properties":{"amount":1,"non_consumable":true}}}
                """, "link");
        assertEquals("Accepted by input · amount 1 · reusable", AtlasRecipeBrowse.relationshipLabel(relation));
        relation.getAsJsonObject("relationship").addProperty("relation", "produces-gt-item");
        relation.getAsJsonObject("relationship").getAsJsonObject("properties").remove("non_consumable");
        relation.getAsJsonObject("relationship").getAsJsonObject("properties").addProperty("amount", 8);
        relation.getAsJsonObject("relationship").getAsJsonObject("properties").addProperty("chanced", true);
        assertEquals("Produced by recipe · amount 8 · chance output; inspect values", AtlasRecipeBrowse.relationshipLabel(relation));
    }

    @Test void navigationOnlyOffersAvailableMovesAndMarksRepeatedNodes() throws Exception {
        var page = fixture();
        var firstPeer = page.getAsJsonArray("links").get(0).getAsJsonObject().getAsJsonObject("node");
        var choices = AtlasRecipeBrowse.choices(page, Set.of(firstPeer.get("selection_id").getAsString()), false);
        assertTrue(choices.stream().noneMatch(choice -> Set.of("back", "previous", "next").contains(choice.action())));
        assertTrue(choices.get(1).label().contains("cycle/reference"));
        assertEquals(firstPeer, choices.get(1).node());
        page.getAsJsonObject("page").addProperty("offset", 50);
        page.getAsJsonObject("page").addProperty("next_offset", 100);
        choices = AtlasRecipeBrowse.choices(page, Set.of(), true);
        assertEquals(java.util.List.of("values", "back", "previous", "next"), choices.subList(0, 4).stream().map(AtlasRecipeBrowse.Choice::action).toList());
    }
}
