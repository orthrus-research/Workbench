package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class FeatureStateRootPolicyTest {
    private static final String ID = "workbench-state-root-policy:sha256:" + "a".repeat(64);

    private static String policy(String workspace, String root) {
        JsonObject value = new JsonObject();
        value.addProperty("format", "workbench-state-root-policy-v1");
        value.addProperty("schema_version", 1);
        value.addProperty("configuration_home", "/home/dev/.workbench");
        value.addProperty("workspace", workspace);
        value.add("workspace_id", null);
        value.addProperty("role", "feature");
        value.addProperty("state_root", root);
        value.addProperty("source", "user-selection");
        value.addProperty("selections_record_id",
                "workbench-state-root-selections:sha256:" + "b".repeat(64));
        value.addProperty("policy_id", ID);
        return value.toString();
    }

    @Test
    void readsTheExactWorkspaceScopedCoreChoice() {
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench", false, null);
        FeatureStateRootPolicy.Choice choice = FeatureStateRootPolicy.parse(
                policy("/home/dev/project", "/home/dev/feature-state"),
                launch, "/home/dev/project"
        );
        assertEquals("/home/dev/feature-state", choice.stateRoot());
        assertEquals(ID, choice.policyId());
        assertThrows(IllegalArgumentException.class, () -> FeatureStateRootPolicy.parse(
                policy("/home/dev/other", "/home/dev/feature-state"),
                launch, "/home/dev/project"
        ));
    }

    @Test
    void mapsOnlyTheConfiguredWslDistribution() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench", true, "C:\\Windows"
        );
        FeatureStateRootPolicy.Choice choice = FeatureStateRootPolicy.parse(
                policy("/home/dev/project", "/home/dev/feature-state"),
                launch, "/home/dev/project"
        );
        assertEquals("\\\\wsl.localhost\\Ubuntu\\home\\dev\\feature-state",
                choice.stateRoot());
        assertEquals("/home/dev/feature-state",
                launch.commandPath(choice.stateRoot(), "Feature state root"));
        assertThrows(IllegalArgumentException.class, () -> FeatureStateRootPolicy.parse(
                policy("/home/dev/project", "/home/dev/../other"),
                launch, "/home/dev/project"
        ));
        assertThrows(IllegalArgumentException.class, () -> launch.commandPath(
                "\\\\wsl.localhost\\Debian\\home\\dev\\feature-state",
                "Feature state root"
        ));
    }
}
