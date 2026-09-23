package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class ProjectQualificationRequestTest {
    @TempDir
    Path temporary;

    @Test
    void buildsReadOnlyPlanThenExactPlanApply() throws IOException {
        Path workspace = Files.createDirectory(temporary.resolve("Supersymmetry"));
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/workbench", false, null);
        ProjectQualificationRequest request = ProjectQualificationRequest.fromProjectWorkspace(
                workspace.toString()
        );
        assertEquals(List.of(
                "project", "qualify", workspace.toRealPath().toString(),
                "--profile", "supersymmetry", "--plan", "--json"
        ), request.planArguments(launch));
        String planId = "workbench-project-qualification-plan:sha256:" + "a".repeat(64);
        assertEquals(List.of(
                "project", "qualify", workspace.toRealPath().toString(),
                "--profile", "supersymmetry", "--apply", planId, "--json"
        ), request.applyArguments(launch, planId));
    }

    @Test
    void refusesAnyApplyIdentityOtherThanOneExactQualificationPlan() throws IOException {
        Path workspace = Files.createDirectory(temporary.resolve("Supersymmetry"));
        ProjectQualificationRequest request = ProjectQualificationRequest.fromProjectWorkspace(
                workspace.toString()
        );
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/workbench", false, null);
        assertThrows(IllegalArgumentException.class, () ->
                request.applyArguments(launch, "not-a-plan"));
        assertThrows(IllegalArgumentException.class, () ->
                request.applyArguments(
                        launch,
                        "workbench-pr-preparation-plan-v2:sha256:" + "a".repeat(64)
                ));
    }

    @Test
    void resolvesSymlinkParentAliasBeforeBuildingNativeArgv() throws IOException {
        Path realParent = Files.createDirectory(temporary.resolve("real-parent"));
        Path workspace = Files.createDirectory(realParent.resolve("Supersymmetry"));
        Path alias = temporary.resolve("alias-parent");
        try {
            Files.createSymbolicLink(alias, realParent);
        } catch (UnsupportedOperationException | SecurityException | IOException error) {
            org.junit.jupiter.api.Assumptions.assumeTrue(
                    false, "symbolic links are unavailable: " + error.getMessage()
            );
        }
        ProjectQualificationRequest request = ProjectQualificationRequest.fromProjectWorkspace(
                alias.resolve("Supersymmetry").toString()
        );
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/workbench", false, null);

        assertEquals(workspace.toRealPath().toString(), request.workspace());
        assertEquals(workspace.toRealPath().toString(), request.coreWorkspace(launch));
        assertEquals(workspace.toRealPath().toString(), request.planArguments(launch).get(2));
    }

    @Test
    void normalizesNativeSeparatorsAndDotSegmentsOnce() throws IOException {
        Path parent = Files.createDirectory(temporary.resolve("parent"));
        Path workspace = Files.createDirectory(parent.resolve("Supersymmetry"));
        String selected = parent + File.separator + "." + File.separator
                + "Supersymmetry" + File.separator;

        ProjectQualificationRequest request = ProjectQualificationRequest.fromProjectWorkspace(
                selected
        );

        assertEquals(workspace.toRealPath().toString(), request.workspace());
    }

    @Test
    void preservesARealWorkspaceNameWithBoundarySpaces() throws IOException {
        Path workspace = Files.createDirectory(temporary.resolve(" Supersymmetry "));

        ProjectQualificationRequest request = ProjectQualificationRequest.fromProjectWorkspace(
                workspace.toString()
        );

        assertEquals(workspace.toRealPath().toString(), request.workspace());
    }

    @Test
    void mapsTheExactCandidateWorkspaceThroughWsl() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
                true,
                "C:\\Windows"
        );
        ProjectQualificationRequest request = ProjectQualificationRequest.fromResolvedWorkspace(
                "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry"
        );
        assertEquals("/work/Supersymmetry", request.coreWorkspace(launch));
        assertEquals("/work/Supersymmetry", request.planArguments(launch).get(2));
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualificationRequest.fromResolvedWorkspace(
                        "\\\\wsl.localhost\\Debian\\work\\Supersymmetry"
                ).planArguments(launch));
    }
}
