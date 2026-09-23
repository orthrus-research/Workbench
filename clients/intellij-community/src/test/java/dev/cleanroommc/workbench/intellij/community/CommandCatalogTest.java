package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonElement;
import com.google.gson.JsonPrimitive;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class CommandCatalogTest {
    private static final String CATALOG_DIGEST = "sha256:" + "1".repeat(64);
    private static final String ACTION_DIGEST = "sha256:" + "2".repeat(64);
    private static final String REVIEW_DIGEST = "sha256:" + "3".repeat(64);

    @Test
    void commandCenterPreservesEverySuiteAndFiltersOnlyByExactSuiteId() {
        CommandCatalog catalog = CommandCatalog.parse(catalogJson());
        assertEquals(CommandCatalog.FORMAT, "workbench-live-console-command-catalog-v2");
        assertEquals(List.of(
                "developer-features.present",
                "manual.open",
                "change.material-fluid-open",
                "atlas.recipes-search"
        ), catalog.commands().stream().map(CommandCatalog.Command::commandId).toList());
        assertEquals(List.of(
                "developer-features.present",
                "manual.open"
        ), catalog.commandsForSuite("developer-features").stream()
                .map(CommandCatalog.Command::commandId).toList());
        assertEquals(List.of("change.material-fluid-open"),
                catalog.commandsForSuite("change").stream()
                        .map(CommandCatalog.Command::commandId).toList());
        assertEquals(List.of("atlas.recipes-search"),
                catalog.commandsForSuite("atlas").stream()
                        .map(CommandCatalog.Command::commandId).toList());
        assertThrows(IllegalArgumentException.class,
                () -> catalog.commandsForSuite("missing"));
    }

    @Test
    void exactCatalogRejectsOwnerShapeDriftAndStaleSuiteCounts() {
        assertThrows(IllegalArgumentException.class, () -> CommandCatalog.parse(
                catalogJson().replace("\"command_count\":2", "\"command_count\":3")
        ));
        assertThrows(IllegalArgumentException.class, () -> CommandCatalog.parse(
                catalogJson().replaceFirst(
                        "\"format_version\"",
                        "\"unexpected\":true,\"format_version\""
                )
        ));
    }

    @Test
    void documentationEntriesRenderAsNonExecutingCatalogResults() {
        CommandCatalog catalog = CommandCatalog.parse(catalogJson());
        CommandCatalog.Command manual = catalog.commandsForSuite("developer-features").get(1);

        assertEquals("""
                Documentation-only catalog entry
                ================================
                Path: manuals/example.md

                No command was executed. Open the documented path from a Workbench checkout.""",
                OpenDeveloperToolsAction.documentationSummary(manual));
        assertThrows(IllegalArgumentException.class, () ->
                OpenDeveloperToolsAction.documentationSummary(
                        catalog.commandsForSuite("change").get(0)
                ));
    }

    @Test
    void flowConvertsCatalogPathValuesThroughExactWslTransport() {
        CommandCatalog catalog = CommandCatalog.parse(catalogJson());
        CommandCatalog.Command command = catalog.commandsForSuite("developer-features").get(0);
        CoreLaunch launch = CoreLaunch.resolve(
                "//wsl.localhost/Ubuntu/home/dev/bin/workbench",
                true,
                "C:\\Windows"
        );
        Map<String, JsonElement> values = new LinkedHashMap<>();
        values.put("collection", new JsonPrimitive(
                "//wsl.localhost/Ubuntu/home/dev/state/plans"
        ));
        values.put("family", new JsonPrimitive("recipe-change"));
        CommandFlow flow = CommandFlow.compose(
                launch, catalog.catalogDigest(), command, values
        );

        assertEquals(List.of(
                "console", "run", "developer-features.present",
                "--set", "collection:=\"/home/dev/state/plans\"",
                "--set", "family:=\"recipe-change\"",
                "--expect-catalog-digest", CATALOG_DIGEST,
                "--expect-action-digest", ACTION_DIGEST,
                "--review-json"
        ), flow.commandReviewArguments());
        assertThrows(IllegalArgumentException.class, () -> CommandFlow.compose(
                launch,
                catalog.catalogDigest(),
                command,
                Map.of("collection", new JsonPrimitive(
                        "//wsl.localhost/Debian/home/dev/state/plans"
                ))
        ));
    }

    @Test
    void reviewBindingPinsCatalogActionAndReviewDigests() {
        CommandCatalog catalog = CommandCatalog.parse(catalogJson());
        CommandCatalog.Command command = catalog.commandsForSuite("developer-features").get(0);
        CommandFlow flow = CommandFlow.compose(
                CoreLaunch.resolve("workbench", false, null),
                catalog.catalogDigest(),
                command,
                Map.of(
                        "collection", new JsonPrimitive("/tmp/plans"),
                        "family", new JsonPrimitive("recipe-change")
                )
        );
        CommandFlow.Bound bound = flow.bindReview(
                reviewJson(CATALOG_DIGEST, "none", "execute")
        );
        assertEquals(REVIEW_DIGEST, bound.review().reviewDigest());
        assertEquals("--execute", bound.executeArguments().getLast());
        assertEquals(REVIEW_DIGEST,
                bound.executeArguments().get(bound.executeArguments().size() - 2));
        assertThrows(IllegalArgumentException.class, () -> flow.bindReview(
                reviewJson("sha256:" + "9".repeat(64), "none", "execute")
        ));
    }

    @Test
    void reviewIntentMustFollowTheCatalogPreviewStrategy() {
        CommandCatalog catalog = CommandCatalog.parse(catalogJson());
        CommandCatalog.Command none = catalog.commandsForSuite("developer-features").get(0);
        assertThrows(IllegalArgumentException.class, () -> flow(catalog, none).bindReview(
                reviewJson(CATALOG_DIGEST, "none", "inert")
        ));

        CommandCatalog.Command inert = withPreview(none, "inert-only");
        assertThrows(IllegalArgumentException.class, () -> flow(catalog, inert).bindReview(
                reviewJson(CATALOG_DIGEST, "inert-only", "preview")
        ));

        CommandCatalog.Command ownerPreview = withPreview(none, "append-show");
        assertThrows(IllegalArgumentException.class, () -> flow(catalog, ownerPreview).bindReview(
                reviewJson(CATALOG_DIGEST, "append-show", "execute")
        ));
    }

    private static CommandFlow flow(
            CommandCatalog catalog,
            CommandCatalog.Command command
    ) {
        return CommandFlow.compose(
                CoreLaunch.resolve("workbench", false, null),
                catalog.catalogDigest(),
                command,
                Map.of(
                        "collection", new JsonPrimitive("/tmp/plans"),
                        "family", new JsonPrimitive("recipe-change")
                )
        );
    }

    private static CommandCatalog.Command withPreview(
            CommandCatalog.Command source,
            String preview
    ) {
        return new CommandCatalog.Command(
                source.commandId(),
                source.suiteId(),
                source.title(),
                source.summary(),
                source.authority(),
                source.risk(),
                preview,
                source.availability(),
                source.documentation(),
                source.document(),
                source.limitations(),
                source.commandPreview(),
                source.actionDigest(),
                source.options()
        );
    }

    private static String reviewJson(
            String catalogDigest,
            String preview,
            String previewIntent
    ) {
        return """
                {
                  "format_version":"workbench-live-console-command-review-v2",
                  "catalog_digest":"%s",
                  "action_digest":"%s",
                  "review_digest":"%s",
                  "command_id":"developer-features.present",
                  "risk":"read-only",
                  "preview":"%s",
                  "preview_intent":"%s",
                  "execute_intent":"execute",
                  "preview_command":"workbench feature present recipe-change plans plan-id --json",
                  "execute_command":"workbench feature present recipe-change plans plan-id --json"
                }
                """.formatted(
                        catalogDigest,
                        ACTION_DIGEST,
                        REVIEW_DIGEST,
                        preview,
                        previewIntent
                );
    }

    private static String catalogJson() {
        return """
                {
                  "format_version":"workbench-live-console-command-catalog-v2",
                  "catalog_digest":"%s",
                  "suites":[
                    {"suite_id":"developer-features","title":"Developer features","summary":"Blueprint records","authority":"Workbench Shell","availability":"available","command_count":2},
                    {"suite_id":"change","title":"Feature Change Workspace","summary":"Material-fluid lifecycle","authority":"Workbench Shell","availability":"experimental","command_count":1},
                    {"suite_id":"atlas","title":"Atlas","summary":"Observed evidence","authority":"Atlas","availability":"available","command_count":1}
                  ],
                  "commands":[
                    {
                      "command_id":"developer-features.present","suite_id":"developer-features","title":"Open feature record","summary":"Render an owner-validated record","authority":"Workbench Shell","risk":"read-only","preview":"none","availability":"available","documentation":null,"document":null,"limitations":["Does not authorize apply"],"command_preview":"workbench feature present ...","action_digest":"%s",
                      "options":[
                        {"key":"collection","label":"Record collection","help":"Directory containing the record","flags":[],"kind":"path","required":true,"positional":true,"choices":[],"nargs":"one","repeat":false,"placement":1,"sensitive":false,"required_group":false,"console_managed":false,"metavar":"PATH"},
                        {"key":"family","label":"Feature family","help":"Owning Blueprint family","flags":[],"kind":"choice","required":true,"positional":true,"choices":["material-fluid-recipe","recipe-change","quest-for-process"],"nargs":"one","repeat":false,"placement":0,"sensitive":false,"required_group":false,"console_managed":false}
                      ]
                    },
                    {
                      "command_id":"manual.open","suite_id":"developer-features","title":"Manual","summary":"Open documentation","authority":"Manuals","risk":"read-only","preview":"none","availability":"available","documentation":null,"document":"manuals/example.md","limitations":[],"command_preview":"manuals/example.md","action_digest":"sha256:%s","options":[]
                    },
                    {
                      "command_id":"change.material-fluid-open","suite_id":"change","title":"Open material-fluid change","summary":"Open one durable change","authority":"Workbench Shell","risk":"read-only","preview":"none","availability":"experimental","documentation":null,"document":null,"limitations":["Does not mutate the workspace"],"command_preview":"workbench change material-fluid open ...","action_digest":"sha256:%s",
                      "options":[
                        {"key":"change_id","label":"Change ID","help":"Durable change identity","flags":[],"kind":"text","required":true,"positional":true,"choices":[],"nargs":"one","repeat":false,"placement":0,"sensitive":false,"required_group":false,"console_managed":false,"metavar":"CHANGE_ID"}
                      ]
                    },
                    {
                      "command_id":"atlas.recipes-search","suite_id":"atlas","title":"Search recipes","summary":"Find recipe evidence","authority":"Atlas","risk":"read-only","preview":"none","availability":"available","documentation":null,"document":null,"limitations":["Source-only evidence may be incomplete"],"command_preview":"workbench atlas recipes search ...","action_digest":"sha256:%s","options":[]
                    }
                  ]
                }
                """.formatted(
                        CATALOG_DIGEST,
                        ACTION_DIGEST,
                        "4".repeat(64),
                        "6".repeat(64),
                        "5".repeat(64)
                );
    }
}
