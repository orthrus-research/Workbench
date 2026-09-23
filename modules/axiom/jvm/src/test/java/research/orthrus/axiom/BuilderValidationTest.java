package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

class BuilderValidationTest {
    @Test void nativeErrorsPreserveUpstreamOrderAndMessages() {
        var result = new BuilderValidation(0, 0, List.of(1), List.of(1, 2), List.of(1, 2, 3), List.of(1),
                new BuilderValidation.Shape(0, 1, 2, 0)).errors();
        assertEquals(List.of("EU/t must not be to 0", "Duration must not be less or equal to 0",
                "No item inputs allowed, but found 1", "Must have at most 1 item output, but found 2",
                "Must have at most 2 fluid inputs, but found 3", "No fluid outputs allowed, but found 1"), result);
        assertThrows(UnsupportedOperationException.class, () -> result.add("changed"));
    }

    @Test void negativePowerAndEmptyFieldsAreNotInventedBuilderErrors() {
        assertEquals(List.of(), new BuilderValidation(Integer.MIN_VALUE, Integer.MAX_VALUE,
                List.of(), List.of(), List.of(), List.of(), new BuilderValidation.Shape(0, 0, 0, 0)).errors());
        // Empty fields passing this operation does not prove tree insertion or machine acceptance.
    }

    @Test void existingDefinitionModelRetainsCallbackEffectsBeforeOriginalRejection() {
        var result = new Engine().run("check", EngineTest.request(EngineTest.IMPORTS + EngineTest.BASIC.replace("duration(200)", "duration(0)")));
        assertEquals("rejected", result.get("status"));
        var definitions = Json.array(Json.object(result.get("result")).get("definitions"));
        assertEquals(2, definitions.size());
        assertEquals("blender", Json.object(definitions.getFirst()).get("map"));
        for (Object definition : definitions) assertEquals(false, Json.object(definition).get("registered"));
    }
}
