package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

public final class DeveloperFeatureClientTest {
    private static final String PLAN =
            "workbench-developer-material-fluid-recipe-plan:sha256:" + "1".repeat(64);
    private static final String RUN =
            "workbench-developer-material-fluid-recipe-run:sha256:" + "2".repeat(64);
    private static final List<String> ASSERTIONS = List.of(
            "fluid_registration", "fml_client_load", "groovy_compilation",
            "localization", "material_registration", "recipe_registration"
    );

    private static DeveloperFeatureClient.Options options() {
        return new DeveloperFeatureClient.Options(
                "prism", "/opt/prism", "/tmp/prism", null, null,
                "/opt/packwiz", List.of("/tmp/seed"), "/tmp/state", 600, 120, 21600
        );
    }

    @Test
    void constructsOneExactConsentNoShellArgv() {
        List<String> command = DeveloperFeatureClient.runCommand("/opt/workbench", PLAN, options());
        assertEquals(List.of(
                "/opt/workbench", "feature", "run", "material-fluid-recipe", PLAN,
                "--consent", PLAN, "--launcher", "prism",
                "--launcher-executable", "/opt/prism",
                "--launcher-root", "/tmp/prism",
                "--packwiz-executable", "/opt/packwiz",
                "--seed", "/tmp/seed",
                "--state-root", "/tmp/state",
                "--timeout", "600.0", "--attach-timeout", "120.0",
                "--session-timeout", "21600.0", "--json"
        ), command);
    }

    @Test
    void mapsEveryPathThroughTheExactWslTransport() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\workbench", true, "C:\\Windows"
        );
        DeveloperFeatureClient.Options options = new DeveloperFeatureClient.Options(
                "prism",
                "\\\\wsl.localhost\\Ubuntu\\mnt\\c\\Prism.exe",
                "\\\\wsl.localhost\\Ubuntu\\mnt\\c\\Prism",
                null, null,
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\packwiz",
                List.of(),
                "\\\\wsl.localhost\\Ubuntu\\home\\dev\\state",
                600, 120, 21600
        );
        List<String> command = DeveloperFeatureClient.runCommand(launch, PLAN, options);
        assertEquals("C:\\Windows\\System32\\wsl.exe", command.getFirst());
        assertTrue(command.contains("/mnt/c/Prism.exe"));
        assertTrue(command.contains("/mnt/c/Prism"));
        assertTrue(command.contains("/home/dev/packwiz"));
        assertTrue(command.contains("/home/dev/state"));
    }

    @Test
    void acceptsOnlyACompleteSixAssertionReceipt() throws Exception {
        DeveloperFeatureClient.Result result = DeveloperFeatureClient.parseResult(
                receipt("complete", null), PLAN
        );
        assertTrue(result.complete());
        assertEquals(RUN, result.id());

        assertThrows(
                IOException.class,
                () -> DeveloperFeatureClient.parseResult(receipt("complete", "localization"), PLAN)
        );
    }

    @Test
    void rejectsChangedOutcomeMeaningAndCliErrorExit() throws Exception {
        DeveloperFeatureClient.Result complete = DeveloperFeatureClient.parseResult(
                receipt("complete", null), PLAN
        );
        DeveloperFeatureClient.validateExitStatus(0, complete);
        assertThrows(
                IOException.class,
                () -> DeveloperFeatureClient.validateExitStatus(1, complete)
        );

        JsonObject changedOutcome = receiptObject("complete", null);
        changedOutcome.addProperty("outcome", "runtime-incomplete");
        assertThrows(
                IOException.class,
                () -> DeveloperFeatureClient.parseResult(changedOutcome.toString(), PLAN)
        );

        JsonObject changedMeaning = receiptObject("complete", null);
        changedMeaning.getAsJsonObject("assertions")
                .getAsJsonObject("localization")
                .addProperty("meaning", "something else");
        assertThrows(
                IOException.class,
                () -> DeveloperFeatureClient.parseResult(changedMeaning.toString(), PLAN)
        );

        DeveloperFeatureClient.Result incomplete = DeveloperFeatureClient.parseResult(
                receipt("incomplete", "localization"), PLAN
        );
        DeveloperFeatureClient.validateExitStatus(1, incomplete);
        assertThrows(
                IOException.class,
                () -> DeveloperFeatureClient.validateExitStatus(2, incomplete)
        );
    }

    private static String receipt(String state, String failedAssertion) {
        return receiptObject(state, failedAssertion).toString();
    }

    private static JsonObject receiptObject(String state, String failedAssertion) {
        JsonObject assertions = new JsonObject();
        for (String name : ASSERTIONS) {
            JsonObject assertion = new JsonObject();
            assertion.addProperty("meaning", switch (name) {
                case "fml_client_load" -> "the disposable client reaches the exact FML loaded marker";
                case "groovy_compilation" -> "the changed Groovy program compiles in the projected client";
                case "material_registration" -> "the requested GregTech material identity is registered";
                case "fluid_registration" -> "the material-backed Forge fluid identity is registered";
                case "localization" -> "the requested client translation resolves to its intended label";
                case "recipe_registration" -> "the exact reviewed machine recipe is registered once in its selected map";
                default -> throw new IllegalArgumentException(name);
            });
            assertion.addProperty("state", name.equals(failedAssertion) ? "failed" : "observed");
            assertions.add(name, assertion);
        }
        JsonObject root = new JsonObject();
        root.addProperty("format", "workbench-developer-material-fluid-recipe-run-v1");
        root.addProperty("schema_version", 1);
        root.addProperty("kind", "workbench-developer-material-fluid-recipe-run");
        root.addProperty("id", RUN);
        root.addProperty("plan_id", PLAN);
        root.addProperty("state", state);
        root.addProperty("outcome", "runtime-completed");
        if (state.equals("incomplete")) {
            root.addProperty("outcome", "runtime-assertion-failed");
        }
        root.add("assertions", assertions);
        return root;
    }
}
