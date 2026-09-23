package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonPrimitive;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Cross-module proof that the shipped Community consumer accepts the current Shell seam. */
final class LiveCommandCatalogTest {
    @Test
    void currentShellPublishesAndReviewsTheCompleteCommandCenterCatalog() throws Exception {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .resolve("../..")
                .normalize();
        String catalogText = python(root, "console", "catalog", "--json");
        CommandCatalog catalog = CommandCatalog.parse(catalogText);
        Set<String> commands = catalog.commands().stream()
                .map(CommandCatalog.Command::commandId)
                .collect(java.util.stream.Collectors.toSet());
        assertTrue(commands.contains("developer-features.examples"));
        assertTrue(commands.contains("developer-features.present"));
        assertTrue(commands.contains("developer-features.plan-recipe-change"));
        assertTrue(commands.contains("developer-features.compare-recipe-runtime"));
        assertTrue(commands.contains("developer-features.plan-quest-for-process"));
        assertTrue(commands.contains("atlas.recipes-context"));
        assertTrue(commands.contains("atlas.recipes-search"));
        assertTrue(commands.contains("atlas.recipes-inspect"));
        assertTrue(commands.contains("atlas.recipes-compare-runtime"));
        assertEquals(Set.of(
                "change.material-fluid-start",
                "change.material-fluid-open",
                "change.material-fluid-test",
                "change.material-fluid-apply",
                "change.material-fluid-verify",
                "change.material-fluid-rollback",
                "change.material-fluid-recover"
        ), catalog.commandsForSuite("change").stream()
                .map(CommandCatalog.Command::commandId)
                .collect(java.util.stream.Collectors.toSet()));
        assertEquals(commands, catalog.suites().stream()
                .flatMap(suite -> catalog.commandsForSuite(suite.suiteId()).stream())
                .map(CommandCatalog.Command::commandId)
                .collect(java.util.stream.Collectors.toSet()));

        CommandCatalog.Command context = catalog.commandsForSuite("atlas").stream()
                .filter(command -> command.commandId().equals("atlas.recipes-context"))
                .findFirst()
                .orElseThrow();
        CommandFlow flow = CommandFlow.compose(
                CoreLaunch.resolve("workbench", false, null),
                catalog.catalogDigest(),
                context,
                Map.of("path", new JsonPrimitive(root.toString()))
        );
        String reviewText = python(root, flow.commandReviewArguments().toArray(String[]::new));
        CommandFlow.Bound bound = flow.bindReview(reviewText);
        assertEquals("atlas.recipes-context", bound.review().commandId());
        assertEquals("read-only", bound.review().risk());
        assertEquals("--execute", bound.executeArguments().getLast());

        CommandCatalog.Command changeOpen = catalog.commandsForSuite("change").stream()
                .filter(command -> command.commandId().equals("change.material-fluid-open"))
                .findFirst()
                .orElseThrow();
        CommandFlow changeFlow = CommandFlow.compose(
                CoreLaunch.resolve("workbench", false, null),
                catalog.catalogDigest(),
                changeOpen,
                Map.of("change_id", new JsonPrimitive("workbench-feature-change-v1-fixture"))
        );
        CommandFlow.Bound changeBound = changeFlow.bindReview(
                python(root, changeFlow.commandReviewArguments().toArray(String[]::new))
        );
        assertEquals("change.material-fluid-open", changeBound.review().commandId());
        assertEquals("read-only", changeBound.review().risk());
        assertEquals("--execute", changeBound.executeArguments().getLast());
    }

    private static String python(Path root, String... arguments) throws Exception {
        java.util.List<String> command = new java.util.ArrayList<>();
        command.add("python3");
        command.add(root.resolve("tools/workbench.py").toString());
        command.addAll(java.util.List.of(arguments));
        Process process = new ProcessBuilder(command)
                .directory(root.toFile())
                .redirectErrorStream(true)
                .start();
        process.getOutputStream().close();
        byte[] output = process.getInputStream().readNBytes(CommandCatalog.MAX_BYTES + 1);
        assertTrue(output.length <= CommandCatalog.MAX_BYTES, "live catalog output exceeded bound");
        assertTrue(process.waitFor(30, TimeUnit.SECONDS), "live Workbench command timed out");
        String text = new String(output, StandardCharsets.UTF_8);
        assertEquals(0, process.exitValue(), text);
        return text;
    }
}
