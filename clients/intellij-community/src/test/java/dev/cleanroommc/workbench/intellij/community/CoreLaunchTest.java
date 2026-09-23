package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class CoreLaunchTest {
    @Test
    void windowsWslCoreUsesOneExactNoShellTransportArgv() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\core\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals("windows-wsl", launch.host());
        assertEquals("C:\\Windows\\System32\\wsl.exe", launch.executable());
        assertEquals(List.of(
                "--distribution", "Ubuntu",
                "--cd", "/home/developer/core",
                "--exec", "/home/developer/core/workbench"
        ), launch.prefixArguments());
        assertEquals(
                "/home/developer/records/plan.json",
                launch.commandPath(
                        "\\\\wsl.localhost\\Ubuntu\\home\\developer\\records\\plan.json",
                        "plan"
                )
        );
    }

    @Test
    void installedParityObservesTheExistingWindowsWslMapping() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\core\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals(
                new CoreLaunch.InstalledWindowsWslMappingV1(
                        "windows-wsl",
                        "Ubuntu",
                        "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace",
                        "/home/developer/workspace"
                ),
                launch.installedWindowsWslMappingV1(
                        "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace"
                )
        );
        assertThrows(IllegalArgumentException.class, () ->
                launch.installedWindowsWslMappingV1(
                        "\\\\wsl.localhost\\Debian\\home\\developer\\workspace"
                )
        );
    }

    @Test
    void foreignWslAndTraversalPathsFailClosed() {
        CoreLaunch launch = CoreLaunch.resolve(
                "//wsl.localhost/Ubuntu/home/developer/core/workbench",
                true,
                "C:\\Windows"
        );
        assertThrows(IllegalArgumentException.class, () -> launch.commandPath(
                "//wsl.localhost/Debian/home/developer/plan.json", "plan"
        ));
        assertThrows(IllegalArgumentException.class, () -> CoreLaunch.resolve(
                "//wsl.localhost/Ubuntu/home/developer/../workbench",
                true,
                "C:\\Windows"
        ));
        assertThrows(IllegalArgumentException.class, () -> CoreLaunch.resolve(
                "//wsl.localhost/Ubuntu;whoami/home/developer/workbench",
                true,
                "C:\\Windows"
        ));
        assertThrows(IllegalArgumentException.class, () -> launch.commandPath(
                " //wsl.localhost/Ubuntu/home/developer/plan.json", "plan"
        ));
    }

    @Test
    void coreArgumentsPreserveExactValues() {
        CoreLaunch launch = CoreLaunch.resolve("C:\\Workbench\\workbench.exe", true, "C:\\Windows");
        assertEquals(
                List.of(
                        "C:\\Workbench\\workbench.exe", "", " leading", "trailing ",
                        "json:=\"value\""
                ),
                launch.command(List.of("", " leading", "trailing ", "json:=\"value\""))
        );
    }

    @Test
    void windowsWslCommandPreservesExactArgumentsForSafeProcessEncoding() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\core\\workbench",
                true,
                "C:\\Windows"
        );
        assertEquals(List.of(
                "C:\\Windows\\System32\\wsl.exe",
                "--distribution", "Ubuntu",
                "--cd", "/home/developer/core",
                "--exec", "/home/developer/core/workbench",
                "console", "run", "action",
                "--set", "name:=\"Release Solvent\"",
                "--set", "nested:=\"a\\\"b\""
        ), launch.command(List.of(
                "console", "run", "action",
                "--set", "name:=\"Release Solvent\"",
                "--set", "nested:=\"a\\\"b\""
        )));
    }

    @Test
    void windowsLaunchRequiresSafeProcessEncoding() {
        String property = "jdk.lang.Process.allowAmbiguousCommands";
        String previous = System.getProperty(property);
        try {
            System.setProperty(property, "true");
            CoreLaunch.requireSafeWindowsProcessArguments();
            assertEquals("false", System.getProperty(property));
        } finally {
            if (previous == null) {
                System.clearProperty(property);
            } else {
                System.setProperty(property, previous);
            }
        }
    }

    @Test
    void windowsWslTransportRejectsASubstitutableSystemRoot() {
        for (String root : List.of(
                "C:\\Windows\\..\\hijack",
                "C:\\Windows\\\\nested",
                "\\\\server\\share"
        )) {
            assertThrows(IllegalArgumentException.class, () -> CoreLaunch.resolve(
                    "//wsl.localhost/Ubuntu/home/dev/core", true, root
            ));
        }
    }
}
