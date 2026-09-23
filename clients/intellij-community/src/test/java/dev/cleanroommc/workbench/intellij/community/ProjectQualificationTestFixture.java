package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;

final class ProjectQualificationTestFixture {
    static final String WORKSPACE = "/work/Supersymmetry";
    static final String STATE_ROOT = "/state";
    static final String REVISION = "1".repeat(40);
    static final String PLAN_ID_PREFIX =
            "workbench-project-qualification-plan:sha256:";
    static final String INSPECTION_ID =
            "workbench-project-inspection:sha256:" + "2".repeat(64);
    static final String BINDING_ID =
            "workbench-project-qualification-binding:sha256:" + "3".repeat(64);
    private static final Gson GSON = new GsonBuilder()
            .disableHtmlEscaping()
            .serializeNulls()
            .create();

    private ProjectQualificationTestFixture() {
    }

    static JsonObject attentionPlanObject() {
        return applicablePlanObject("attention", "attention");
    }

    static JsonObject readyPlanObject() {
        return applicablePlanObject("ready", "ready");
    }

    static JsonObject incompatiblePlanObject() {
        JsonObject root = commonPlan("incompatible", false);
        root.add("checks", checks("incompatible"));
        root.add("limitations", strings(
                "No pack-family qualification is granted while exact profile inspection is incompatible."
        ));
        root.add("actions", new JsonArray());
        JsonObject consent = new JsonObject();
        consent.addProperty("required", false);
        consent.add("prompt", JsonNull.INSTANCE);
        consent.add("non_interactive", JsonNull.INSTANCE);
        root.add("consent", consent);
        reidentify(root);
        return root;
    }

    static String attentionPlanJson() {
        return GSON.toJson(attentionPlanObject());
    }

    static String incompatiblePlanJson() {
        return GSON.toJson(incompatiblePlanObject());
    }

    static JsonObject resultObject(JsonObject plan) {
        JsonObject result = new JsonObject();
        result.addProperty("format", "workbench-project-qualification-result-v1");
        result.addProperty("schema_version", 1);
        String operation = plan.getAsJsonArray("actions")
                .get(0).getAsJsonObject().get("operation").getAsString();
        String outcome = switch (operation) {
            case "atomic-private-record-create" -> "qualified";
            case "atomic-private-record-replace" -> "requalified";
            case "reuse-current-binding" -> "reused";
            default -> throw new IllegalArgumentException("unsupported fixture action");
        };
        result.addProperty("outcome", outcome);
        result.addProperty("applied_plan_id", plan.get("plan_id").getAsString());
        JsonObject binding = plan.getAsJsonObject("binding").deepCopy();
        binding.addProperty("state", "current");
        binding.add("stale_reasons", new JsonArray());
        JsonObject qualification = new JsonObject();
        qualification.addProperty("qualified", true);
        for (String field : new String[]{
                "project_id", "profile", "workspace", "inspection_id", "state",
                "checks", "limitations"
        }) {
            qualification.add(field, plan.get(field).deepCopy());
        }
        result.add("binding", binding);
        result.add("qualification", qualification);
        JsonArray next = new JsonArray();
        next.add(strings(
                "workbench", "project", "qualify", WORKSPACE,
                "--profile", "supersymmetry", "--status",
                "--state-root", STATE_ROOT
        ));
        result.add("next_commands", next);
        reidentifyResultBinding(result);
        return result;
    }

    static void reidentifyResultBinding(JsonObject result) {
        JsonObject binding = result.getAsJsonObject("binding");
        JsonObject qualification = result.getAsJsonObject("qualification");
        JsonObject body = new JsonObject();
        body.addProperty("format", "workbench-project-qualification-binding-v1");
        body.addProperty("schema_version", 1);
        body.add("binding_id", binding.get("binding_id").deepCopy());
        for (String field : new String[]{
                "project_id", "profile", "workspace", "inspection_id", "state",
                "checks", "limitations"
        }) {
            body.add(field, qualification.get(field).deepCopy());
        }
        binding.addProperty(
                "state_revision",
                CanonicalJson.contentId("workbench-project-qualification-state", body)
        );
    }

    static String json(JsonObject value) {
        return GSON.toJson(value);
    }

    static void reidentify(JsonObject plan) {
        JsonObject identity = new JsonObject();
        identity.add("project_id", plan.get("project_id").deepCopy());
        identity.add("profile", plan.get("profile").deepCopy());
        identity.addProperty(
                "workspace_root",
                plan.getAsJsonObject("workspace").get("root").getAsString()
        );
        identity.add("inspection_id", plan.get("inspection_id").deepCopy());
        identity.add("qualification_state", plan.get("state").deepCopy());
        JsonObject sourceBinding = plan.getAsJsonObject("binding");
        JsonObject binding = new JsonObject();
        for (String field : new String[]{
                "state", "binding_id", "path", "state_revision"
        }) {
            binding.add(field, sourceBinding.get(field).deepCopy());
        }
        identity.add("binding", binding);
        JsonArray actions = plan.getAsJsonArray("actions");
        identity.add(
                "action",
                actions.isEmpty() ? JsonNull.INSTANCE : actions.get(0).deepCopy()
        );
        plan.addProperty(
                "plan_id",
                CanonicalJson.contentId("workbench-project-qualification-plan", identity)
        );
    }

    private static JsonObject applicablePlanObject(
            String state,
            String indexState
    ) {
        JsonObject root = commonPlan(state, true);
        root.add("checks", checks(indexState));
        JsonArray limitations = strings(
                "Qualification selects pack-family authority; it is not a stable support or runtime-success claim."
        );
        if ("attention".equals(state)) {
            limitations.add(
                    "The declared and observed Packwiz index differ; qualification does not establish Packwiz or runtime payload integrity."
            );
        }
        root.add("limitations", limitations);
        JsonObject action = new JsonObject();
        action.addProperty("id", "persist-project-qualification");
        action.addProperty("operation", "atomic-private-record-create");
        action.addProperty(
                "destination",
                root.getAsJsonObject("binding").get("path").getAsString()
        );
        action.addProperty(
                "effect",
                "Persist the reviewed qualification in private external Workbench state without writing the checkout."
        );
        JsonArray actions = new JsonArray();
        actions.add(action);
        root.add("actions", actions);
        JsonObject consent = new JsonObject();
        consent.addProperty("required", true);
        consent.addProperty("prompt", "Apply this qualification? [y/N]");
        consent.addProperty(
                "non_interactive", "Pass this exact plan_id with --apply."
        );
        root.add("consent", consent);
        reidentify(root);
        return root;
    }

    private static JsonObject commonPlan(String state, boolean canApply) {
        JsonObject root = new JsonObject();
        root.addProperty("format", "workbench-project-qualification-plan-v1");
        root.addProperty("schema_version", 1);
        root.addProperty("operation_class", "review-before-mutation");
        root.addProperty("plan_id", PLAN_ID_PREFIX + "0".repeat(64));
        root.addProperty("state", state);
        root.addProperty("can_apply", canApply);
        root.addProperty("project_id", "supersymmetry");
        root.add("profile", profile());
        root.add("workspace", workspace());
        root.addProperty("inspection_id", INSPECTION_ID);
        root.add("binding", absentBinding());
        return root;
    }

    private static JsonObject profile() {
        JsonObject profile = new JsonObject();
        profile.addProperty("selector", "supersymmetry");
        profile.addProperty("pack_profile_id", "workbench-pack:supersymmetry");
        profile.addProperty("pack_variant", "master-ceu");
        profile.addProperty(
                "platform_profile_id",
                "workbench-platform:cleanroom:groovyscript:1.4.3"
        );
        profile.addProperty("selection_digest", "sha256:" + "5".repeat(64));
        return profile;
    }

    private static JsonObject workspace() {
        JsonObject workspace = new JsonObject();
        workspace.addProperty("root", WORKSPACE);
        workspace.addProperty("root_uri", "file:///work/Supersymmetry");
        workspace.addProperty("revision", REVISION);
        workspace.addProperty("dirty", false);
        workspace.add("dirty_entries", new JsonArray());
        workspace.addProperty("dirty_fingerprint", "sha256:" + "6".repeat(64));
        return workspace;
    }

    private static JsonObject absentBinding() {
        JsonObject binding = new JsonObject();
        binding.addProperty("state", "absent");
        binding.addProperty("binding_id", BINDING_ID);
        binding.addProperty(
                "path",
                STATE_ROOT + "/project-qualification-v1/bindings/"
                        + "3".repeat(64) + ".json"
        );
        binding.add("state_revision", JsonNull.INSTANCE);
        binding.add("stale_reasons", new JsonArray());
        return binding;
    }

    private static JsonArray checks(String indexState) {
        JsonArray checks = new JsonArray();
        checks.add(check(
                "profile-conformance",
                "Supersymmetry profile conformance",
                "incompatible".equals(indexState) ? "incompatible" : "ready",
                "incompatible".equals(indexState)
                        ? "Required profile evidence could not be inspected."
                        : "Required marker types, Packwiz identity, Minecraft loader, and selected profile agree."
        ));
        checks.add(check(
                "packwiz-index-integrity",
                "Packwiz index integrity",
                indexState,
                switch (indexState) {
                    case "ready" -> "The Packwiz index matches its declared SHA-256.";
                    case "attention" -> "The current Packwiz index does not match its declared SHA-256; family qualification remains available, but runtime payload integrity is separate.";
                    default -> "Unavailable until profile conformance succeeds.";
                }
        ));
        return checks;
    }

    private static JsonObject check(
            String id,
            String label,
            String state,
            String detail
    ) {
        JsonObject check = new JsonObject();
        check.addProperty("id", id);
        check.addProperty("label", label);
        check.addProperty("state", state);
        check.addProperty("detail", detail);
        return check;
    }

    private static JsonArray strings(String... values) {
        JsonArray array = new JsonArray();
        for (String value : values) {
            array.add(value);
        }
        return array;
    }
}
