package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class CurrentContextFeatureClientTest {
    @Test
    void actionsContainNoWorkspacePathOrOwnerIdentity() {
        for (String action : List.of(
                "open", "test", "apply", "verify", "rollback", "recover"
        )) {
            assertEquals(
                    List.of("change", "material-fluid-recipe", action, "--json"),
                    CurrentContextFeatureClient.arguments(action)
            );
        }
        assertThrows(
                IllegalArgumentException.class,
                () -> CurrentContextFeatureClient.arguments("start")
        );
        assertThrows(
                IllegalArgumentException.class,
                () -> CurrentContextFeatureClient.arguments(
                        "work-session-v2-" + "1".repeat(32)
                )
        );
    }
}
