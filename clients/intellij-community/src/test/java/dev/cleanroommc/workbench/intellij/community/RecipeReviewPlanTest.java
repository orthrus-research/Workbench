package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class RecipeReviewPlanTest {
    @Test
    void parsesAndAuthenticatesExactProviderPlan() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        assertEquals(RecipeReviewTestFixture.PULL_REQUEST, plan.pullRequest());
        assertEquals(RecipeReviewTestFixture.BASE_OID, plan.baseOid());
        assertEquals(RecipeReviewTestFixture.HEAD_OID, plan.headOid());
        assertEquals(3, plan.effects().size());
    }

    @Test
    void rejectsPlanWhoseProviderIdentityWasChanged() {
        JsonObject value = RecipeReviewTestFixture.planObject();
        value.addProperty("head_oid", "9".repeat(40));
        assertThrows(IllegalArgumentException.class, () -> RecipeReviewPlan.parse(
                value.toString(), RecipeReviewTestFixture.PULL_REQUEST
        ));
    }
}
