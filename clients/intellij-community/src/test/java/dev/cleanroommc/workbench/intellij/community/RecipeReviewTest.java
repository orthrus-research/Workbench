package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;

import javax.swing.tree.DefaultMutableTreeNode;
import java.util.ArrayList;
import java.util.Enumeration;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class RecipeReviewTest {
    @Test
    void parsesIdentityBoundPrScopedDecisionAndNativeTargets() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        RecipeReview review = RecipeReview.parse(
                RecipeReviewTestFixture.reviewJson(), plan
        );
        assertEquals("attention", review.summary().status());
        assertEquals(1, review.modifiedRecipes().count());
        assertEquals(1, review.scope().attentionScope().introducedStaticSignals());
        assertEquals(2, review.scope().attentionScope().preexistingStaticSignals());
        assertEquals("clean", review.scope().gitHygiene().state());

        List<String> labels = labels(RecipeReviewTree.review(review));
        assertTrue(labels.stream().anyMatch(value -> value.contains("PR-introduced decision scope")));
        assertTrue(labels.stream().anyMatch(value -> value.contains("not PR-introduced")));
        assertTrue(labels.stream().anyMatch(value -> value.contains("Git hygiene: clean")));
        assertTrue(RecipeReviewDiffOpener.render(
                review.modifiedRecipes().rows().getFirst(), true
        ).contains("duration:\n  100"));
    }

    @Test
    void rejectsIdentityCoveredNestedSchemaDrift() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        JsonObject value = RecipeReviewTestFixture.reviewObject();
        value.getAsJsonObject("attention").addProperty("future_field", "unsupported");
        RecipeReviewTestFixture.reidentify(value);
        assertThrows(IllegalArgumentException.class, () ->
                RecipeReview.parse(value.toString(), plan));
    }

    @Test
    void rejectsReportThatDoesNotMatchConsentedHead() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        JsonObject value = RecipeReviewTestFixture.reviewObject();
        value.getAsJsonObject("selection")
                .getAsJsonObject("head")
                .addProperty("oid", "9".repeat(40));
        RecipeReviewTestFixture.reidentify(value);
        assertThrows(IllegalArgumentException.class, () ->
                RecipeReview.parse(value.toString(), plan));
    }

    @Test
    void rejectsAProgramProfileThatClaimsAnotherPackEnvironment() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        JsonObject value = RecipeReviewTestFixture.reviewObject();
        value.getAsJsonObject("profile").addProperty(
                "pack_profile_id", "workbench-pack:other"
        );
        RecipeReviewTestFixture.reidentify(value);

        IllegalArgumentException failure = assertThrows(
                IllegalArgumentException.class,
                () -> RecipeReview.parse(value.toString(), plan)
        );
        assertTrue(failure.getMessage().contains(
                "pack_profile_id must be workbench-pack:supersymmetry"
        ));
    }

    @Test
    void classifiesTheEnvironmentByPackFamilyNotProgramProfileVersion() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        JsonObject value = RecipeReviewTestFixture.reviewObject();
        value.getAsJsonObject("profile").addProperty(
                "profile_id",
                "workbench-pack:supersymmetry:groovy-program:future-version"
        );
        RecipeReviewTestFixture.reidentify(value);

        RecipeReview review = RecipeReview.parse(value.toString(), plan);
        assertEquals("attention", review.summary().status());
    }

    @Test
    void rejectsTheCliSelectorAsAPackEnvironmentIdentity() {
        RecipeReviewPlan plan = RecipeReviewPlan.parse(
                RecipeReviewTestFixture.planJson(), RecipeReviewTestFixture.PULL_REQUEST
        );
        JsonObject value = RecipeReviewTestFixture.reviewObject();
        value.getAsJsonObject("profile").addProperty(
                "pack_profile_id", "supersymmetry"
        );
        RecipeReviewTestFixture.reidentify(value);

        assertThrows(
                IllegalArgumentException.class,
                () -> RecipeReview.parse(value.toString(), plan)
        );
    }

    private static List<String> labels(DefaultMutableTreeNode root) {
        List<String> values = new ArrayList<>();
        Enumeration<?> rows = root.preorderEnumeration();
        while (rows.hasMoreElements()) {
            values.add(rows.nextElement().toString());
        }
        return values;
    }
}
