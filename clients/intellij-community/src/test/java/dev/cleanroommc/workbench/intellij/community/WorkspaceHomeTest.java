package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class WorkspaceHomeTest {
    @TempDir
    Path temporary;

    @Test
    void preservesCoreActionOrderAndBlockedReasonsWithoutReclassification() {
        WorkspaceHome home = WorkspaceHome.parse(home().toString());

        assertEquals("Example Mod", home.workspace().displayName());
        assertEquals("attention", home.status());
        assertEquals(
                List.of("workspace-health", "search-workspace", "run-development-client"),
                home.actions().stream().map(WorkspaceHome.Action::id).toList()
        );
        WorkspaceHome.Action available = home.actions().get(0);
        assertTrue(available.available());
        assertEquals(List.of("workbench", "doctor", "/workspace"), available.argv());
        assertEquals(List.of(), available.blockers());
        assertNull(available.unavailableReason());

        WorkspaceHome.Action blocked = home.actions().get(1);
        assertFalse(blocked.available());
        assertNull(blocked.argv());
        assertEquals(List.of("PROJECT_SURFACE_UNAVAILABLE"), blocked.blockers());
        assertEquals("No bounded project files were found.", blocked.unavailableReason());
    }

    @Test
    void rejectsActionsThatWouldRequireTheClientToGuessAvailability() {
        JsonObject fakeAvailable = home();
        JsonObject action = fakeAvailable.getAsJsonArray("actions")
                .get(0).getAsJsonObject();
        action.getAsJsonArray("blockers").add("CORE_SAYS_BLOCKED");
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHome.parse(fakeAvailable.toString()));

        JsonObject unexplainedBlock = home();
        unexplainedBlock.getAsJsonArray("actions").get(1).getAsJsonObject()
                .add("unavailable_reason", JsonNull.INSTANCE);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHome.parse(unexplainedBlock.toString()));
    }

    @Test
    void rejectsDuplicateJsonKeysBeforeGsonCanCollapseThem() {
        String duplicate = home().toString().replaceFirst(
                "\\{\\\"format\\\"",
                "{\\\"format\\\":\\\"counterfeit\\\",\\\"format\\\""
        );
        assertThrows(IllegalArgumentException.class, () -> WorkspaceHome.parse(duplicate));
    }

    @Test
    void parsesTheLivePublicWorkspaceHomeV2ContractWithoutChangingTheV1Parser() throws Exception {
        Path repository = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().resolve("../..").normalize();
        Path workspace = temporary.resolve("unknown-project");
        Files.createDirectories(workspace);
        Files.writeString(workspace.resolve("README.md"), "fixture\n");
        String installedCore = System.getenv("WORKBENCH_EXECUTABLE");
        List<String> command = installedCore == null || installedCore.isBlank()
                ? List.of(
                        "python3",
                        repository.resolve("tools/workbench.py").toString(),
                        "open", workspace.toString(), "--json"
                )
                : List.of(installedCore, "open", workspace.toString(), "--json");
        Process process = new ProcessBuilder(command)
                .directory(repository.toFile()).start();
        process.getOutputStream().close();
        byte[] stdout = process.getInputStream().readNBytes(WorkspaceHomeV2.MAX_BYTES + 1);
        byte[] stderr = process.getErrorStream().readNBytes(64 * 1024 + 1);
        if (!process.waitFor(30, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new AssertionError("live workbench open timed out");
        }
        assertEquals(0, process.exitValue(), new String(stderr, StandardCharsets.UTF_8));
        String json = new String(stdout, StandardCharsets.UTF_8);
        WorkspaceHomeV2 parsed = WorkspaceHomeV2.parse(json);
        assertEquals(workspace.toString(), parsed.workspace().root());
        String firstOwnerAction = JsonParser.parseString(json).getAsJsonObject()
                .getAsJsonArray("jobs").get(0).getAsJsonObject()
                .get("id").getAsString();
        assertEquals(firstOwnerAction, parsed.jobs().getFirst().id());
    }

    private static JsonObject home() {
        JsonObject workspace = new JsonObject();
        workspace.addProperty("requested_path", "/workspace");
        workspace.addProperty("root", "/workspace");
        workspace.addProperty("display_name", "Example Mod");
        workspace.addProperty("kind", "cleanroom-mod");
        workspace.addProperty("recognition", "bounded");

        JsonObject status = new JsonObject();
        status.addProperty("status", "attention");

        JsonArray actions = new JsonArray();
        actions.add(action(
                "workspace-health",
                "Check workspace health",
                true,
                List.of("workbench", "doctor", "/workspace"),
                List.of(),
                null
        ));
        actions.add(action(
                "search-workspace",
                "Search this workspace",
                false,
                null,
                List.of("PROJECT_SURFACE_UNAVAILABLE"),
                "No bounded project files were found."
        ));
        actions.add(action(
                "run-development-client",
                "Run a development client",
                false,
                null,
                List.of("EXACT_RUN_PROFILE_REQUIRED"),
                "No exact run profile was found."
        ));

        JsonObject gap = new JsonObject();
        gap.addProperty("id", "generic-development-loop");
        gap.addProperty("summary", "A generic run adapter is not available.");
        JsonArray gaps = new JsonArray();
        gaps.add(gap);
        JsonArray limitations = new JsonArray();
        limitations.add("Home is read-only.");

        JsonObject root = new JsonObject();
        root.addProperty("format", WorkspaceHome.FORMAT);
        root.addProperty("schema_version", 1);
        root.addProperty("read_only", true);
        root.add("workspace", workspace);
        root.add("status", status);
        root.add("problems", new JsonArray());
        root.add("actions", actions);
        root.add("gaps", gaps);
        root.add("limitations", limitations);
        return root;
    }

    private static JsonObject action(
            String id,
            String title,
            boolean available,
            List<String> argv,
            List<String> blockers,
            String reason
    ) {
        JsonObject action = new JsonObject();
        action.addProperty("id", id);
        action.addProperty("title", title);
        action.addProperty("purpose", "Purpose for " + id);
        action.addProperty("available", available);
        if (argv == null) {
            action.add("argv", JsonNull.INSTANCE);
        } else {
            JsonArray arguments = new JsonArray();
            argv.forEach(arguments::add);
            action.add("argv", arguments);
        }
        JsonArray blockerRows = new JsonArray();
        blockers.forEach(blockerRows::add);
        action.add("blockers", blockerRows);
        if (reason == null) {
            action.add("unavailable_reason", JsonNull.INSTANCE);
        } else {
            action.addProperty("unavailable_reason", reason);
        }
        return action;
    }
}
