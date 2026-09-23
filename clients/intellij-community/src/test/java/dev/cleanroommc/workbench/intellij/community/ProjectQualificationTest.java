package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class ProjectQualificationTest {
    @Test
    void parsesAttentionAsApplicableAndPresentsEveryCheckWithoutApproval() {
        ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.attentionPlanJson(),
                ProjectQualificationTestFixture.WORKSPACE
        );
        assertEquals("attention", plan.state());
        assertTrue(plan.canApply());
        assertEquals(2, plan.checks().size());
        String presentation = OpenProjectQualificationAction.confirmationText(plan);
        assertTrue(presentation.contains("Qualification state: ATTENTION"));
        assertTrue(presentation.contains("READY — Supersymmetry profile conformance"));
        assertTrue(presentation.contains("ATTENTION — Packwiz index integrity"));
        assertTrue(presentation.contains("not release, publication, runtime, recipe, or code approval"));
    }

    @Test
    void rendersIncompatibleEvidenceButExposesNoAction() {
        ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.incompatiblePlanJson(),
                ProjectQualificationTestFixture.WORKSPACE
        );
        assertEquals("incompatible", plan.state());
        assertFalse(plan.canApply());
        assertNull(plan.action());
        String presentation = OpenProjectQualificationAction.confirmationText(plan);
        assertTrue(presentation.contains("Qualification state: INCOMPATIBLE"));
        assertTrue(presentation.contains("No qualification action is available"));
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualification.parseResult("{}", plan));
    }

    @Test
    void authenticatesSelectivePlanIdentityAndRequestedWorkspace() {
        JsonObject changedInspection = ProjectQualificationTestFixture.attentionPlanObject();
        changedInspection.addProperty(
                "inspection_id",
                "workbench-project-inspection:sha256:" + "9".repeat(64)
        );
        assertThrows(IllegalArgumentException.class, () -> ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(changedInspection),
                ProjectQualificationTestFixture.WORKSPACE
        ));

        assertThrows(IllegalArgumentException.class, () -> ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.attentionPlanJson(),
                "/work/AnotherCheckout"
        ));
    }

    @Test
    void rejectsMalformedOrInternallyContradictoryPlanJson() {
        JsonObject extra = ProjectQualificationTestFixture.attentionPlanObject();
        extra.addProperty("approved", true);
        assertThrows(IllegalArgumentException.class, () -> ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(extra),
                ProjectQualificationTestFixture.WORKSPACE
        ));

        JsonObject dirty = ProjectQualificationTestFixture.attentionPlanObject();
        dirty.getAsJsonObject("workspace").addProperty("dirty", true);
        ProjectQualificationTestFixture.reidentify(dirty);
        assertThrows(IllegalArgumentException.class, () -> ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(dirty),
                ProjectQualificationTestFixture.WORKSPACE
        ));

        String duplicated = ProjectQualificationTestFixture.attentionPlanJson().replaceFirst(
                "\\\"can_apply\\\":true",
                "\\\"can_apply\\\":true,\\\"can_apply\\\":true"
        );
        assertThrows(IllegalArgumentException.class, () -> ProjectQualification.parsePlan(
                duplicated, ProjectQualificationTestFixture.WORKSPACE
        ));
    }

    @Test
    void bindsApplyResultToPlanWhileAllowingFreshGitProvenance() {
        JsonObject planJson = ProjectQualificationTestFixture.attentionPlanObject();
        ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(planJson),
                ProjectQualificationTestFixture.WORKSPACE
        );
        JsonObject resultJson = ProjectQualificationTestFixture.resultObject(planJson);
        JsonObject fresh = resultJson.getAsJsonObject("qualification")
                .getAsJsonObject("workspace");
        fresh.addProperty("revision", "7".repeat(40));
        fresh.addProperty("dirty", true);
        JsonArray entries = new JsonArray();
        entries.add(" M pack.toml");
        fresh.add("dirty_entries", entries);
        fresh.addProperty("dirty_fingerprint", "sha256:" + "8".repeat(64));
        ProjectQualificationTestFixture.reidentifyResultBinding(resultJson);
        ProjectQualification.Result result = ProjectQualification.parseResult(
                ProjectQualificationTestFixture.json(resultJson), plan
        );
        assertEquals("qualified", result.outcome());
        assertEquals("7".repeat(40), result.qualification().workspace().revision());
        assertTrue(result.qualification().workspace().dirty());
        assertEquals(List.of(List.of(
                "workbench", "project", "qualify",
                ProjectQualificationTestFixture.WORKSPACE,
                "--profile", "supersymmetry", "--status",
                "--state-root", ProjectQualificationTestFixture.STATE_ROOT
        )), result.nextCommands());
        assertTrue(OpenProjectQualificationAction.resultText(result)
                .contains("Qualification state: ATTENTION"));
    }

    @Test
    void rejectsWrongPlanOrChangedQualificationAuthorityInResult() {
        JsonObject planJson = ProjectQualificationTestFixture.readyPlanObject();
        ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(planJson),
                ProjectQualificationTestFixture.WORKSPACE
        );
        JsonObject wrongPlan = ProjectQualificationTestFixture.resultObject(planJson);
        wrongPlan.addProperty(
                "applied_plan_id",
                ProjectQualificationTestFixture.PLAN_ID_PREFIX + "f".repeat(64)
        );
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualification.parseResult(
                        ProjectQualificationTestFixture.json(wrongPlan), plan
                ));

        JsonObject changedProfile = ProjectQualificationTestFixture.resultObject(planJson);
        changedProfile.getAsJsonObject("qualification")
                .getAsJsonObject("profile")
                .addProperty("pack_variant", "foreign-variant");
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualification.parseResult(
                        ProjectQualificationTestFixture.json(changedProfile), plan
                ));
    }

    @Test
    void rejectsAStatusCommandThatDropsOrChangesTheEffectiveStateRoot() {
        JsonObject planJson = ProjectQualificationTestFixture.readyPlanObject();
        ProjectQualification.Plan plan = ProjectQualification.parsePlan(
                ProjectQualificationTestFixture.json(planJson),
                ProjectQualificationTestFixture.WORKSPACE
        );
        JsonObject missingStateRoot = ProjectQualificationTestFixture.resultObject(planJson);
        missingStateRoot.getAsJsonArray("next_commands").set(0, strings(
                "workbench", "project", "qualify",
                ProjectQualificationTestFixture.WORKSPACE,
                "--profile", "supersymmetry", "--status"
        ));
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualification.parseResult(
                        ProjectQualificationTestFixture.json(missingStateRoot), plan
                ));

        JsonObject changedStateRoot = ProjectQualificationTestFixture.resultObject(planJson);
        changedStateRoot.getAsJsonArray("next_commands").set(0, strings(
                "workbench", "project", "qualify",
                ProjectQualificationTestFixture.WORKSPACE,
                "--profile", "supersymmetry", "--status",
                "--state-root", "/another-state"
        ));
        assertThrows(IllegalArgumentException.class, () ->
                ProjectQualification.parseResult(
                        ProjectQualificationTestFixture.json(changedStateRoot), plan
                ));
    }

    private static JsonArray strings(String... values) {
        JsonArray result = new JsonArray();
        for (String value : values) {
            result.add(value);
        }
        return result;
    }
}
