package dev.cleanroommc.workbench.intellij.community;

import com.intellij.openapi.actionSystem.ActionManager;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.DefaultActionGroup;
import com.intellij.testFramework.fixtures.BasePlatformTestCase;
import com.intellij.openapi.wm.ToolWindowFactory;
import java.util.List;

/** Genuine IntelliJ Platform coverage for the mandatory Community action surface. */
public final class CommunityPlatformFixtureTest extends BasePlatformTestCase {
    private static final List<String> REQUIRED_ACTIONS = List.of(
            "Workbench.OpenWorkspaceHome",
            "Workbench.ConfigureInstalledCore",
            "Workbench.OpenInstallationHelp",
            "Workbench.OpenSetup",
            "Workbench.QualifyProject",
            "Workbench.ReviewRecipes",
            "Workbench.ReopenFeatureStudioJob",
            "Workbench.RunMaterialFluidRecipe",
            "Workbench.OpenDeveloperTools",
            "Workbench.OpenRetainedRecords",
            "Workbench.OpenRecipeImpact",
            "Workbench.BrowseAtlasRecipes",
            "Workbench.NavigateSource",
            "Workbench.ReviewLocalChanges",
            "Workbench.RunSavedChecks"
    );

    public void testCommunityDescriptorRegistersSemanticNativeActions() {
        ActionManager manager = ActionManager.getInstance();
        AnAction groupAction = manager.getAction("Workbench.ActionGroup");
        assertTrue(groupAction instanceof DefaultActionGroup);
        DefaultActionGroup group = (DefaultActionGroup) groupAction;
        assertEquals(REQUIRED_ACTIONS.size(), group.getChildren(manager).length);

        for (String actionId : REQUIRED_ACTIONS) {
            AnAction action = manager.getAction(actionId);
            assertNotNull("Community action was not registered: " + actionId, action);
            String text = action.getTemplatePresentation().getText();
            String description = action.getTemplatePresentation().getDescription();
            assertNotNull("Community action lacks native text: " + actionId, text);
            assertFalse("Community action text is blank: " + actionId, text.isBlank());
            assertNotNull("Community action lacks an accessible description: " + actionId, description);
            assertFalse("Community action description is blank: " + actionId, description.isBlank());
        }
    }

    public void testCoreSettingUsesTheProductNamespace() {
        assertEquals("workbench.coreExecutable", CoreLocation.PROPERTY);
        assertFalse(CoreLocation.PROPERTY.startsWith("workbench.release."));
    }

    public void testRecipeReviewIsANativeLazyToolWindow() {
        assertEquals("Recipe Review", RecipeReviewToolWindowFactory.TOOL_WINDOW_ID);
        assertTrue(ToolWindowFactory.class.isAssignableFrom(
                RecipeReviewToolWindowFactory.class
        ));
    }
}
