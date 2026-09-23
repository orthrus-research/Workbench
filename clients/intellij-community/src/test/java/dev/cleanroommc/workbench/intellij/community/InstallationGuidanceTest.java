package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class InstallationGuidanceTest {
    @Test
    void givesAnExplicitNativeWheelhouseRecoveryPath() {
        String instructions = OpenInstallationHelpAction.instructions();
        assertTrue(instructions.contains("reviewed native Workbench wheelhouse"));
        assertTrue(instructions.contains("<wheelhouse>/install_workbench.py <wheelhouse> --destination <new-environment>"));
        assertTrue(instructions.contains("not an installed launcher"));
        assertFalse(instructions.contains("bootstrap_pixi"));
        assertTrue(instructions.contains("Configure Core Executable"));
        assertTrue(instructions.contains("Set Up or Repair Environment"));
        assertTrue(OpenInstallationHelpAction.DOCUMENTATION_URL.endsWith("#start-here"));
    }

    @Test
    void packagesAHandAuthoredVectorMarkRatherThanAnEmbeddedBitmap() throws IOException {
        try (InputStream stream = Objects.requireNonNull(
                getClass().getResourceAsStream("/META-INF/pluginIcon.svg")
        )) {
            String svg = new String(stream.readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(svg.startsWith("<svg"));
            assertTrue(svg.contains("<path"));
            assertFalse(svg.contains("<image"));
            assertFalse(svg.contains("data:image"));
        }
    }

    @Test
    void packagesThirdPartyAttributionBesideTheLicense() throws IOException {
        try (InputStream stream = Objects.requireNonNull(
                getClass().getResourceAsStream("/META-INF/NOTICE.md")
        )) {
            String notice = new String(stream.readAllBytes(), StandardCharsets.UTF_8);
            assertTrue(notice.contains("Minecraft"));
            assertTrue(notice.contains("respective owners"));
            assertTrue(notice.contains("LGPL-3.0-only"));
        }
    }
}
