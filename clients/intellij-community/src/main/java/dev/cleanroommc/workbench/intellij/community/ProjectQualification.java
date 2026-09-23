package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict V1 qualification plan/result boundary owned by the installed core. */
final class ProjectQualification {
    static final int MAX_OUTPUT_BYTES = 2 * 1024 * 1024;

    private static final String PLAN_FORMAT = "workbench-project-qualification-plan-v1";
    private static final String RESULT_FORMAT = "workbench-project-qualification-result-v1";
    private static final String PLAN_ID_KIND = "workbench-project-qualification-plan";
    private static final Pattern PLAN_ID = Pattern.compile(
            "^workbench-project-qualification-plan:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern INSPECTION_ID = Pattern.compile(
            "^workbench-project-inspection:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern BINDING_ID = Pattern.compile(
            "^workbench-project-qualification-binding:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern STATE_REVISION = Pattern.compile(
            "^workbench-project-qualification-state:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern SHA256 = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Pattern GIT_REVISION = Pattern.compile(
            "^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
    );
    private static final Pattern CHECK_ID = Pattern.compile(
            "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$"
    );
    private static final Pattern STALE_REASON = Pattern.compile(
            "^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"
    );
    private static final Pattern WINDOWS_ABSOLUTE = Pattern.compile(
            "^[A-Za-z]:[\\\\/].+"
    );
    private static final Set<String> PLAN_FIELDS = Set.of(
            "format", "schema_version", "operation_class", "plan_id", "state",
            "can_apply", "project_id", "profile", "workspace", "inspection_id", "binding",
            "checks", "limitations", "actions", "consent"
    );
    private static final Set<String> RESULT_FIELDS = Set.of(
            "format", "schema_version", "outcome", "applied_plan_id", "binding",
            "qualification", "next_commands"
    );
    private static final Set<String> PROFILE_FIELDS = Set.of(
            "selector", "pack_profile_id", "pack_variant", "platform_profile_id",
            "selection_digest"
    );
    private static final Set<String> WORKSPACE_FIELDS = Set.of(
            "root", "root_uri", "revision", "dirty", "dirty_entries",
            "dirty_fingerprint"
    );
    private static final Set<String> BINDING_FIELDS = Set.of(
            "state", "binding_id", "path", "state_revision", "stale_reasons"
    );
    private static final Set<String> ACTION_FIELDS = Set.of(
            "id", "operation", "destination", "effect"
    );
    private static final Set<String> CHECK_FIELDS = Set.of(
            "id", "label", "state", "detail"
    );
    private static final Set<String> CONSENT_FIELDS = Set.of(
            "required", "prompt", "non_interactive"
    );
    private static final Set<String> QUALIFICATION_FIELDS = Set.of(
            "qualified", "state", "project_id", "profile", "workspace",
            "inspection_id", "checks", "limitations"
    );

    private ProjectQualification() {
    }

    record Profile(
            @NotNull String selector,
            @NotNull String packProfileId,
            @NotNull String packVariant,
            @NotNull String platformProfileId,
            @NotNull String selectionDigest
    ) {
    }

    record Workspace(
            @NotNull String root,
            @NotNull String rootUri,
            @NotNull String revision,
            boolean dirty,
            @NotNull List<String> dirtyEntries,
            @NotNull String dirtyFingerprint
    ) {
        Workspace {
            dirtyEntries = List.copyOf(dirtyEntries);
        }
    }

    record Binding(
            @NotNull String state,
            @NotNull String bindingId,
            @NotNull String path,
            @Nullable String stateRevision,
            @NotNull List<String> staleReasons
    ) {
        Binding {
            staleReasons = List.copyOf(staleReasons);
        }
    }

    record Check(
            @NotNull String id,
            @NotNull String label,
            @NotNull String state,
            @NotNull String detail
    ) {
    }

    record Action(
            @NotNull String id,
            @NotNull String operation,
            @NotNull String destination,
            @NotNull String effect
    ) {
    }

    record Plan(
            @NotNull String rawJson,
            @NotNull String planId,
            @NotNull String state,
            boolean canApply,
            @NotNull String projectId,
            @NotNull Profile profile,
            @NotNull Workspace workspace,
            @NotNull String inspectionId,
            @NotNull Binding binding,
            @NotNull List<Check> checks,
            @NotNull List<String> limitations,
            @Nullable Action action
    ) {
        Plan {
            checks = List.copyOf(checks);
            limitations = List.copyOf(limitations);
        }
    }

    record Qualification(
            boolean qualified,
            @NotNull String state,
            @NotNull String projectId,
            @NotNull Profile profile,
            @NotNull Workspace workspace,
            @NotNull String inspectionId,
            @NotNull List<Check> checks,
            @NotNull List<String> limitations
    ) {
        Qualification {
            checks = List.copyOf(checks);
            limitations = List.copyOf(limitations);
        }
    }

    record Result(
            @NotNull String rawJson,
            @NotNull String outcome,
            @NotNull String appliedPlanId,
            @NotNull Binding binding,
            @NotNull Qualification qualification,
            @NotNull List<List<String>> nextCommands
    ) {
        Result {
            List<List<String>> copied = new ArrayList<>(nextCommands.size());
            nextCommands.forEach(command -> copied.add(List.copyOf(command)));
            nextCommands = List.copyOf(copied);
        }
    }

    static @NotNull Plan parsePlan(
            @NotNull String json,
            @NotNull String expectedWorkspaceRoot
    ) {
        boundedJson(json, "project qualification plan");
        JsonObject root = RecipeReviewJson.root(
                json, "project qualification plan", 100_000
        );
        RecipeReviewJson.exactKeys(root, PLAN_FIELDS, "project qualification plan");
        if (!PLAN_FORMAT.equals(text(root, "format", "project qualification plan"))
                || RecipeReviewJson.nonnegativeInteger(
                root, "schema_version", "project qualification plan"
        ) != 1
                || !"review-before-mutation".equals(text(
                root, "operation_class", "project qualification plan"
        ))) {
            throw invalid("project qualification plan", "format is unsupported");
        }
        String planId = text(root, "plan_id", "project qualification plan");
        requirePattern(planId, PLAN_ID, "project qualification plan identity");
        String state = planState(root, "state", "project qualification plan");
        boolean canApply = RecipeReviewJson.bool(
                root, "can_apply", "project qualification plan"
        );
        if (canApply == "incompatible".equals(state)) {
            throw invalid(
                    "project qualification plan", "applicability contradicts qualification state"
            );
        }
        String projectId = text(root, "project_id", "project qualification plan");
        if (!"supersymmetry".equals(projectId)) {
            throw invalid("project qualification plan", "project authority is unsupported");
        }
        JsonObject profileJson = RecipeReviewJson.object(
                root, "profile", "project qualification plan"
        );
        Profile profile = profile(profileJson, "project qualification plan profile");
        JsonObject workspaceJson = RecipeReviewJson.object(
                root, "workspace", "project qualification plan"
        );
        Workspace workspace = workspace(
                workspaceJson, "project qualification plan workspace"
        );
        if (!workspace.root().equals(expectedWorkspaceRoot)) {
            throw invalid(
                    "project qualification plan",
                    "does not identify the exact requested workspace"
            );
        }
        String inspectionId = text(root, "inspection_id", "project qualification plan");
        requirePattern(inspectionId, INSPECTION_ID, "project inspection identity");
        JsonObject bindingJson = RecipeReviewJson.object(
                root, "binding", "project qualification plan"
        );
        Binding binding = binding(bindingJson, false, "project qualification plan binding");
        List<Check> checks = checks(
                RecipeReviewJson.array(root, "checks", "project qualification plan"),
                "project qualification plan checks"
        );
        List<String> limitations = plainTextList(
                RecipeReviewJson.array(root, "limitations", "project qualification plan"),
                64,
                "project qualification plan limitations"
        );
        requireAggregateState(state, checks, limitations, "project qualification plan");

        JsonArray actionsJson = RecipeReviewJson.array(
                root, "actions", "project qualification plan"
        );
        JsonObject actionJson = null;
        Action action = null;
        if (canApply) {
            if (actionsJson.size() != 1 || !actionsJson.get(0).isJsonObject()) {
                throw invalid("project qualification plan", "must declare exactly one action");
            }
            actionJson = actionsJson.get(0).getAsJsonObject();
            action = action(actionJson, binding, "project qualification plan action");
        } else if (!actionsJson.isEmpty()) {
            throw invalid("project qualification plan", "incompatible evidence declares an action");
        }
        consent(
                RecipeReviewJson.object(root, "consent", "project qualification plan"),
                canApply
        );
        verifyPlanIdentity(
                planId, projectId, profileJson, workspace.root(), inspectionId,
                state, bindingJson, actionJson
        );
        return new Plan(
                json, planId, state, canApply, projectId, profile, workspace, inspectionId,
                binding, checks, limitations, action
        );
    }

    static @NotNull Result parseResult(@NotNull String json, @NotNull Plan plan) {
        if (!plan.canApply() || plan.action() == null) {
            throw invalid(
                    "project qualification result",
                    "cannot exist for an incompatible qualification plan"
            );
        }
        boundedJson(json, "project qualification result");
        JsonObject root = RecipeReviewJson.root(
                json, "project qualification result", 100_000
        );
        RecipeReviewJson.exactKeys(root, RESULT_FIELDS, "project qualification result");
        if (!RESULT_FORMAT.equals(text(root, "format", "project qualification result"))
                || RecipeReviewJson.nonnegativeInteger(
                root, "schema_version", "project qualification result"
        ) != 1) {
            throw invalid("project qualification result", "format is unsupported");
        }
        String outcome = text(root, "outcome", "project qualification result");
        if (!Set.of("qualified", "requalified", "reused").contains(outcome)) {
            throw invalid("project qualification result", "outcome is unsupported");
        }
        String appliedPlanId = text(
                root, "applied_plan_id", "project qualification result"
        );
        requirePattern(appliedPlanId, PLAN_ID, "applied qualification plan identity");
        if (!plan.planId().equals(appliedPlanId)) {
            throw invalid(
                    "project qualification result", "does not apply the consented exact plan"
            );
        }
        Binding binding = binding(
                RecipeReviewJson.object(root, "binding", "project qualification result"),
                true,
                "project qualification result binding"
        );
        if (!binding.bindingId().equals(plan.binding().bindingId())
                || !binding.path().equals(plan.binding().path())) {
            throw invalid(
                    "project qualification result", "binding does not match the consented plan"
            );
        }
        JsonObject qualificationJson = RecipeReviewJson.object(
                root, "qualification", "project qualification result"
        );
        Qualification qualification = qualification(qualificationJson);
        if (!qualification.projectId().equals(plan.projectId())
                || !qualification.state().equals(plan.state())
                || !qualification.profile().equals(plan.profile())
                || !qualification.workspace().root().equals(plan.workspace().root())
                || !qualification.inspectionId().equals(plan.inspectionId())
                || !qualification.checks().equals(plan.checks())
                || !qualification.limitations().equals(plan.limitations())) {
            throw invalid(
                    "project qualification result",
                    "qualification does not match the consented exact plan"
            );
        }
        String expectedOutcome = switch (plan.action().operation()) {
            case "atomic-private-record-create" -> "qualified";
            case "atomic-private-record-replace" -> "requalified";
            case "reuse-current-binding" -> "reused";
            default -> throw new IllegalStateException("validated action became unsupported");
        };
        if (!outcome.equals(expectedOutcome)) {
            throw invalid(
                    "project qualification result", "outcome contradicts the consented action"
            );
        }
        verifyResultBindingRevision(binding, qualificationJson, outcome, plan);
        List<List<String>> nextCommands = commands(
                RecipeReviewJson.array(root, "next_commands", "project qualification result"),
                binding,
                qualification
        );
        return new Result(
                json, outcome, appliedPlanId, binding, qualification, nextCommands
        );
    }

    private static @NotNull Profile profile(
            @NotNull JsonObject value,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(value, PROFILE_FIELDS, label);
        Profile result = new Profile(
                text(value, "selector", label),
                text(value, "pack_profile_id", label),
                text(value, "pack_variant", label),
                text(value, "platform_profile_id", label),
                text(value, "selection_digest", label)
        );
        if (!"supersymmetry".equals(result.selector())
                || !"workbench-pack:supersymmetry".equals(result.packProfileId())) {
            throw invalid(label, "does not identify the requested Supersymmetry authority");
        }
        requirePattern(result.selectionDigest(), SHA256, "profile selection digest");
        return result;
    }

    private static @NotNull Workspace workspace(
            @NotNull JsonObject value,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(value, WORKSPACE_FIELDS, label);
        String root = text(value, "root", label);
        requireAbsolutePath(root, label + " root");
        String rootUri = text(value, "root_uri", label);
        requireFileUri(rootUri, root, label + " URI");
        String revision = text(value, "revision", label);
        requirePattern(revision, GIT_REVISION, "workspace Git revision");
        boolean dirty = RecipeReviewJson.bool(value, "dirty", label);
        List<String> dirtyEntries = plainTextList(
                RecipeReviewJson.array(value, "dirty_entries", label),
                100_000,
                label + " dirty entries"
        );
        if (dirty != !dirtyEntries.isEmpty()) {
            throw invalid(label, "dirty flag contradicts dirty entries");
        }
        String dirtyFingerprint = text(value, "dirty_fingerprint", label);
        requirePattern(dirtyFingerprint, SHA256, "workspace dirty fingerprint");
        return new Workspace(
                root, rootUri, revision,
                dirty,
                dirtyEntries, dirtyFingerprint
        );
    }

    private static @NotNull Binding binding(
            @NotNull JsonObject value,
            boolean requireCurrent,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(value, BINDING_FIELDS, label);
        String state = text(value, "state", label);
        if (!Set.of("absent", "current", "stale").contains(state)
                || (requireCurrent && !"current".equals(state))) {
            throw invalid(label, "state is unsupported");
        }
        String bindingId = text(value, "binding_id", label);
        requirePattern(bindingId, BINDING_ID, "qualification binding identity");
        String path = text(value, "path", label);
        requireAbsolutePath(path, label + " path");
        String stateRevision = RecipeReviewJson.nullableText(value, "state_revision", label);
        if (("absent".equals(state) && stateRevision != null)
                || (!"absent".equals(state) && stateRevision == null)) {
            throw invalid(label, "state revision contradicts binding state");
        }
        if (stateRevision != null) {
            requirePattern(stateRevision, STATE_REVISION, "qualification state revision");
        }
        List<String> staleReasons = RecipeReviewJson.textList(
                RecipeReviewJson.array(value, "stale_reasons", label), 64,
                label + " stale reasons"
        );
        for (String reason : staleReasons) {
            requirePattern(reason, STALE_REASON, "qualification stale reason");
        }
        if (("stale".equals(state) && staleReasons.isEmpty())
                || (!"stale".equals(state) && !staleReasons.isEmpty())) {
            throw invalid(label, "stale reasons contradict binding state");
        }
        return new Binding(state, bindingId, path, stateRevision, staleReasons);
    }

    private static @NotNull List<Check> checks(
            @NotNull JsonArray values,
            @NotNull String label
    ) {
        if (values.isEmpty() || values.size() > 64) {
            throw invalid(label, "must contain one to 64 checks");
        }
        List<Check> checks = new ArrayList<>(values.size());
        Set<String> ids = new HashSet<>();
        for (JsonElement value : values) {
            if (!value.isJsonObject()) {
                throw invalid(label, "contains a non-object check");
            }
            JsonObject object = value.getAsJsonObject();
            RecipeReviewJson.exactKeys(object, CHECK_FIELDS, label);
            String id = text(object, "id", label);
            requirePattern(id, CHECK_ID, "qualification check identity");
            if (!ids.add(id)) {
                throw invalid(label, "contains a duplicate check identity");
            }
            String state = checkState(object, "state", label);
            checks.add(new Check(
                    id,
                    plainText(object, "label", label),
                    state,
                    plainText(object, "detail", label)
            ));
        }
        Check conformance = checks.stream()
                .filter(check -> "profile-conformance".equals(check.id()))
                .findFirst()
                .orElseThrow(() -> invalid(label, "omits profile conformance"));
        if (checks.stream().noneMatch(check -> "packwiz-index-integrity".equals(check.id()))) {
            throw invalid(label, "omits Packwiz index integrity");
        }
        return List.copyOf(checks);
    }

    private static @NotNull Action action(
            @NotNull JsonObject value,
            @NotNull Binding binding,
            @NotNull String label
    ) {
        RecipeReviewJson.exactKeys(value, ACTION_FIELDS, label);
        String id = text(value, "id", label);
        String operation = text(value, "operation", label);
        String destination = text(value, "destination", label);
        String effect = plainText(value, "effect", label);
        if (!"persist-project-qualification".equals(id)
                || !Set.of(
                "atomic-private-record-create",
                "atomic-private-record-replace",
                "reuse-current-binding"
        ).contains(operation)) {
            throw invalid(label, "operation is unsupported");
        }
        String expected = switch (binding.state()) {
            case "absent" -> "atomic-private-record-create";
            case "stale" -> "atomic-private-record-replace";
            case "current" -> "reuse-current-binding";
            default -> throw new IllegalStateException("validated binding became unsupported");
        };
        if (!operation.equals(expected) || !destination.equals(binding.path())) {
            throw invalid(label, "does not match the binding transition");
        }
        return new Action(id, operation, destination, effect);
    }

    private static void consent(@NotNull JsonObject value, boolean canApply) {
        String label = "project qualification plan consent";
        RecipeReviewJson.exactKeys(value, CONSENT_FIELDS, label);
        boolean required = RecipeReviewJson.bool(value, "required", label);
        String prompt = RecipeReviewJson.nullableText(value, "prompt", label);
        String nonInteractive = RecipeReviewJson.nullableText(
                value, "non_interactive", label
        );
        boolean valid = canApply
                ? required
                && "Apply this qualification? [y/N]".equals(prompt)
                && "Pass this exact plan_id with --apply.".equals(nonInteractive)
                : !required && prompt == null && nonInteractive == null;
        if (!valid) {
            throw invalid(label, "does not require exact-plan consent");
        }
    }

    private static @NotNull Qualification qualification(@NotNull JsonObject value) {
        String label = "project qualification result qualification";
        RecipeReviewJson.exactKeys(value, QUALIFICATION_FIELDS, label);
        if (!RecipeReviewJson.bool(value, "qualified", label)) {
            throw invalid(label, "does not record qualification");
        }
        String state = qualificationState(value, "state", label);
        String projectId = text(value, "project_id", label);
        if (!"supersymmetry".equals(projectId)) {
            throw invalid(label, "project authority is unsupported");
        }
        Profile profile = profile(RecipeReviewJson.object(value, "profile", label), label);
        Workspace workspace = workspace(
                RecipeReviewJson.object(value, "workspace", label), label
        );
        String inspectionId = text(value, "inspection_id", label);
        requirePattern(inspectionId, INSPECTION_ID, "project inspection identity");
        List<Check> checks = checks(RecipeReviewJson.array(value, "checks", label), label);
        List<String> limitations = plainTextList(
                RecipeReviewJson.array(value, "limitations", label), 64, label
        );
        requireAggregateState(state, checks, limitations, label);
        return new Qualification(
                true, state, projectId, profile, workspace, inspectionId,
                checks, limitations
        );
    }

    private static void verifyPlanIdentity(
            @NotNull String planId,
            @NotNull String projectId,
            @NotNull JsonObject profile,
            @NotNull String workspaceRoot,
            @NotNull String inspectionId,
            @NotNull String qualificationState,
            @NotNull JsonObject binding,
            @Nullable JsonObject action
    ) {
        JsonObject identity = new JsonObject();
        identity.addProperty("project_id", projectId);
        identity.add("profile", profile.deepCopy());
        identity.addProperty("workspace_root", workspaceRoot);
        identity.addProperty("inspection_id", inspectionId);
        identity.addProperty("qualification_state", qualificationState);
        JsonObject bindingIdentity = new JsonObject();
        bindingIdentity.addProperty("state", text(
                binding, "state", "project qualification plan binding"
        ));
        bindingIdentity.addProperty("binding_id", text(
                binding, "binding_id", "project qualification plan binding"
        ));
        bindingIdentity.addProperty("path", text(
                binding, "path", "project qualification plan binding"
        ));
        String revision = RecipeReviewJson.nullableText(
                binding, "state_revision", "project qualification plan binding"
        );
        if (revision == null) {
            bindingIdentity.add("state_revision", JsonNull.INSTANCE);
        } else {
            bindingIdentity.addProperty("state_revision", revision);
        }
        identity.add("binding", bindingIdentity);
        identity.add("action", action == null ? JsonNull.INSTANCE : action.deepCopy());
        String expected = CanonicalJson.contentId(PLAN_ID_KIND, identity);
        if (!planId.equals(expected)) {
            throw invalid(
                    "project qualification plan",
                    "identity does not match its exact qualification body"
            );
        }
    }

    private static void verifyResultBindingRevision(
            @NotNull Binding binding,
            @NotNull JsonObject qualification,
            @NotNull String outcome,
            @NotNull Plan plan
    ) {
        if ("reused".equals(outcome)) {
            if (!binding.stateRevision().equals(plan.binding().stateRevision())) {
                throw invalid(
                        "project qualification result",
                        "reused binding revision does not match the consented binding"
                );
            }
            return;
        }
        JsonObject body = new JsonObject();
        body.addProperty("format", "workbench-project-qualification-binding-v1");
        body.addProperty("schema_version", 1);
        body.addProperty("binding_id", binding.bindingId());
        for (String field : new String[]{
                "project_id", "profile", "workspace", "inspection_id", "state",
                "checks", "limitations"
        }) {
            body.add(field, qualification.get(field).deepCopy());
        }
        String expected = CanonicalJson.contentId(
                "workbench-project-qualification-state", body
        );
        if (!expected.equals(binding.stateRevision())) {
            throw invalid(
                    "project qualification result", "binding state identity is invalid"
            );
        }
    }

    private static void requireAggregateState(
            @NotNull String state,
            @NotNull List<Check> checks,
            @NotNull List<String> limitations,
            @NotNull String label
    ) {
        boolean attention = checks.stream().anyMatch(check -> "attention".equals(check.state()));
        boolean incompatible = checks.stream().anyMatch(
                check -> "incompatible".equals(check.state())
        );
        String expected = incompatible ? "incompatible" : attention ? "attention" : "ready";
        if (!expected.equals(state)) {
            throw invalid(label, "state contradicts its check summary");
        }
        if (limitations.isEmpty()) {
            throw invalid(label, "has no declared limitation");
        }
        Check conformance = checks.stream()
                .filter(check -> "profile-conformance".equals(check.id()))
                .findFirst()
                .orElseThrow(() -> invalid(label, "omits profile conformance"));
        String expectedConformance = incompatible ? "incompatible" : "ready";
        if (!expectedConformance.equals(conformance.state())) {
            throw invalid(label, "profile conformance contradicts qualification state");
        }
    }

    private static @NotNull List<List<String>> commands(
            @NotNull JsonArray values,
            @NotNull Binding binding,
            @NotNull Qualification qualification
    ) {
        String label = "project qualification result next commands";
        if (values.size() != 1 || !values.get(0).isJsonArray()) {
            throw invalid(label, "must contain the one exact status command");
        }
        List<String> command = plainTextList(values.get(0).getAsJsonArray(), 9, label);
        if (command.size() != 9
                || !command.subList(0, 8).equals(List.of(
                "workbench", "project", "qualify", qualification.workspace().root(),
                "--profile", "supersymmetry", "--status", "--state-root"
        ))) {
            throw invalid(label, "does not match the exact qualification status command");
        }
        String stateRoot = command.get(8);
        requireAbsolutePath(stateRoot, label + " state root");
        String digest = binding.bindingId().substring(binding.bindingId().lastIndexOf(':') + 1);
        String expectedBindingPath = joinBoundaryPath(
                stateRoot,
                "project-qualification-v1/bindings/" + digest + ".json"
        );
        if (!normalizeBoundaryPath(binding.path()).equals(expectedBindingPath)) {
            throw invalid(label, "does not preserve the binding's effective state root");
        }
        return List.of(command);
    }

    private static @NotNull String joinBoundaryPath(
            @NotNull String root,
            @NotNull String relative
    ) {
        String normalized = normalizeBoundaryPath(root);
        while (normalized.endsWith("/") && normalized.length() > 1) {
            normalized = normalized.substring(0, normalized.length() - 1);
        }
        return ("/".equals(normalized) ? "" : normalized) + "/" + relative;
    }

    private static @NotNull String normalizeBoundaryPath(@NotNull String value) {
        return value.replace('\\', '/');
    }

    private static @NotNull List<String> plainTextList(
            @NotNull JsonArray values,
            int maximum,
            @NotNull String label
    ) {
        List<String> selected = RecipeReviewJson.textList(values, maximum, label);
        selected.forEach(value -> requirePlain(value, label));
        return selected;
    }

    private static @NotNull String qualificationState(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        String state = text(value, field, label);
        if (!Set.of("ready", "attention").contains(state)) {
            throw invalid(label, "field " + field + " is unsupported");
        }
        return state;
    }

    private static @NotNull String planState(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        String state = text(value, field, label);
        if (!Set.of("ready", "attention", "incompatible").contains(state)) {
            throw invalid(label, "field " + field + " is unsupported");
        }
        return state;
    }

    private static @NotNull String checkState(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        return planState(value, field, label);
    }

    private static @NotNull String plainText(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        String selected = text(value, field, label);
        requirePlain(selected, label + " field " + field);
        return selected;
    }

    private static void requirePlain(@NotNull String value, @NotNull String label) {
        if (value.isBlank() || value.codePoints().anyMatch(Character::isISOControl)) {
            throw invalid(label, "must contain nonempty plain text");
        }
    }

    private static @NotNull String text(
            @NotNull JsonObject value,
            @NotNull String field,
            @NotNull String label
    ) {
        String selected = RecipeReviewJson.text(value, field, label);
        if (selected.isEmpty()) {
            throw invalid(label, "field " + field + " must not be empty");
        }
        return selected;
    }

    private static void requirePattern(
            @NotNull String value,
            @NotNull Pattern pattern,
            @NotNull String label
    ) {
        if (!pattern.matcher(value).matches()) {
            throw invalid(label, "is malformed");
        }
    }

    private static void requireAbsolutePath(
            @NotNull String value,
            @NotNull String label
    ) {
        boolean absolute = value.startsWith("/")
                || WINDOWS_ABSOLUTE.matcher(value).matches()
                || value.startsWith("\\\\");
        if (!absolute || value.indexOf('\0') >= 0) {
            throw invalid(label, "is not absolute");
        }
    }

    private static void requireFileUri(
            @NotNull String value,
            @NotNull String root,
            @NotNull String label
    ) {
        try {
            URI uri = URI.create(value);
            if (!uri.isAbsolute() || !"file".equalsIgnoreCase(uri.getScheme())) {
                throw invalid(label, "is not an absolute file URI");
            }
            if (uri.getQuery() != null || uri.getFragment() != null
                    || uri.getUserInfo() != null || uri.getPort() != -1) {
                throw invalid(label, "contains unsupported URI components");
            }
            String normalizedRoot = root.replace('\\', '/');
            boolean matches;
            if (normalizedRoot.startsWith("//")) {
                int boundary = normalizedRoot.indexOf('/', 2);
                String authority = boundary < 0
                        ? normalizedRoot.substring(2)
                        : normalizedRoot.substring(2, boundary);
                String path = boundary < 0 ? "/" : normalizedRoot.substring(boundary);
                matches = uri.getAuthority() != null
                        && uri.getAuthority().equalsIgnoreCase(authority)
                        && path.equals(uri.getPath());
            } else if (WINDOWS_ABSOLUTE.matcher(root).matches()) {
                matches = (uri.getAuthority() == null || uri.getAuthority().isEmpty())
                        && ("/" + normalizedRoot).equalsIgnoreCase(uri.getPath());
            } else {
                matches = (uri.getAuthority() == null || uri.getAuthority().isEmpty())
                        && root.equals(uri.getPath());
            }
            if (!matches) {
                throw invalid(label, "does not identify the workspace root");
            }
        } catch (IllegalArgumentException error) {
            if (error.getMessage() != null && error.getMessage().startsWith("Workbench ")) {
                throw error;
            }
            throw invalid(label, "is malformed");
        }
    }

    private static void boundedJson(@NotNull String json, @NotNull String label) {
        if (json.getBytes(StandardCharsets.UTF_8).length > MAX_OUTPUT_BYTES) {
            throw invalid(label, "exceeds its byte bound");
        }
    }

    private static @NotNull IllegalArgumentException invalid(
            @NotNull String label,
            @NotNull String detail
    ) {
        return RecipeReviewJson.invalid(label, detail);
    }
}
