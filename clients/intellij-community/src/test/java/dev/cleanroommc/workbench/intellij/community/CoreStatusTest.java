package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class CoreStatusTest {
    @Test
    void projectsNativeVersionAndSetupReadiness() {
        CoreStatus.Version version = CoreStatus.parseVersion("""
                {"component_id":"workbench-core","version":"0.1.3"}
                """);
        assertEquals("0.1.3", version.currentVersion());

        CoreStatus.Setup setup = CoreStatus.parseSetup("""
                {
                  "format": "workbench-setup-check-v1",
                  "schema_version": 1,
                  "state": "installable",
                  "configured": false,
                  "blockers": [],
                  "managed_installs_available": ["java"],
                  "dependencies": [],
                  "additive": true
                }
                """);
        assertFalse(setup.ready());
        assertEquals("installable", setup.state());
        assertEquals(java.util.List.of("java"), setup.managedInstallsAvailable());
    }

    @Test
    void acceptsNativePreReleaseVersions() {
        for (String value : java.util.List.of("1.0.0a1", "1.0.0b2", "1.0.0rc3")) {
            assertEquals(value, CoreStatus.parseVersion(
                    "{\"component_id\":\"workbench-core\",\"version\":\"" + value + "\"}"
            ).currentVersion());
        }
    }

    @Test
    void rejectsNonCoreAndRetiredVersionResponses() {
        assertThrows(IllegalArgumentException.class, () -> CoreStatus.parseVersion("""
                {"component_id":"workbench-vscode","version":"0.1.3"}
                """));
        assertThrows(IllegalArgumentException.class, () -> CoreStatus.parseVersion("""
                {
                  "format":"workbench-component-version-v1",
                  "schema_version":1,
                  "component_id":"workbench-core",
                  "current_version":"0.1.3",
                  "distribution":{},"release_metadata":{},"claims":{}
                }
                """));
    }

    @Test
    void rejectsAdditiveClaimsAndInvalidVersions() {
        assertThrows(IllegalArgumentException.class, () -> CoreStatus.parseVersion("""
                {"component_id":"workbench-core","version":"0.1.3","claims":{"release_qualified":true}}
                """));
        for (String value : java.util.List.of("null", "3", "\"\"", "\"01.2.3\"", "\"1.2\"", "\"1.2.3-legacy\"", "\"1.2.3rc0\"")) {
            assertThrows(IllegalArgumentException.class, () -> CoreStatus.parseVersion(
                    "{\"component_id\":\"workbench-core\",\"version\":" + value + "}"
            ));
        }
    }

    @Test
    void malformedSetupStateFailsClosed() {
        assertThrows(IllegalArgumentException.class, () -> CoreStatus.parseSetup("""
                {
                  "format": "workbench-setup-check-v1",
                  "schema_version": 1,
                  "state": "ready",
                  "configured": "yes",
                  "blockers": [],
                  "managed_installs_available": []
                }
                """));
    }
}
