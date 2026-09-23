package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.io.TempDir;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertFalse;

final class AtlasRecipeClientTest {
    @Test
    void rootLinkageResolvesNativeSpellingWithoutAcceptingAnotherDirectory(@TempDir Path temporary) throws Exception {
        Path graph = Files.createDirectory(temporary.resolve("Graph é"));
        Path other = Files.createDirectory(temporary.resolve("other"));
        assertTrue(AtlasRecipeContext.sameRoot(graph.toRealPath().toString(), graph.resolve(".").toString()));
        assertFalse(AtlasRecipeContext.sameRoot(graph.toString(), other.toString()));
        assertFalse(AtlasRecipeContext.sameRoot(temporary.resolve("missing").toString(),
                temporary.resolve("missing/.").toString()));
        assertEquals(System.getProperty("os.name").startsWith("Windows"),
                AtlasRecipeContext.sameRoot(graph.toString(), graph.toString().toUpperCase(java.util.Locale.ROOT)));
    }
    private static final String RECIPE_ID =
            "workbench-atlas-node-v2:gt-recipe:mixer%7Cselected%7C0";

    @Test
    void composesStableDirectSearchAndImpactRoutes() {
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/bin/workbench", false, null);
        AtlasRecipeClient.GraphPath graph = AtlasRecipeClient.graphPath(
                launch, "/srv/atlas/graph"
        );
        assertEquals(List.of(
                "atlas", "recipes", "search", "/srv/atlas/graph", "mixer steam",
                "--limit", "100", "--json"
        ), AtlasRecipeClient.searchArguments(graph, "mixer steam", 100));
        assertEquals(List.of(
                "atlas", "recipes", "impact", "/srv/atlas/graph", RECIPE_ID,
                "--max-depth", "4", "--max-nodes", "500", "--json"
        ), AtlasRecipeClient.impactArguments(graph, RECIPE_ID, 4, 500));
    }

    @Test
    void graphAndRecipeInputsFailClosedBeforeProcessLaunch() {
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/bin/workbench", false, null);
        assertThrows(IllegalArgumentException.class,
                () -> AtlasRecipeClient.graphPath(launch, "relative/graph"));
        AtlasRecipeClient.GraphPath graph = AtlasRecipeClient.graphPath(launch, "/graph");
        assertThrows(IllegalArgumentException.class,
                () -> AtlasRecipeClient.searchArguments(graph, "   ", 100));
        assertThrows(IllegalArgumentException.class,
                () -> AtlasRecipeClient.impactArguments(graph, "gt-recipe:guess", 4, 500));
        assertThrows(IllegalArgumentException.class,
                () -> AtlasRecipeClient.impactArguments(graph, RECIPE_ID, 13, 500));
        assertThrows(IllegalArgumentException.class,
                () -> AtlasRecipeClient.impactArguments(graph, RECIPE_ID, 4, 2_001));
    }

    @Test
    void sameDistributionWslGraphMapsToOneAbsoluteLinuxContext() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
                true,
                "C:\\Windows"
        );
        AtlasRecipeClient.GraphPath graph = AtlasRecipeClient.graphPath(
                launch,
                "\\\\wsl.localhost\\Ubuntu\\home\\developer\\atlas\\graph"
        );
        assertEquals("/home/developer/atlas/graph", graph.commandPath());
        assertEquals("/home/developer/atlas/graph", graph.expectedContextRoot());
        assertThrows(IllegalArgumentException.class, () -> AtlasRecipeClient.graphPath(
                launch,
                "\\\\wsl.localhost\\Debian\\home\\developer\\atlas\\graph"
        ));
    }
}
