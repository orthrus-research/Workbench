package dev.cleanroommc.workbench.intellij.community;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class RecipeReviewRequestTest {
    @Test
    void buildsProviderPlanThenExactPlanApply() {
        CoreLaunch launch = CoreLaunch.resolve("/opt/workbench/workbench", false, null);
        assertEquals(List.of(
                "review", "pr", "2002",
                "--profile", "supersymmetry",
                "--source", "/work/Supersymmetry",
                "--plan", "--json"
        ), new RecipeReviewRequest(2002, "/work/Supersymmetry").planArguments(launch));
        String planId = "workbench-pr-preparation-plan-v2:sha256:" + "a".repeat(64);
        assertEquals(List.of(
                "review", "pr", "2002",
                "--profile", "supersymmetry",
                "--source", "/work/Supersymmetry",
                "--apply", planId,
                "--json"
        ), new RecipeReviewRequest(2002, "/work/Supersymmetry")
                .applyArguments(launch, planId));
    }

    @Test
    void validatesHumanPullRequestInputAndOpaquePlanIdentity() {
        assertEquals(2002, RecipeReviewRequest.parsePullRequest(" 2002 "));
        assertThrows(IllegalArgumentException.class, () ->
                RecipeReviewRequest.parsePullRequest("origin/main"));
        assertThrows(IllegalArgumentException.class, () ->
                new RecipeReviewRequest(1, "/work/Supersymmetry").applyArguments(
                        CoreLaunch.resolve("/opt/workbench/workbench", false, null),
                        "not-a-plan"
                ));
    }

    @Test
    void mapsCandidateCheckoutThroughWsl() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
                true,
                "C:\\Windows"
        );
        RecipeReviewRequest request = new RecipeReviewRequest(
                2002, "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry"
        );
        assertEquals("/work/Supersymmetry", request.planArguments(launch).get(6));
        assertThrows(IllegalArgumentException.class, () -> new RecipeReviewRequest(
                2002, "\\\\wsl.localhost\\Debian\\work\\Supersymmetry"
        ).planArguments(launch));
    }
}
