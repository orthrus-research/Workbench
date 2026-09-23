package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Strict, authority-preserving projection of Workspace Home V2. */
public final class WorkspaceHomeV2 {
    public static final String FORMAT = "workbench-workspace-home-v2";
    public static final int MAX_BYTES = 16 * 1024 * 1024;
    private static final String CATALOG_FORMAT = "workbench-live-console-command-catalog-v2";
    private static final Pattern DIGEST = Pattern.compile("^sha256:[0-9a-f]{64}$");
    private static final Pattern HOME_ID = Pattern.compile(
            "^workspace-home:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern WORKSPACE_ID = Pattern.compile(
            "^workspace:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern OWNER_REF_ID = Pattern.compile(
            "^owner-ref:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern SESSION_ID = Pattern.compile(
            "^work-session-v2-[0-9a-f]{32}$"
    );
    private static final Pattern SESSION_RECORD_ID = Pattern.compile(
            "^work-session-record:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern CAPABILITY_CATALOG_ID = Pattern.compile(
            "^workbench-product-capabilities:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern BINDING_ID = Pattern.compile(
            "^workspace-home-binding:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern CAPABILITY_ID = Pattern.compile(
            "^capability:sha256:[0-9a-f]{64}$"
    );
    private static final Pattern CAPABILITY_KEY = Pattern.compile(
            "^[a-z][a-z0-9.-]+$"
    );
    private static final Pattern IDENTITY = Pattern.compile(
            "^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,255}$"
    );
    private static final Set<String> FRESHNESS = Set.of(
            "current", "stale", "corrupt", "not-applicable", "unavailable"
    );
    private static final List<String> FIXTURE_TOOL_KINDS = List.of(
            "fixture-cleanup-init", "fixture-owner-lock", "fixture-owner-schema",
            "gradle-executable", "java-executable", "java-release",
            "profile-preflight-tool"
    );
    private static final Set<String> FIXTURE_TOOL_KIND_SET = Set.copyOf(FIXTURE_TOOL_KINDS);
    private static final Set<String> NEW_PROJECT_UNAVAILABLE_BLOCKERS = Set.of(
            "NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE",
            "OWNER_ADMITTED_NEW_KIND_ABSENT"
    );

    private final String rawJson;
    private final String homeId;
    private final Workspace workspace;
    private final Status status;
    private final Map<String, JsonElement> context;
    private final List<OwnerReference> ownerRecords;
    private final OwnerProjection session;
    private final OwnerProjection capabilityCatalog;
    private final String catalogDigest;
    private final List<Job> jobs;
    private final NewProject newProject;
    private final Adoption adoption;
    private final List<Problem> problems;
    private final List<String> limitations;

    private WorkspaceHomeV2(
            @NotNull String rawJson,
            @NotNull String homeId,
            @NotNull Workspace workspace,
            @NotNull Status status,
            @NotNull Map<String, JsonElement> context,
            @NotNull List<OwnerReference> ownerRecords,
            @NotNull OwnerProjection session,
            @NotNull OwnerProjection capabilityCatalog,
            @NotNull String catalogDigest,
            @NotNull List<Job> jobs,
            @NotNull NewProject newProject,
            @NotNull Adoption adoption,
            @NotNull List<Problem> problems,
            @NotNull List<String> limitations
    ) {
        this.rawJson = rawJson;
        this.homeId = homeId;
        this.workspace = workspace;
        this.status = status;
        this.context = Map.copyOf(context);
        this.ownerRecords = List.copyOf(ownerRecords);
        this.session = session;
        this.capabilityCatalog = capabilityCatalog;
        this.catalogDigest = catalogDigest;
        this.jobs = List.copyOf(jobs);
        this.newProject = newProject;
        this.adoption = adoption;
        this.problems = List.copyOf(problems);
        this.limitations = List.copyOf(limitations);
    }

    public static @NotNull WorkspaceHomeV2 parse(@NotNull String json) {
        JsonObject root = ProductSpineJson.parse(
                json, "Workspace Home V2", MAX_BYTES, 250_000
        );
        ProductSpineJson.exact(root, "Workspace Home V2",
                "format", "schema_version", "home_id", "operation", "read_only",
                "local_state_effect", "workspace", "status", "freshness", "owner_records",
                "session", "capability_catalog", "jobs", "catalog", "new_project", "adoption",
                "problems", "base_home", "limitations");
        ProductSpineJson.require(FORMAT.equals(ProductSpineJson.string(
                        root, "format", "Workspace Home V2")),
                "Workbench returned an unsupported Workspace Home V2 format");
        ProductSpineJson.require(ProductSpineJson.integer(
                        root, "schema_version", "Workspace Home V2", 2, 2) == 2,
                "Workbench returned an unsupported Workspace Home V2 schema");
        ProductSpineJson.require(ProductSpineJson.bool(root, "read_only", "Workspace Home V2"),
                "Workspace Home V2 is not read-only");
        String operation = ProductSpineJson.member(root, "operation", "Workspace Home V2",
                Set.of("open", "adopt", "reopen"));
        String localStateEffect = ProductSpineJson.member(
                root, "local_state_effect", "Workspace Home V2",
                Set.of("none", "adoption-binding-created")
        );
        ProductSpineJson.require(localStateEffect.equals(
                        "adopt".equals(operation) ? "adoption-binding-created" : "none"),
                "Workspace Home V2 local state effect is inconsistent");
        String homeId = ProductSpineJson.patterned(
                root, "home_id", "Workspace Home V2", HOME_ID
        );
        CanonicalJson.verifyObjectIdentity(root, "home_id", "workspace-home", true);

        JsonObject baseRoot = ProductSpineJson.object(root, "base_home", "Workspace Home V2");
        WorkspaceHome baseHome = WorkspaceHome.parse(baseRoot.toString());
        JsonObject workspaceRow = ProductSpineJson.object(root, "workspace", "Workspace Home V2");
        ProductSpineJson.exact(workspaceRow, "Workspace Home V2 workspace",
                "requested_path", "root", "display_name", "kind", "recognition",
                "workspace_id", "workspace_revision", "source_revision", "dirty_fingerprint");
        String workspaceId = ProductSpineJson.patterned(
                workspaceRow, "workspace_id", "Workspace Home V2 workspace", WORKSPACE_ID
        );
        String workspaceRoot = ProductSpineJson.string(
                workspaceRow, "root", "Workspace Home V2 workspace"
        );
        String workspaceKind = ProductSpineJson.string(
                workspaceRow, "kind", "Workspace Home V2 workspace"
        );
        JsonObject workspaceIdentity = new JsonObject();
        workspaceIdentity.addProperty("root", workspaceRoot);
        workspaceIdentity.addProperty("kind", workspaceKind);
        ProductSpineJson.require(workspaceId.equals(CanonicalJson.contentId(
                        "workspace", workspaceIdentity, true)),
                "Workspace Home V2 workspace identity changed");
        ProductSpineJson.require(workspaceRoot.equals(baseHome.workspace().root())
                        && workspaceKind.equals(baseHome.workspace().kind()),
                "Workspace Home V2 changes its V1 workspace identity");
        Workspace workspace = new Workspace(
                ProductSpineJson.string(workspaceRow, "requested_path", "Workspace Home V2 workspace"),
                workspaceRoot,
                ProductSpineJson.string(workspaceRow, "display_name", "Workspace Home V2 workspace"),
                workspaceKind,
                ProductSpineJson.member(workspaceRow, "recognition", "Workspace Home V2 workspace",
                        Set.of("exact", "bounded")),
                workspaceId,
                ProductSpineJson.patterned(workspaceRow, "workspace_revision",
                        "Workspace Home V2 workspace", DIGEST),
                ProductSpineJson.string(workspaceRow, "source_revision", "Workspace Home V2 workspace"),
                ProductSpineJson.patterned(workspaceRow, "dirty_fingerprint",
                        "Workspace Home V2 workspace", DIGEST)
        );

        JsonObject statusRow = ProductSpineJson.object(root, "status", "Workspace Home V2");
        ProductSpineJson.exact(statusRow, "Workspace Home V2 status",
                "state", "base_home_state", "blockers", "warnings", "information");
        Status status = new Status(
                ProductSpineJson.member(statusRow, "state", "Workspace Home V2 status",
                        Set.of("ready", "attention", "blocked")),
                ProductSpineJson.member(statusRow, "base_home_state", "Workspace Home V2 status",
                        Set.of("ready", "attention", "blocked")),
                ProductSpineJson.integer(statusRow, "blockers", "Workspace Home V2 status", 0, Integer.MAX_VALUE),
                ProductSpineJson.integer(statusRow, "warnings", "Workspace Home V2 status", 0, Integer.MAX_VALUE),
                ProductSpineJson.integer(statusRow, "information", "Workspace Home V2 status", 0, Integer.MAX_VALUE)
        );
        ProductSpineJson.require(status.baseHomeState().equals(baseHome.status()),
                "Workspace Home V2 changes its V1 status");

        JsonObject freshness = ProductSpineJson.object(root, "freshness", "Workspace Home V2");
        ProductSpineJson.exact(freshness, "Workspace Home V2 freshness",
                "workspace", "adoption", "session", "capability_catalog");
        String workspaceFreshness = ProductSpineJson.member(
                freshness, "workspace", "Workspace Home V2 freshness", FRESHNESS
        );
        ProductSpineJson.require("current".equals(workspaceFreshness),
                "Workspace Home V2 workspace is not current");
        String adoptionFreshness = ProductSpineJson.member(
                freshness, "adoption", "Workspace Home V2 freshness", FRESHNESS
        );
        String sessionFreshness = ProductSpineJson.member(
                freshness, "session", "Workspace Home V2 freshness", FRESHNESS
        );
        String capabilityCatalogFreshness = ProductSpineJson.member(
                freshness, "capability_catalog", "Workspace Home V2 freshness", FRESHNESS
        );

        JsonArray ownerRows = ProductSpineJson.array(
                root, "owner_records", "Workspace Home V2", 256
        );
        ProductSpineJson.require(ownerRows.size() >= 1,
                "Workspace Home V2 has no owner record references");
        List<OwnerReference> ownerRecords = new ArrayList<>();
        Map<String, OwnerReference> ownerById = new HashMap<>();
        for (int index = 0; index < ownerRows.size(); index++) {
            OwnerReference owner = ownerReference(
                    ProductSpineJson.object(ownerRows.get(index), "Workspace Home V2 owner reference"),
                    index
            );
            ProductSpineJson.require(ownerById.put(owner.id(), owner) == null,
                    "Workspace Home V2 repeats an owner reference identity");
            ownerRecords.add(owner);
        }
        Comparator<OwnerReference> ownerOrder = Comparator.comparing(OwnerReference::kind)
                .thenComparing(OwnerReference::ownerId).thenComparing(OwnerReference::id);
        for (int index = 1; index < ownerRecords.size(); index++) {
            ProductSpineJson.require(ownerOrder.compare(
                            ownerRecords.get(index - 1), ownerRecords.get(index)) <= 0,
                    "Workspace Home V2 owner references are not in owner order");
        }

        OwnerProjection session = ownerProjection(
                ProductSpineJson.object(root, "session", "Workspace Home V2"),
                "Workspace Home V2 session", "work-session", ownerById
        );
        OwnerProjection capabilityCatalog = ownerProjection(
                ProductSpineJson.object(root, "capability_catalog", "Workspace Home V2"),
                "Workspace Home V2 capability catalog", "product-capability-catalog", ownerById
        );
        ProductSpineJson.require(session.freshness().equals(sessionFreshness)
                        && capabilityCatalog.freshness().equals(capabilityCatalogFreshness),
                "Workspace Home V2 owner freshness differs across projections");

        JsonObject catalog = ProductSpineJson.object(root, "catalog", "Workspace Home V2");
        ProductSpineJson.exact(catalog, "Workspace Home V2 catalog",
                "format_version", "catalog_digest");
        ProductSpineJson.require(CATALOG_FORMAT.equals(ProductSpineJson.string(
                        catalog, "format_version", "Workspace Home V2 catalog")),
                "Workspace Home V2 catalog format changed");
        String catalogDigest = ProductSpineJson.patterned(
                catalog, "catalog_digest", "Workspace Home V2 catalog", DIGEST
        );

        JsonArray jobRows = ProductSpineJson.array(root, "jobs", "Workspace Home V2", 5);
        ProductSpineJson.require(jobRows.size() >= 1,
                "Workspace Home V2 must expose one to five jobs");
        List<Job> jobs = new ArrayList<>();
        Set<String> jobIds = new HashSet<>();
        for (int index = 0; index < jobRows.size(); index++) {
            Job job = job(
                    ProductSpineJson.object(jobRows.get(index), "Workspace Home V2 job"),
                    index, workspace.workspaceRevision(), capabilityCatalog,
                    catalogDigest, ownerById
            );
            ProductSpineJson.require(jobIds.add(job.id()),
                    "Workspace Home V2 repeats job " + job.id());
            if (!jobs.isEmpty()) {
                Job previous = jobs.getLast();
                ProductSpineJson.require(previous.rank() < job.rank()
                                || (previous.rank() == job.rank()
                                && previous.id().compareTo(job.id()) <= 0),
                        "Workspace Home V2 job order changed");
            }
            jobs.add(job);
        }

        NewProject newProject = validateNewProject(
                ProductSpineJson.object(root, "new_project", "Workspace Home V2"),
                ownerById
        );
        Adoption adoption = validateAdoption(
                ProductSpineJson.object(root, "adoption", "Workspace Home V2"),
                operation, adoptionFreshness, session.sessionId(), workspace
        );
        List<Problem> problems = problems(root);
        List<String> limitations = ProductSpineJson.strings(
                root, "limitations", "Workspace Home V2", 256
        );
        JsonObject contextRow = ProductSpineJson.object(baseRoot, "context", "Workspace Home V1");
        Map<String, JsonElement> context = new HashMap<>();
        for (Map.Entry<String, JsonElement> entry : contextRow.entrySet()) {
            context.put(entry.getKey(), entry.getValue().deepCopy());
        }
        return new WorkspaceHomeV2(
                json, homeId, workspace, status, context, ownerRecords, session,
                capabilityCatalog, catalogDigest, jobs, newProject, adoption, problems,
                limitations
        );
    }

    private static @NotNull OwnerReference ownerReference(
            @NotNull JsonObject row, int index
    ) {
        String label = "Workspace Home V2 owner reference " + index;
        ProductSpineJson.exact(row, label,
                "id", "kind", "owner_id", "record_format", "record_id", "session_id",
                "catalog_id", "record_revision", "record_digest",
                "bound_workspace_revision", "freshness", "integrity", "validation_problem");
        String id = ProductSpineJson.patterned(row, "id", label, OWNER_REF_ID);
        String kind = ProductSpineJson.patterned(row, "kind", label, IDENTITY);
        String ownerId = ProductSpineJson.patterned(row, "owner_id", label, IDENTITY);
        String digest = ProductSpineJson.patterned(row, "record_digest", label, DIGEST);
        ProductSpineJson.require(digest.equals(ProductSpineJson.patterned(
                        row, "record_revision", label, DIGEST)),
                "Workspace Home V2 owner record revision differs from its digest");
        JsonObject identity = new JsonObject();
        identity.addProperty("kind", kind);
        identity.addProperty("owner_id", ownerId);
        identity.addProperty("record_digest", digest);
        ProductSpineJson.require(id.equals(CanonicalJson.contentId("owner-ref", identity, true)),
                "Workspace Home V2 owner reference identity changed");
        String freshness = ProductSpineJson.member(
                row, "freshness", label, Set.of("current", "stale", "corrupt", "not-applicable")
        );
        String integrity = ProductSpineJson.member(
                row, "integrity", label, Set.of("verified", "failed")
        );
        ProductSpineJson.require(("corrupt".equals(freshness)) == ("failed".equals(integrity)),
                "Workspace Home V2 corrupt owner state is inconsistent");
        String recordId = ProductSpineJson.nullableString(row, "record_id", label);
        String sessionId = ProductSpineJson.nullablePatterned(
                row, "session_id", label, SESSION_ID
        );
        String capabilityCatalogId = ProductSpineJson.nullablePatterned(
                row, "catalog_id", label, CAPABILITY_CATALOG_ID
        );
        String validationProblem = ProductSpineJson.nullableString(
                row, "validation_problem", label
        );
        ProductSpineJson.require(validationProblem == null
                        || "OWNER_VALIDATION_FAILED".equals(validationProblem),
                "Workspace Home V2 owner validation problem is unsupported");
        if (validationProblem != null) {
            ProductSpineJson.require("corrupt".equals(freshness)
                            && "failed".equals(integrity) && recordId == null
                            && sessionId == null && capabilityCatalogId == null,
                    "Workspace Home V2 rejected owner record was not isolated");
        } else if ("work-session".equals(kind)) {
            ProductSpineJson.require(recordId != null
                            && SESSION_RECORD_ID.matcher(recordId).matches()
                            && sessionId != null && capabilityCatalogId == null,
                    "Workspace Home V2 Work Session owner identities changed");
        } else if ("product-capability-catalog".equals(kind)) {
            ProductSpineJson.require(capabilityCatalogId != null
                            && capabilityCatalogId.equals(recordId)
                            && sessionId == null,
                    "Workspace Home V2 capability catalog identity changed");
        } else {
            ProductSpineJson.require(sessionId == null && capabilityCatalogId == null,
                    "Workspace Home V2 unrelated owner carries a reserved identity");
        }
        return new OwnerReference(
                id, kind, ownerId, ProductSpineJson.string(row, "record_format", label),
                recordId, sessionId, capabilityCatalogId, digest,
                ProductSpineJson.nullablePatterned(
                        row, "bound_workspace_revision", label, DIGEST), freshness, integrity,
                validationProblem
        );
    }

    private static @NotNull OwnerProjection ownerProjection(
            @NotNull JsonObject row,
            @NotNull String label,
            @NotNull String expectedKind,
            @NotNull Map<String, OwnerReference> ownerById
    ) {
        ProductSpineJson.exact(row, label,
                "state", "owner_ref_id", "record_id", "session_id", "catalog_id",
                "record_revision", "freshness", "reason");
        String state = ProductSpineJson.member(row, "state", label,
                Set.of("available", "unavailable"));
        String ownerRefId = ProductSpineJson.nullablePatterned(
                row, "owner_ref_id", label, OWNER_REF_ID
        );
        String recordId = ProductSpineJson.nullableString(row, "record_id", label);
        String sessionId = ProductSpineJson.nullablePatterned(row, "session_id", label, SESSION_ID);
        String capabilityCatalogId = ProductSpineJson.nullablePatterned(
                row, "catalog_id", label, CAPABILITY_CATALOG_ID
        );
        String revision = ProductSpineJson.nullablePatterned(
                row, "record_revision", label, DIGEST
        );
        String freshness = ProductSpineJson.member(row, "freshness", label, FRESHNESS);
        String reason = ProductSpineJson.nullableString(row, "reason", label);
        if (ownerRefId == null) {
            ProductSpineJson.require("unavailable".equals(state)
                            && "unavailable".equals(freshness) && recordId == null
                            && sessionId == null && capabilityCatalogId == null && revision == null
                            && reason != null,
                    label + " absent owner state is inconsistent");
        } else {
            OwnerReference reference = ownerById.get(ownerRefId);
            ProductSpineJson.require(reference != null
                            && expectedKind.equals(reference.kind())
                            && reference.recordRevision().equals(revision)
                            && reference.freshness().equals(freshness)
                            && java.util.Objects.equals(reference.recordId(), recordId)
                            && java.util.Objects.equals(reference.sessionId(), sessionId)
                            && java.util.Objects.equals(
                            reference.capabilityCatalogId(), capabilityCatalogId),
                    label + " differs from its exact owner reference");
            boolean shouldBeAvailable = Set.of("current", "not-applicable").contains(freshness);
            ProductSpineJson.require(("available".equals(state)) == shouldBeAvailable
                            && (shouldBeAvailable ? reason == null : reason != null),
                    label + " availability differs from owner freshness");
            if ("work-session".equals(expectedKind)) {
                ProductSpineJson.require(sessionId != null && capabilityCatalogId == null
                                && recordId != null && SESSION_RECORD_ID.matcher(recordId).matches(),
                        label + " omits or changes the owner-returned session identity");
            } else {
                ProductSpineJson.require(capabilityCatalogId != null && sessionId == null
                                && capabilityCatalogId.equals(recordId),
                        label + " omits or changes the owner-returned catalog identity");
            }
        }
        return new OwnerProjection(
                state, ownerRefId, recordId, sessionId, capabilityCatalogId,
                revision, freshness, reason
        );
    }

    private static @NotNull Job job(
            @NotNull JsonObject row,
            int index,
            @NotNull String workspaceRevision,
            @NotNull OwnerProjection capabilityCatalog,
            @NotNull String catalogDigest,
            @NotNull Map<String, OwnerReference> ownerById
    ) {
        String label = "Workspace Home V2 job " + index;
        ProductSpineJson.exact(row, label,
                "id", "rank", "title", "purpose", "state", "argv", "blockers",
                "unavailable_reason", "owner_ref_ids", "eligibility_binding", "command_id",
                "catalog_digest", "action_digest", "capability_id", "capability_key",
                "capability", "availability_basis", "tool_inputs", "next_safe_action",
                "arguments", "eligibility_digest");
        String id = ProductSpineJson.patterned(row, "id", label, IDENTITY);
        int rank = ProductSpineJson.integer(row, "rank", label, 0, Integer.MAX_VALUE);
        String state = ProductSpineJson.member(row, "state", label,
                Set.of("available", "unavailable"));
        List<String> blockers = ProductSpineJson.strings(row, "blockers", label, 256);
        ProductSpineJson.require(blockers.equals(blockers.stream().distinct().sorted().toList()),
                label + " blockers are not canonical");
        List<String> ownerRefIds = ProductSpineJson.strings(row, "owner_ref_ids", label, 256);
        ProductSpineJson.require(ownerRefIds.equals(ownerRefIds.stream().distinct().sorted().toList())
                        && ownerRefIds.stream().allMatch(ownerById::containsKey),
                label + " owner references are inconsistent");
        JsonObject binding = ProductSpineJson.object(row, "eligibility_binding", label);
        ProductSpineJson.exact(binding, label + " eligibility binding",
                "workspace_revision", "session_revision", "capability_catalog_revision");
        String expectedSessionRevision = ownerRefIds.stream().map(ownerById::get)
                .filter(reference -> "work-session".equals(reference.kind()))
                .map(OwnerReference::recordRevision).findFirst().orElse(null);
        String expectedCapabilityCatalogRevision = ownerRefIds.stream().map(ownerById::get)
                .filter(reference -> "product-capability-catalog".equals(reference.kind()))
                .map(OwnerReference::recordRevision).findFirst().orElse(null);
        ProductSpineJson.require(workspaceRevision.equals(ProductSpineJson.patterned(
                        binding, "workspace_revision", label, DIGEST))
                        && java.util.Objects.equals(expectedSessionRevision, ProductSpineJson.nullablePatterned(
                        binding, "session_revision", label, DIGEST))
                        && java.util.Objects.equals(expectedCapabilityCatalogRevision,
                        ProductSpineJson.nullablePatterned(
                        binding, "capability_catalog_revision", label, DIGEST)),
                label + " eligibility identity changed");
        String commandId = ProductSpineJson.nullablePatterned(row, "command_id", label, IDENTITY);
        String actionDigest = ProductSpineJson.nullablePatterned(row, "action_digest", label, DIGEST);
        ProductSpineJson.require((commandId == null) == (actionDigest == null),
                label + " has a partial catalog identity");
        String capabilityId = ProductSpineJson.nullablePatterned(
                row, "capability_id", label, IDENTITY
        );
        String capabilityKey = ProductSpineJson.nullablePatterned(
                row, "capability_key", label, IDENTITY
        );
        JsonElement capabilityValue = ProductSpineJson.required(row, "capability", label);
        JsonObject capability = null;
        if (capabilityValue.isJsonNull()) {
            ProductSpineJson.require(capabilityId == null && capabilityKey == null,
                    label + " has a partial product capability identity");
        } else {
            ProductSpineJson.require(capabilityId != null && capabilityKey != null
                            && commandId != null && actionDigest != null,
                    label + " has a partial product capability");
            capability = ProductSpineJson.object(capabilityValue, label + " capability");
            validateCapability(capability, commandId, actionDigest, label + " capability");
            ProductSpineJson.require(capabilityId.equals(capability.get("capability_id").getAsString())
                            && capabilityKey.equals(capability.get("capability_key").getAsString()),
                    label + " product capability identity changed");
            ProductSpineJson.require(ownerRefIds.stream().map(ownerById::get)
                            .anyMatch(reference ->
                                    "product-capability-catalog".equals(reference.kind())),
                    label + " capability lost its exact catalog owner reference");
        }
        JsonElement arguments = ProductSpineJson.required(row, "arguments", label);
        ProductSpineJson.require(
                commandId == null ? arguments.isJsonNull() : arguments.isJsonObject(),
                label + " catalog arguments are inconsistent"
        );
        JsonObject parsedArguments = arguments.isJsonNull()
                ? null : ProductSpineJson.object(arguments, label + " arguments");
        if ("cleanroom-fixture-build".equals(id) && parsedArguments != null) {
            ProductSpineJson.exact(parsedArguments, label + " Cleanroom fixture arguments",
                    "gradle_cmd", "java_home", "expected_input_digest", "state_root");
            ProductSpineJson.nullableString(
                    parsedArguments, "gradle_cmd", label + " Cleanroom fixture arguments"
            );
            ProductSpineJson.nullableString(
                    parsedArguments, "java_home", label + " Cleanroom fixture arguments"
            );
            ProductSpineJson.nullablePatterned(
                    parsedArguments, "expected_input_digest",
                    label + " Cleanroom fixture arguments", DIGEST
            );
            ProductSpineJson.string(
                    parsedArguments, "state_root", label + " Cleanroom fixture arguments"
            );
        }
        JsonArray toolRows = ProductSpineJson.array(row, "tool_inputs", label, 256);
        List<ToolInput> toolInputs = new ArrayList<>();
        for (int toolIndex = 0; toolIndex < toolRows.size(); toolIndex++) {
            String toolLabel = label + " tool input " + toolIndex;
            JsonObject tool = ProductSpineJson.object(toolRows.get(toolIndex), toolLabel);
            ProductSpineJson.exact(tool, toolLabel, "kind", "path", "sha256", "size", "mode");
            String kind = ProductSpineJson.string(tool, "kind", toolLabel);
            ProductSpineJson.require(FIXTURE_TOOL_KIND_SET.contains(kind),
                    toolLabel + " kind is unsupported");
            toolInputs.add(new ToolInput(
                    kind,
                    ProductSpineJson.string(tool, "path", toolLabel),
                    ProductSpineJson.patterned(tool, "sha256", toolLabel, DIGEST),
                    ProductSpineJson.integer(tool, "size", toolLabel, 0, Integer.MAX_VALUE),
                    ProductSpineJson.integer(tool, "mode", toolLabel, 0, 07777)
            ));
        }
        List<String> toolKeys = toolInputs.stream()
                .map(tool -> tool.kind() + "\0" + tool.path()).toList();
        ProductSpineJson.require(toolKeys.equals(toolKeys.stream().distinct().sorted().toList()),
                label + " tool inputs are not in exact owner order");
        String nextSafeAction = ProductSpineJson.nullableString(
                row, "next_safe_action", label
        );
        ProductSpineJson.require("cleanroom-fixture-build".equals(id)
                        || (toolInputs.isEmpty() && nextSafeAction == null),
                label + " invented Cleanroom fixture tool custody");
        AvailabilityBasis availabilityBasis = availabilityBasis(
                ProductSpineJson.object(row, "availability_basis", label), label,
                id, commandId, actionDigest, capabilityId, capability, workspaceRevision,
                capabilityCatalog, ownerById
        );
        ProductSpineJson.require(availabilityBasis.ownerRefId() == null
                        || ownerRefIds.contains(availabilityBasis.ownerRefId()),
                label + " availability basis lost its exact owner reference");
        boolean globallyExecutable = capability != null
                && !"unavailable".equals(capability.get("availability").getAsString())
                && capability.getAsJsonObject("handler").get("registered").getAsBoolean()
                && capability.getAsJsonObject("handler").get("executable").getAsBoolean()
                && actionDigest.equals(capability.getAsJsonObject("catalog_action")
                .get("action_digest").getAsString());
        ProductSpineJson.require(!"available".equals(state)
                        || "owner-context-resolution".equals(availabilityBasis.kind())
                        || globallyExecutable,
                "available " + label + " was not authorized by its exact availability basis");
        if ("available".equals(state) && "cleanroom-fixture-build".equals(id)) {
            ProductSpineJson.require(toolInputs.stream().map(ToolInput::kind).toList()
                            .equals(FIXTURE_TOOL_KINDS) && nextSafeAction == null,
                    label + " executable fixture tool custody is incomplete");
        }
        ProductSpineJson.require(catalogDigest.equals(ProductSpineJson.patterned(
                        row, "catalog_digest", label, DIGEST)),
                label + " catalog identity changed");
        List<String> argv = null;
        String reason = null;
        JsonElement argvValue = ProductSpineJson.required(row, "argv", label);
        if ("available".equals(state)) {
            argv = ProductSpineJson.strings(row, "argv", label, 256);
            boolean staleDependency = ownerRefIds.stream()
                    .map(ownerById::get)
                    .anyMatch(reference -> Set.of("stale", "corrupt").contains(reference.freshness()));
            ProductSpineJson.require(!argv.isEmpty() && blockers.isEmpty()
                            && ProductSpineJson.required(row, "unavailable_reason", label).isJsonNull()
                            && commandId != null && !staleDependency,
                    "available " + label + " depends on unavailable owner state");
        } else {
            reason = ProductSpineJson.string(row, "unavailable_reason", label);
            ProductSpineJson.require(argvValue.isJsonNull() && !blockers.isEmpty(),
                    "unavailable " + label + " was made executable");
        }
        ProductSpineJson.patterned(row, "eligibility_digest", label, DIGEST);
        CanonicalJson.verifyObjectIdentity(row, "eligibility_digest", "", true);
        return new Job(
                id, rank, ProductSpineJson.string(row, "title", label),
                ProductSpineJson.string(row, "purpose", label), state, argv,
                blockers, reason, ownerRefIds, commandId, catalogDigest, actionDigest,
                capabilityId, capabilityKey, capability,
                availabilityBasis, toolInputs, nextSafeAction,
                parsedArguments == null ? null : parsedArguments.deepCopy(),
                ProductSpineJson.string(row, "eligibility_digest", label)
        );
    }

    private static @NotNull AvailabilityBasis availabilityBasis(
            @NotNull JsonObject row,
            @NotNull String label,
            @NotNull String jobId,
            @Nullable String commandId,
            @Nullable String actionDigest,
            @Nullable String capabilityId,
            @Nullable JsonObject capability,
            @NotNull String workspaceRevision,
            @NotNull OwnerProjection capabilityCatalog,
            @NotNull Map<String, OwnerReference> ownerById
    ) {
        String basisLabel = label + " availability basis";
        ProductSpineJson.exact(row, basisLabel,
                "kind", "scope", "owner_ref_id", "owner_record_revision",
                "command_id", "capability_id", "global_capability_effect");
        AvailabilityBasis parsed = new AvailabilityBasis(
                ProductSpineJson.member(row, "kind", basisLabel,
                        Set.of("base-home", "owner-context-resolution", "product-capability")),
                ProductSpineJson.member(row, "scope", basisLabel,
                        Set.of("base-home", "workspace-context", "cleanroom-fixture", "global")),
                ProductSpineJson.nullablePatterned(row, "owner_ref_id", basisLabel, OWNER_REF_ID),
                ProductSpineJson.nullablePatterned(
                        row, "owner_record_revision", basisLabel, DIGEST
                ),
                ProductSpineJson.nullablePatterned(row, "command_id", basisLabel, IDENTITY),
                ProductSpineJson.nullablePatterned(
                        row, "capability_id", basisLabel, CAPABILITY_ID
                ),
                ProductSpineJson.member(row, "global_capability_effect", basisLabel,
                        Set.of("not-applicable", "retained-unmodified", "authoritative"))
        );
        List<OwnerReference> doctors = ownerById.values().stream()
                .filter(owner -> "workspace-context".equals(owner.kind())
                        && "project-intelligence".equals(owner.ownerId()))
                .toList();
        ProductSpineJson.require(doctors.size() == 1,
                label + " lost its exact Project Intelligence owner");
        OwnerReference doctor = doctors.getFirst();
        List<OwnerReference> fixtures = ownerById.values().stream()
                .filter(owner -> "cleanroom-fixture-lock".equals(owner.kind())
                        && "cleanroom-platform-profile".equals(owner.ownerId()))
                .toList();
        ProductSpineJson.require(fixtures.size() <= 1,
                label + " has duplicate Cleanroom fixture owners");
        OwnerReference fixture = fixtures.isEmpty() ? null : fixtures.getFirst();
        OwnerReference catalogOwner = capabilityCatalog.ownerRefId() == null
                ? null : ownerById.get(capabilityCatalog.ownerRefId());
        boolean exactContextCapability = capability != null
                && actionDigest != null
                && actionDigest.equals(capability.getAsJsonObject("catalog_action")
                .get("action_digest").getAsString())
                && !"unavailable".equals(capability.get("availability").getAsString())
                && capability.getAsJsonObject("handler").get("registered").getAsBoolean()
                && capability.getAsJsonObject("handler").get("executable").getAsBoolean();
        boolean fixtureResolution = "cleanroom-fixture-build".equals(jobId)
                && "cleanroom.fixture-build".equals(commandId)
                && fixture != null && "current".equals(fixture.freshness())
                && "verified".equals(fixture.integrity())
                && fixture.validationProblem() == null
                && workspaceRevision.equals(fixture.boundWorkspaceRevision())
                && exactContextCapability;
        boolean doctorResolution = "workspace-health".equals(jobId)
                && "doctor.inspect".equals(commandId)
                && "current".equals(doctor.freshness())
                && "verified".equals(doctor.integrity())
                && doctor.validationProblem() == null
                && workspaceRevision.equals(doctor.boundWorkspaceRevision())
                && exactContextCapability;
        AvailabilityBasis expected;
        if (commandId == null) {
            expected = new AvailabilityBasis(
                    "base-home", "base-home", doctor.id(), doctor.recordRevision(),
                    null, null, "not-applicable"
            );
        } else if (fixtureResolution) {
            expected = new AvailabilityBasis(
                    "owner-context-resolution", "cleanroom-fixture", fixture.id(),
                    fixture.recordRevision(), commandId, capabilityId,
                    "retained-unmodified"
            );
        } else if (doctorResolution) {
            expected = new AvailabilityBasis(
                    "owner-context-resolution", "workspace-context", doctor.id(),
                    doctor.recordRevision(), commandId, capabilityId,
                    "retained-unmodified"
            );
        } else {
            expected = new AvailabilityBasis(
                    "product-capability", "global",
                    catalogOwner == null ? null : catalogOwner.id(),
                    catalogOwner == null ? null : catalogOwner.recordRevision(),
                    commandId, capabilityId, "authoritative"
            );
        }
        ProductSpineJson.require(parsed.equals(expected),
                label + " availability basis differs from exact owner resolution");
        return parsed;
    }

    private static void validateCapability(
            @NotNull JsonObject row,
            @NotNull String commandId,
            @NotNull String actionDigest,
            @NotNull String label
    ) {
        ProductSpineJson.exact(row, label,
                "capability_id", "capability_key", "title", "summary", "authority",
                "risk", "availability", "handler", "catalog_action", "limitations");
        ProductSpineJson.patterned(row, "capability_id", label, CAPABILITY_ID);
        ProductSpineJson.patterned(row, "capability_key", label, CAPABILITY_KEY);
        ProductSpineJson.string(row, "title", label);
        ProductSpineJson.string(row, "summary", label);
        ProductSpineJson.string(row, "authority", label);
        ProductSpineJson.member(row, "risk", label,
                Set.of("read-only", "writes-output", "mutating", "destructive"));
        String availability = ProductSpineJson.member(row, "availability", label,
                Set.of("available", "experimental", "unavailable"));
        ProductSpineJson.strings(row, "limitations", label, 256);

        JsonObject catalogAction = ProductSpineJson.object(row, "catalog_action", label);
        ProductSpineJson.exact(catalogAction, label + " catalog action",
                "action_digest", "command_id", "suite_id");
        ProductSpineJson.require(commandId.equals(ProductSpineJson.patterned(
                        catalogAction, "command_id", label, IDENTITY))
                        && actionDigest.equals(ProductSpineJson.patterned(
                        catalogAction, "action_digest", label, DIGEST)),
                label + " catalog binding changed");
        ProductSpineJson.patterned(catalogAction, "suite_id", label,
                Pattern.compile("^[a-z][a-z0-9-]+$"));

        JsonObject handler = ProductSpineJson.object(row, "handler", label);
        ProductSpineJson.exact(handler, label + " handler",
                "kind", "registered", "executable");
        String handlerKind = ProductSpineJson.member(handler, "kind", label,
                Set.of("process", "document"));
        boolean registered = ProductSpineJson.bool(handler, "registered", label);
        boolean executable = ProductSpineJson.bool(handler, "executable", label);
        ProductSpineJson.require(registered
                        && (!"document".equals(handlerKind) || !executable)
                        && (!"unavailable".equals(availability) || !executable),
                label + " handler binding changed");
    }

    private static @NotNull NewProject validateNewProject(
            @NotNull JsonObject row,
            @NotNull Map<String, OwnerReference> ownerById
    ) {
        String label = "Workspace Home V2 new-project state";
        ProductSpineJson.exact(row, label,
                "state", "admitted_kinds", "owner_ref_ids", "blockers", "reason",
                "next_safe_action");
        String state = ProductSpineJson.member(
                row, "state", label, Set.of("available", "unavailable")
        );
        List<String> admittedKinds = ProductSpineJson.strings(
                row, "admitted_kinds", label, 64
        );
        List<String> ownerRefIds = ProductSpineJson.strings(
                row, "owner_ref_ids", label, 64
        );
        List<String> blockers = ProductSpineJson.strings(row, "blockers", label, 64);
        String reason = ProductSpineJson.string(row, "reason", label);
        String nextSafeAction = ProductSpineJson.string(
                row, "next_safe_action", label
        );
        if ("unavailable".equals(state)) {
            ProductSpineJson.require(admittedKinds.isEmpty()
                            && ownerRefIds.isEmpty()
                            && blockers.size() == 1
                            && NEW_PROJECT_UNAVAILABLE_BLOCKERS.contains(blockers.getFirst()),
                    "Workspace Home V2 invented new-project construction authority");
            return new NewProject(
                    state, admittedKinds, ownerRefIds, blockers, reason, nextSafeAction
            );
        }

        ProductSpineJson.require(admittedKinds.equals(List.of(
                                "workbench-new-project-kind:cleanroom-mod"
                        ))
                        && ownerRefIds.size() == 1
                        && blockers.isEmpty()
                        && "workbench new cleanroom-mod preview --help".equals(nextSafeAction),
                "Workspace Home V2 new-project availability changed");
        OwnerReference owner = ownerById.get(ownerRefIds.getFirst());
        ProductSpineJson.require(owner != null
                        && "new-project-construction".equals(owner.kind())
                        && "cleanroom-platform-profile".equals(owner.ownerId())
                        && "workbench-cleanroom-mod-construction-owner-v2".equals(
                        owner.recordFormat())
                        && owner.recordId() != null
                        && "verified".equals(owner.integrity())
                        && Set.of("current", "not-applicable").contains(owner.freshness())
                        && owner.validationProblem() == null,
                "Workspace Home V2 new-project owner binding changed");
        return new NewProject(
                state, admittedKinds, ownerRefIds, blockers, reason, nextSafeAction
        );
    }

    private static @NotNull Adoption validateAdoption(
            @NotNull JsonObject row,
            @NotNull String operation,
            @NotNull String projectedFreshness,
            @Nullable String homeSessionId,
            @NotNull Workspace workspace
    ) {
        String label = "Workspace Home V2 adoption";
        ProductSpineJson.exact(row, label,
                "state", "binding_id", "session_id", "state_revision",
                "adopted_workspace_id", "adopted_workspace_root", "freshness",
                "stale_reasons", "recovery_state", "recovery_reasons",
                "interrupted_write_count");
        String state = ProductSpineJson.member(row, "state", label,
                Set.of("unadopted", "adopted"));
        String bindingId = ProductSpineJson.nullablePatterned(row, "binding_id", label, BINDING_ID);
        String sessionId = ProductSpineJson.nullablePatterned(row, "session_id", label, SESSION_ID);
        String stateRevision = ProductSpineJson.nullablePatterned(
                row, "state_revision", label, DIGEST
        );
        String adoptedWorkspaceId = ProductSpineJson.nullablePatterned(
                row, "adopted_workspace_id", label, WORKSPACE_ID
        );
        String adoptedWorkspaceRoot = ProductSpineJson.nullableString(
                row, "adopted_workspace_root", label
        );
        String freshness = ProductSpineJson.member(row, "freshness", label, FRESHNESS);
        List<String> staleReasons = ProductSpineJson.strings(
                row, "stale_reasons", label, 5
        );
        ProductSpineJson.require(staleReasons.equals(
                        staleReasons.stream().distinct().sorted().toList())
                        && staleReasons.stream().allMatch(Set.of(
                        "WORKSPACE_REVISION_CHANGED", "WORKSPACE_ID_CHANGED",
                        "WORKSPACE_ROOT_CHANGED", "SOURCE_REVISION_CHANGED",
                        "OWNER_RECORD_REVISIONS_CHANGED")::contains),
                "Workspace Home V2 adoption stale reasons are invalid");
        String recoveryState = ProductSpineJson.member(
                row, "recovery_state", label, Set.of("none", "required")
        );
        List<String> recoveryReasons = ProductSpineJson.strings(
                row, "recovery_reasons", label, 2
        );
        ProductSpineJson.require(recoveryReasons.equals(
                        recoveryReasons.stream().distinct().sorted().toList())
                        && recoveryReasons.stream().allMatch(Set.of(
                        "INTERRUPTED_ADOPTION_WRITE", "WORKSPACE_LOCATION_CHANGED")::contains),
                "Workspace Home V2 adoption recovery reasons are invalid");
        int interruptedWriteCount = ProductSpineJson.integer(
                row, "interrupted_write_count", label, 0, Integer.MAX_VALUE
        );
        ProductSpineJson.require(("required".equals(recoveryState))
                        == !recoveryReasons.isEmpty()
                        && (recoveryReasons.contains("INTERRUPTED_ADOPTION_WRITE")
                        == (interruptedWriteCount > 0)),
                "Workspace Home V2 adoption recovery state is inconsistent");
        ProductSpineJson.require(freshness.equals(projectedFreshness),
                "Workspace Home V2 adoption freshness changed");
        if ("open".equals(operation)) {
            ProductSpineJson.require(bindingId == null && sessionId == null
                            && stateRevision == null && adoptedWorkspaceId == null
                            && adoptedWorkspaceRoot == null && "unadopted".equals(state)
                            && "not-applicable".equals(freshness) && staleReasons.isEmpty()
                            && "none".equals(recoveryState) && recoveryReasons.isEmpty()
                            && interruptedWriteCount == 0,
                    "Read-only Workspace Home V2 open invented adoption state");
        } else {
            ProductSpineJson.require(bindingId != null && stateRevision != null
                            && "adopted".equals(state) && sessionId != null
                            && sessionId.equals(homeSessionId)
                            && adoptedWorkspaceId != null && adoptedWorkspaceRoot != null
                            && Set.of("current", "stale").contains(freshness)
                            && ("stale".equals(freshness) == !staleReasons.isEmpty()),
                    "Workspace Home V2 adoption session identity changed");
            boolean workspaceIdChanged = !workspace.workspaceId().equals(adoptedWorkspaceId);
            boolean workspaceRootChanged = !workspace.root().equals(adoptedWorkspaceRoot);
            boolean locationChanged = workspaceIdChanged || workspaceRootChanged;
            JsonObject identity = new JsonObject();
            identity.addProperty("workspace_id", adoptedWorkspaceId);
            ProductSpineJson.require(bindingId.equals(CanonicalJson.contentId(
                            "workspace-home-binding", identity, true))
                            && (staleReasons.contains("WORKSPACE_ID_CHANGED")
                            == workspaceIdChanged)
                            && (staleReasons.contains("WORKSPACE_ROOT_CHANGED")
                            == workspaceRootChanged)
                            && (recoveryReasons.contains("WORKSPACE_LOCATION_CHANGED")
                            == locationChanged),
                    "Workspace Home V2 adoption move identity is inconsistent");
            ProductSpineJson.require(!"adopt".equals(operation)
                            || ("current".equals(freshness) && staleReasons.isEmpty()
                            && !locationChanged),
                    "New Workspace Home V2 adoption cannot already be stale");
        }
        return new Adoption(
                state, bindingId, sessionId, stateRevision, adoptedWorkspaceId,
                adoptedWorkspaceRoot, freshness, staleReasons, recoveryState,
                recoveryReasons, interruptedWriteCount
        );
    }

    private static @NotNull List<Problem> problems(@NotNull JsonObject root) {
        JsonArray rows = ProductSpineJson.array(root, "problems", "Workspace Home V2", 4096);
        List<Problem> result = new ArrayList<>();
        Set<String> ids = new HashSet<>();
        for (int index = 0; index < rows.size(); index++) {
            JsonObject row = ProductSpineJson.object(rows.get(index), "Workspace Home V2 problem");
            String label = "Workspace Home V2 problem " + index;
            ProductSpineJson.exact(row, label, "id", "severity", "detail");
            Problem problem = new Problem(
                    ProductSpineJson.patterned(row, "id", label, IDENTITY),
                    ProductSpineJson.member(row, "severity", label,
                            Set.of("blocker", "warning", "info")),
                    ProductSpineJson.string(row, "detail", label)
            );
            ProductSpineJson.require(ids.add(problem.id()),
                    "Workspace Home V2 repeats problem " + problem.id());
            if (!result.isEmpty()) {
                ProductSpineJson.require(result.getLast().id().compareTo(problem.id()) <= 0,
                        "Workspace Home V2 problem order changed");
            }
            result.add(problem);
        }
        return List.copyOf(result);
    }

    public @NotNull String rawJson() { return rawJson; }
    public @NotNull String homeId() { return homeId; }
    public @NotNull Workspace workspace() { return workspace; }
    public @NotNull Status status() { return status; }
    public @NotNull Map<String, JsonElement> context() { return context; }
    public @NotNull List<OwnerReference> ownerRecords() { return ownerRecords; }
    public @NotNull OwnerProjection session() { return session; }
    public @NotNull OwnerProjection capabilityCatalog() { return capabilityCatalog; }
    public @NotNull String catalogDigest() { return catalogDigest; }
    public @NotNull List<Job> jobs() { return jobs; }
    public @NotNull NewProject newProject() { return newProject; }
    public @NotNull Adoption adoption() { return adoption; }
    public @NotNull List<Problem> problems() { return problems; }
    public @NotNull List<String> limitations() { return limitations; }

    public record Workspace(
            @NotNull String requestedPath, @NotNull String root, @NotNull String displayName,
            @NotNull String kind, @NotNull String recognition, @NotNull String workspaceId,
            @NotNull String workspaceRevision, @NotNull String sourceRevision,
            @NotNull String dirtyFingerprint
    ) { }

    public record Status(
            @NotNull String state, @NotNull String baseHomeState,
            int blockers, int warnings, int information
    ) { }

    public record OwnerReference(
            @NotNull String id, @NotNull String kind, @NotNull String ownerId,
            @NotNull String recordFormat, @Nullable String recordId,
            @Nullable String sessionId, @Nullable String capabilityCatalogId,
            @NotNull String recordRevision,
            @Nullable String boundWorkspaceRevision, @NotNull String freshness,
            @NotNull String integrity, @Nullable String validationProblem
    ) { }

    public record OwnerProjection(
            @NotNull String state, @Nullable String ownerRefId, @Nullable String recordId,
            @Nullable String sessionId, @Nullable String capabilityCatalogId,
            @Nullable String recordRevision, @NotNull String freshness,
            @Nullable String reason
    ) { }

    public record AvailabilityBasis(
            @NotNull String kind, @NotNull String scope,
            @Nullable String ownerRefId, @Nullable String ownerRecordRevision,
            @Nullable String commandId, @Nullable String capabilityId,
            @NotNull String globalCapabilityEffect
    ) { }

    public record ToolInput(
            @NotNull String kind, @NotNull String path, @NotNull String sha256,
            int size, int mode
    ) { }

    public record NewProject(
            @NotNull String state,
            @NotNull List<String> admittedKinds,
            @NotNull List<String> ownerRefIds,
            @NotNull List<String> blockers,
            @NotNull String reason,
            @NotNull String nextSafeAction
    ) {
        public NewProject {
            admittedKinds = List.copyOf(admittedKinds);
            ownerRefIds = List.copyOf(ownerRefIds);
            blockers = List.copyOf(blockers);
        }
    }

    public record Adoption(
            @NotNull String state, @Nullable String bindingId,
            @Nullable String sessionId, @Nullable String stateRevision,
            @Nullable String adoptedWorkspaceId, @Nullable String adoptedWorkspaceRoot,
            @NotNull String freshness, @NotNull List<String> staleReasons,
            @NotNull String recoveryState, @NotNull List<String> recoveryReasons,
            int interruptedWriteCount
    ) {
        public Adoption {
            staleReasons = List.copyOf(staleReasons);
            recoveryReasons = List.copyOf(recoveryReasons);
        }
    }

    public record Job(
            @NotNull String id, int rank, @NotNull String title, @NotNull String purpose,
            @NotNull String state, @Nullable List<String> argv,
            @NotNull List<String> blockers, @Nullable String unavailableReason,
            @NotNull List<String> ownerRefIds, @Nullable String commandId,
            @NotNull String catalogDigest, @Nullable String actionDigest,
            @Nullable String capabilityId, @Nullable String capabilityKey,
            @Nullable JsonObject capability,
            @NotNull AvailabilityBasis availabilityBasis,
            @NotNull List<ToolInput> toolInputs,
            @Nullable String nextSafeAction,
            @Nullable JsonObject arguments,
            @NotNull String eligibilityDigest
    ) {
        public Job {
            argv = argv == null ? null : List.copyOf(argv);
            blockers = List.copyOf(blockers);
            ownerRefIds = List.copyOf(ownerRefIds);
            capability = capability == null ? null : capability.deepCopy();
            toolInputs = List.copyOf(toolInputs);
            arguments = arguments == null ? null : arguments.deepCopy();
        }

        public boolean available() { return "available".equals(state); }
        @Override public @Nullable JsonObject capability() {
            return capability == null ? null : capability.deepCopy();
        }
        @Override public @Nullable JsonObject arguments() {
            return arguments == null ? null : arguments.deepCopy();
        }
    }

    public record Problem(
            @NotNull String id, @NotNull String severity, @NotNull String detail
    ) { }
}
