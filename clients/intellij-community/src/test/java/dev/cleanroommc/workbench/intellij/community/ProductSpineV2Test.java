package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

final class ProductSpineV2Test {
    @TempDir
    Path temporary;

    @Test
    void parsesLiveCoreHomeSessionTimelineRecoveryAndSixStableRegions() throws Exception {
        JsonObject bundle = liveBundle();
        WorkspaceHomeV2 home = WorkspaceHomeV2.parse(bundle.get("home").toString());
        WorkSessionV2.Status status = WorkSessionV2.parseStatus(bundle.get("status").toString());
        WorkSessionV2.Timeline timeline = WorkSessionV2.parseTimeline(
                bundle.get("timeline").toString()
        );
        WorkSessionV2.RecoveryPreview recovery = WorkSessionV2.parseRecovery(
                bundle.get("recovery").toString()
        );
        List<WorkspaceHomeV2Regions.Region> regions = WorkspaceHomeV2Regions.compose(
                home, status, timeline, recovery
        );

        assertEquals(List.of(
                "workspace", "exact-environment", "support-limitations", "active-work",
                "recovery-required", "recommended-jobs"
        ), regions.stream().map(region -> region.definition().key()).toList());
        assertEquals(home.session().sessionId(), status.sessionId());
        assertEquals(status.sessionId(), timeline.sessionId());
        assertEquals(status.sessionId(), recovery.sessionId());
        assertFalse(recovery.automatic());
        WorkspaceHomeV2Regions.Region active = regions.stream()
                .filter(region -> "active-work".equals(region.definition().key()))
                .findFirst().orElseThrow();
        assertEquals(timeline.events().size(), active.rows().stream()
                .filter(row -> row.id().startsWith("event:")).count());
        assertEquals("available", home.capabilityCatalog().state());
        assertEquals("not-applicable", home.capabilityCatalog().freshness());
        assertNotNull(home.capabilityCatalog().ownerRefId());
        WorkspaceHomeV2.Job catalogBoundJob = home.jobs().stream()
                .filter(job -> "workspace-health".equals(job.id()))
                .findFirst().orElseThrow();
        assertEquals("available", catalogBoundJob.state());
        assertEquals(List.of(), catalogBoundJob.blockers());
        assertNotNull(catalogBoundJob.capabilityId());
        assertNotNull(catalogBoundJob.capabilityKey());
        assertNotNull(catalogBoundJob.capability());
        assertEquals("owner-context-resolution", catalogBoundJob.availabilityBasis().kind());
        assertEquals("workspace-context", catalogBoundJob.availabilityBasis().scope());
        assertNotNull(catalogBoundJob.availabilityBasis().ownerRefId());
        assertEquals("retained-unmodified",
                catalogBoundJob.availabilityBasis().globalCapabilityEffect());
        WorkspaceHomeV2 fixtureHome = WorkspaceHomeV2.parse(
                bundle.get("fixture_home").toString()
        );
        WorkspaceHomeV2.Job fixtureJob = fixtureHome.jobs().stream()
                .filter(job -> "cleanroom-fixture-build".equals(job.id()))
                .findFirst().orElseThrow();
        assertEquals("cleanroom-fixture", fixtureJob.availabilityBasis().scope());
        assertFalse(fixtureJob.available());
        assertNotNull(fixtureJob.nextSafeAction());
        assertNotNull(fixtureJob.arguments());
        assertEquals(true, fixtureJob.arguments().get("gradle_cmd").isJsonNull());
        assertEquals(true, fixtureJob.arguments().get("java_home").isJsonNull());
        assertEquals(true, fixtureJob.arguments().get("expected_input_digest").isJsonNull());
        assertFalse(fixtureJob.arguments().get("state_root").getAsString().isBlank());
        assertEquals(List.of("CLEANROOM_FIXTURE_TOOL_CONFIGURATION_REQUIRED"),
                fixtureJob.blockers());
        assertNotNull(fixtureJob.capabilityId());
        assertNotNull(fixtureJob.capabilityKey());
        assertNotNull(fixtureJob.capability());
        assertEquals("unadopted", home.adoption().state());
        assertEquals(null, home.adoption().adoptedWorkspaceId());
    }

    @Test
    void retainsAndRendersOnlyExactAvailableAndUnavailableNewProjectStates()
            throws Exception {
        JsonObject available = withAvailableCleanroomConstructionOwner(
                liveHome()
        );
        WorkspaceHomeV2 parsed = WorkspaceHomeV2.parse(available.toString());
        assertEquals(1, parsed.ownerRecords().stream()
                .filter(owner -> "new-project-construction".equals(owner.kind()))
                .count());
        assertEquals("available", parsed.newProject().state());
        assertEquals(List.of("workbench-new-project-kind:cleanroom-mod"),
                parsed.newProject().admittedKinds());
        assertEquals("The Cleanroom profile owns one exact fresh-project constructor.",
                parsed.newProject().reason());
        assertEquals("workbench new cleanroom-mod preview --help",
                parsed.newProject().nextSafeAction());
        WorkspaceHomeV2Regions.Row availableRegion = WorkspaceHomeV2Regions.compose(
                        parsed, null, null, null
                ).stream()
                .filter(region -> "support-limitations".equals(region.definition().key()))
                .flatMap(region -> region.rows().stream())
                .filter(row -> "new-project".equals(row.id()))
                .findFirst().orElseThrow();
        assertEquals(
                "available · The Cleanroom profile owns one exact fresh-project constructor."
                        + " · next=workbench new cleanroom-mod preview --help",
                availableRegion.value()
        );
        assertEquals(parsed.newProject(), availableRegion.ownerValue());

        JsonObject legacyUnavailable = available.deepCopy();
        removeConstructionOwners(legacyUnavailable);
        JsonObject unavailableRow = legacyUnavailable.getAsJsonObject("new_project");
        unavailableRow.addProperty("state", "unavailable");
        unavailableRow.add("admitted_kinds", strings());
        unavailableRow.add("owner_ref_ids", strings());
        unavailableRow.add("blockers", strings("OWNER_ADMITTED_NEW_KIND_ABSENT"));
        unavailableRow.addProperty("reason", "No owner-admitted kind is available.");
        unavailableRow.addProperty(
                "next_safe_action", "Inspect the release boundary before construction."
        );
        reidentify(legacyUnavailable, "home_id", "workspace-home", true);
        WorkspaceHomeV2 parsedLegacy = WorkspaceHomeV2.parse(legacyUnavailable.toString());
        assertEquals(List.of("OWNER_ADMITTED_NEW_KIND_ABSENT"),
                parsedLegacy.newProject().blockers());

        assertNewProjectRejected(available, row ->
                row.add("admitted_kinds", strings("workbench-new-project-kind:other"))
        );
        assertNewProjectRejected(available, row -> row.add(
                "owner_ref_ids", strings("owner-ref:sha256:" + "f".repeat(64))
        ));
        assertNewProjectRejected(available, row -> row.add(
                "owner_ref_ids", strings(
                        row.getAsJsonArray("owner_ref_ids").get(0).getAsString(),
                        row.getAsJsonArray("owner_ref_ids").get(0).getAsString()
                )
        ));
        assertNewProjectRejected(available, row ->
                row.add("blockers", strings("OWNER_ADMITTED_NEW_KIND_ABSENT"))
        );
        assertNewProjectRejected(available, row -> row.addProperty("reason", ""));
        assertNewProjectRejected(available, row -> row.addProperty(
                "next_safe_action", "workbench new cleanroom-mod apply --help"
        ));
        assertNewProjectRejected(available, row -> row.addProperty("action", "preview"));

        assertConstructionOwnerRejected(available, owner ->
                owner.addProperty("kind", "other-construction")
        );
        assertConstructionOwnerRejected(available, owner ->
                owner.addProperty("owner_id", "other-platform-profile")
        );
        assertConstructionOwnerRejected(available, owner -> owner.addProperty(
                "record_format", "workbench-cleanroom-mod-construction-owner-v1"
        ));
        assertConstructionOwnerRejected(available, owner ->
                owner.add("record_id", com.google.gson.JsonNull.INSTANCE)
        );

        JsonObject currentUnavailable = legacyUnavailable.deepCopy();
        currentUnavailable.getAsJsonObject("new_project").add(
                "blockers", strings("NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE")
        );
        currentUnavailable.getAsJsonObject("new_project").addProperty(
                "reason", "The exact profile construction owner is unavailable."
        );
        currentUnavailable.getAsJsonObject("new_project").addProperty(
                "next_safe_action", "Restore the exact profile construction owner and reopen Home."
        );
        reidentify(currentUnavailable, "home_id", "workspace-home", true);
        WorkspaceHomeV2 parsedCurrent = WorkspaceHomeV2.parse(currentUnavailable.toString());
        assertEquals(List.of("NEW_PROJECT_CONSTRUCTION_OWNER_UNAVAILABLE"),
                parsedCurrent.newProject().blockers());
        WorkspaceHomeV2Regions.Row unavailableRegion = WorkspaceHomeV2Regions.compose(
                        parsedCurrent, null, null, null
                ).stream()
                .filter(region -> "support-limitations".equals(region.definition().key()))
                .flatMap(region -> region.rows().stream())
                .filter(row -> "new-project".equals(row.id()))
                .findFirst().orElseThrow();
        assertEquals(
                "unavailable · The exact profile construction owner is unavailable."
                        + " · next=Restore the exact profile construction owner and reopen Home.",
                unavailableRegion.value()
        );
        assertEquals(parsedCurrent.newProject(), unavailableRegion.ownerValue());

        JsonObject unavailableDrift = currentUnavailable.deepCopy();
        unavailableDrift.getAsJsonObject("new_project").add(
                "blockers", strings("INVENTED_CONSTRUCTION_AUTHORITY")
        );
        reidentify(unavailableDrift, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(unavailableDrift.toString()));
    }

    @Test
    void rejectsFormatProjectionJobRecoveryAndCrossSurfaceIdentityDrift() throws Exception {
        JsonObject bundle = liveBundle();

        JsonObject wrongFormat = bundle.getAsJsonObject("home").deepCopy();
        wrongFormat.addProperty("format", "workbench-workspace-home-v1");
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(wrongFormat.toString()));

        JsonObject catalogDrift = bundle.getAsJsonObject("home").deepCopy();
        catalogDrift.getAsJsonObject("capability_catalog").addProperty(
                "catalog_id", "workbench-product-capabilities:sha256:" + "f".repeat(64)
        );
        reidentify(catalogDrift, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(catalogDrift.toString()));

        JsonObject tooMany = bundle.getAsJsonObject("home").deepCopy();
        JsonArray jobs = tooMany.getAsJsonArray("jobs");
        while (jobs.size() < 6) jobs.add(jobs.get(0).deepCopy());
        reidentify(tooMany, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(tooMany.toString()));

        JsonObject executable = bundle.getAsJsonObject("home").deepCopy();
        JsonObject unavailable = null;
        for (var item : executable.getAsJsonArray("jobs")) {
            if ("unavailable".equals(item.getAsJsonObject().get("state").getAsString())) {
                unavailable = item.getAsJsonObject();
                break;
            }
        }
        assertNotNull(unavailable);
        JsonArray inventedArgv = new JsonArray();
        inventedArgv.add("python3");
        unavailable.add("argv", inventedArgv);
        reidentify(unavailable, "eligibility_digest", "", true);
        reidentify(executable, "home_id", "workspace-home", true);
        JsonObject finalExecutable = executable;
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(finalExecutable.toString()));

        JsonObject basisDrift = bundle.getAsJsonObject("home").deepCopy();
        JsonObject driftedJob = basisDrift.getAsJsonArray("jobs").get(0).getAsJsonObject();
        driftedJob.getAsJsonObject("availability_basis").addProperty(
                "owner_record_revision", "sha256:" + "f".repeat(64)
        );
        reidentify(driftedJob, "eligibility_digest", "", true);
        reidentify(basisDrift, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(basisDrift.toString()));

        JsonObject staleHome = bundle.getAsJsonObject("home").deepCopy();
        JsonObject catalogOwner = staleHome.getAsJsonObject("capability_catalog");
        catalogOwner.addProperty("freshness", "stale");
        catalogOwner.addProperty("reason", "The validated owner record is stale.");
        staleHome.getAsJsonObject("freshness").addProperty("capability_catalog", "stale");
        reidentify(staleHome, "home_id", "workspace-home", true);
        JsonObject finalStaleHome = staleHome;
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(finalStaleHome.toString()));

        JsonObject automatic = bundle.getAsJsonObject("recovery").deepCopy();
        automatic.addProperty("automatic", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkSessionV2.parseRecovery(automatic.toString()));

        JsonObject corrupt = bundle.getAsJsonObject("recovery").deepCopy();
        corrupt.addProperty("required", false);
        corrupt.getAsJsonObject("integrity").addProperty("state", "corrupt");
        corrupt.getAsJsonObject("integrity").addProperty("journal_state", "corrupt");
        assertThrows(IllegalArgumentException.class,
                () -> WorkSessionV2.parseRecovery(corrupt.toString()));

        WorkspaceHomeV2 home = WorkspaceHomeV2.parse(bundle.get("home").toString());
        JsonObject statusDrift = bundle.getAsJsonObject("status").deepCopy();
        statusDrift.addProperty("session_id", "work-session-v2-" + "f".repeat(32));
        reidentify(statusDrift, "summary_id", "work-session-summary", false);
        WorkSessionV2.Status otherStatus = WorkSessionV2.parseStatus(statusDrift.toString());
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2Regions.compose(home, otherStatus, null, null));
    }

    @Test
    void clientsBuildOnlyExactPublicJsonArgv() {
        CoreLaunch nativeLaunch = CoreLaunch.resolve("/opt/workbench/bin/workbench", false, null);
        String sessionId = "work-session-v2-" + "1".repeat(32);
        assertEquals(List.of("open", "/workspace", "--json"),
                WorkspaceHomeV2Client.arguments(nativeLaunch, "/workspace"));
        assertEquals(List.of(
                        "session", "status", sessionId,
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.statusArguments(sessionId));
        assertEquals(List.of(
                "session", "timeline", sessionId, "--after-sequence", "4",
                "--limit", "32", "--frontend", "intellij-community", "--json"
        ), WorkSessionV2Client.timelineArguments(sessionId, 4, 32));
        assertEquals(List.of(
                        "session", "recover", sessionId,
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.recoveryArguments(sessionId));
        assertEquals(List.of(
                        "session", "recover", sessionId, "--apply",
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.recoveryArguments(sessionId, true));
        assertEquals(List.of(
                        "session", "resume", sessionId, "--workspace", "/workspace",
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.resumeArguments(nativeLaunch, sessionId, "/workspace"));
        assertEquals(List.of(
                        "session", "close", sessionId,
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.closeArguments(sessionId));
        WorkSessionV2.OwnerReference owner = new WorkSessionV2.OwnerReference(
                "workbench-shell", "live-console-artifact-v1",
                "workbench-live-console-session-v1",
                "file:///tmp/live-console-artifact-v1/session-v1.json",
                "sha256:" + "a".repeat(64), "complete", "2026-08-21T12:00:00Z"
        );
        assertEquals(List.of(
                        "session", "artifact", sessionId, owner.recordId(), owner.digest(),
                        "--after-sequence", "-1", "--limit", "1024",
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.artifactEventsArguments(sessionId, owner, -1, 1024));
        assertEquals(List.of(
                        "session", "artifact", sessionId, owner.recordId(), owner.digest(),
                        "event:1",
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.artifactRangeArguments(
                        sessionId, owner, "event:1"
                ));
    }

    @Test
    void windowsHostedWslResumeMapsOnlyTheSelectedDistributionWorkspace() {
        CoreLaunch launch = CoreLaunch.resolve(
                "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
                true,
                "C:\\Windows"
        );
        String sessionId = "work-session-v2-" + "1".repeat(32);
        assertEquals(List.of(
                        "session", "resume", sessionId,
                        "--workspace", "/home/developer/workspace",
                        "--frontend", "intellij-community", "--json"),
                WorkSessionV2Client.resumeArguments(
                        launch,
                        sessionId,
                        "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace"
                ));
        assertThrows(IllegalArgumentException.class, () ->
                WorkSessionV2Client.resumeArguments(
                        launch,
                        sessionId,
                        "\\\\wsl.localhost\\Debian\\home\\developer\\workspace"
                ));
        assertThrows(IllegalArgumentException.class, () ->
                WorkSessionV2Client.resumeArguments(
                        launch,
                        sessionId,
                        "C:\\Users\\developer\\workspace"
                ));
    }

    @Test
    void intellijAdapterMutationsRetainExactFrontendIdentity() throws Exception {
        Path repository = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().resolve("../..").normalize();
        Path state = temporary.resolve("intellij-adapter-state");
        String script = """
                import pathlib, runpy, sys
                root = pathlib.Path(sys.argv[1]).resolve()
                state = pathlib.Path(sys.argv[2]).resolve()
                runpy.run_path(str(root / "tools/workbench.py"), run_name="intellij_adapter_fixture")
                from workbench_shell.work_session import WorkSessionStore
                created = WorkSessionStore(state).create(
                    task={"task_id": "task:intellij-adapter", "owner_id": "workbench-shell"},
                    workspace={
                        "identity_id": "workspace:intellij-adapter",
                        "canonical_root": str(root),
                        "source_revision": "git:intellij-adapter",
                        "dirty_fingerprint": "sha256:" + "1" * 64,
                    },
                    identities={
                        "core_id": "core:intellij-adapter",
                        "catalog_id": "sha256:" + "2" * 64,
                        "host_adapter_id": "host:native",
                        "platform_profile_id": None,
                        "pack_profile_id": None,
                    },
                    frontend={"frontend_id": "workbench-cli", "kind": "cli", "version": "0.1.0"},
                    lifecycle="discovered",
                )
                print(created["session_id"])
                """;
        Process created = new ProcessBuilder(
                "python3", "-c", script, repository.toString(), state.toString()
        ).directory(repository.toFile()).start();
        created.getOutputStream().close();
        String sessionId = new String(
                created.getInputStream().readNBytes(1024), StandardCharsets.UTF_8
        ).trim();
        String createError = new String(
                created.getErrorStream().readNBytes(1024 * 1024), StandardCharsets.UTF_8
        );
        assertEquals(true, created.waitFor(120, TimeUnit.SECONDS));
        assertEquals(0, created.exitValue(), createError);

        CoreLaunch nativeLaunch = CoreLaunch.resolve("python3", false, null);
        WorkSessionV2.parseStatus(invokeAdapter(
                repository, state,
                WorkSessionV2Client.resumeArguments(
                        nativeLaunch, sessionId, repository.toString()
                )
        ));
        WorkSessionV2.parseStatus(invokeAdapter(
                repository, state, WorkSessionV2Client.closeArguments(sessionId)
        ));
        WorkSessionV2.Timeline timeline = WorkSessionV2.parseTimeline(invokeAdapter(
                repository, state,
                WorkSessionV2Client.timelineArguments(sessionId, -1, 32)
        ));
        List<WorkSessionV2.Event> retained = timeline.events().subList(
                timeline.events().size() - 2, timeline.events().size()
        );
        assertEquals(List.of("frontend-reopened", "session-closed"),
                retained.stream().map(WorkSessionV2.Event::kind).toList());
        assertEquals(List.of("intellij-community", "intellij-community"),
                retained.stream().map(event -> event.frontend().kind()).toList());
        assertEquals(List.of("workbench-intellij-community", "workbench-intellij-community"),
                retained.stream().map(event -> event.frontend().frontendId()).toList());
    }

    @Test
    void intellijReadsHistoricalOwnerArtifactRangesOnlyThroughTheCorePort() throws Exception {
        Path repository = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().resolve("../..").normalize();
        Path state = temporary.resolve("intellij-artifact-state");
        Files.createDirectories(state);
        String script = """
                import json, pathlib, runpy, sys
                root = pathlib.Path(sys.argv[1]).resolve()
                state = pathlib.Path(sys.argv[2]).resolve()
                runpy.run_path(str(root / "tools/workbench.py"), run_name="intellij_artifact_fixture")
                from workbench_core.sessions import RetainedSession, live_console_owner_reference
                from workbench_shell.work_session import WorkSessionStore
                retained = RetainedSession(root=state, command_id="fixture.raw-range", argv=["fixture"], cwd=root, intent="inspect", session_id="live-console-artifact-v1")
                historical = live_console_owner_reference(state, retained.session_id)
                store = WorkSessionStore(state)
                created = store.create(
                    task={"task_id": "task:intellij-artifact", "owner_id": "workbench-shell"},
                    workspace={"identity_id": "workspace:intellij-artifact", "canonical_root": str(root), "source_revision": "git:intellij-artifact", "dirty_fingerprint": "sha256:" + "1" * 64},
                    identities={"core_id": "core:intellij-artifact", "catalog_id": "sha256:" + "2" * 64, "host_adapter_id": "host:native", "platform_profile_id": "platform:cleanroom", "pack_profile_id": None},
                    frontend={"frontend_id": "workbench-test", "kind": "test", "version": "0.1.0"},
                )
                store._append_event(
                    created["session_id"], expected_sequence=0,
                    frontend={"frontend_id": "workbench-test", "kind": "test", "version": "0.1.0"},
                    kind="owner-execution-bound", lifecycle="running",
                    stage={"stage_id": "owner-execution", "state": "running"},
                    owner_record_refs=[historical],
                    _terminal_owner_authorized=False, _owner_custody_authorized=True,
                )
                locator = retained.write_raw("stdout", b"needle\\n")
                retained.record_event({
                    "format_version": "workbench-live-console-event-v1", "event_id": "event:1", "sequence": 1,
                    "ingested_at": "2026-08-21T12:00:00Z", "monotonic_ns": 1, "source_timestamp": None,
                    "source": "fixture", "stream": "stdout",
                    "raw_locator": {"artifact": locator.path, "byte_start": locator.byte_start, "byte_end": locator.byte_end, "line": 1, "chunk": 1, "boundary": "lf"},
                    "kind": "text", "severity": "unknown", "subsystem": "generic", "logger": None, "thread": None,
                    "message": "needle", "parse_provenance": "raw", "classification_basis": [],
                    "cluster_key": "sha256:" + "0" * 64, "signal": True, "outcome_failure": False,
                    "source_locators": [], "limitations": [],
                })
                retained.finish(state="complete", process_exit_code=0, effective_exit_code=0, outcome="complete")
                print(json.dumps({"session_id": created["session_id"], "owner": historical}, sort_keys=True))
                """;
        Process process = new ProcessBuilder(
                "python3", "-c", script, repository.toString(), state.toString()
        ).directory(repository.toFile()).start();
        process.getOutputStream().close();
        String stdout = new String(
                process.getInputStream().readNBytes(8 * 1024 * 1024), StandardCharsets.UTF_8
        );
        String stderr = new String(
                process.getErrorStream().readNBytes(1024 * 1024), StandardCharsets.UTF_8
        );
        assertEquals(true, process.waitFor(120, TimeUnit.SECONDS));
        assertEquals(0, process.exitValue(), stderr);
        JsonObject fixture = JsonParser.parseString(stdout).getAsJsonObject();
        String sessionId = fixture.get("session_id").getAsString();
        JsonObject ownerValue = fixture.getAsJsonObject("owner");
        WorkSessionV2.OwnerReference owner = new WorkSessionV2.OwnerReference(
                ownerValue.get("owner_id").getAsString(),
                ownerValue.get("record_id").getAsString(),
                ownerValue.get("record_kind").getAsString(),
                ownerValue.get("uri").getAsString(),
                ownerValue.get("digest").getAsString(),
                ownerValue.get("last_verified_state").getAsString(),
                ownerValue.get("verified_at").getAsString()
        );
        WorkSessionV2Client.OwnerArtifactEvents page =
                WorkSessionV2Client.parseArtifactEvents(invokeAdapter(
                        repository, state,
                        WorkSessionV2Client.artifactEventsArguments(sessionId, owner, -1, 1024)
                ));
        assertEquals(1, page.events().size());
        assertEquals("event:1", page.events().getFirst().eventId());
        assertFalse(page.currentOwnerDigest().equals(owner.digest()));
        WorkSessionV2Client.OwnerArtifactRange range =
                WorkSessionV2Client.parseArtifactRange(invokeAdapter(
                        repository, state,
                        WorkSessionV2Client.artifactRangeArguments(
                                sessionId, owner, page.events().getFirst().eventId()
                        )
                ));
        assertEquals("needle\n", range.utf8());
        assertEquals(7, range.byteCount());

        Path raw = state.resolve(
                ".workbench/sessions/live-console/live-console-artifact-v1/stdout.raw"
        );
        Files.writeString(raw, "noodle\n", StandardCharsets.UTF_8);
        assertThrows(AssertionError.class, () -> invokeAdapter(
                repository, state,
                WorkSessionV2Client.artifactRangeArguments(
                        sessionId, owner, page.events().getFirst().eventId()
                )
        ));
    }

    private static String invokeAdapter(
            Path repository, Path stateRoot, List<String> adapterArguments
    ) throws Exception {
        List<String> arguments = new ArrayList<>(adapterArguments);
        int jsonIndex = arguments.indexOf("--json");
        arguments.add(jsonIndex, "--state-root");
        arguments.add(jsonIndex + 1, stateRoot.toString());
        List<String> command = new ArrayList<>(List.of(
                "python3", repository.resolve("tools/workbench.py").toString()
        ));
        command.addAll(arguments);
        Process process = new ProcessBuilder(command).directory(repository.toFile()).start();
        process.getOutputStream().close();
        byte[] stdout = process.getInputStream().readNBytes(48 * 1024 * 1024 + 1);
        byte[] stderr = process.getErrorStream().readNBytes(1024 * 1024 + 1);
        assertEquals(true, process.waitFor(120, TimeUnit.SECONDS));
        assertEquals(0, process.exitValue(), new String(stderr, StandardCharsets.UTF_8));
        return new String(stdout, StandardCharsets.UTF_8);
    }

    private JsonObject liveBundle() throws Exception {
        Path repository = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().resolve("../..").normalize();
        String script = """
                import json, os, pathlib, runpy, sys
                root = pathlib.Path(sys.argv[1]).resolve()
                state = pathlib.Path(sys.argv[2]).resolve()
                runpy.run_path(str(root / "tools/workbench.py"), run_name="intellij_v2_fixture")
                from workbench_shell.work_session import WorkSessionStore
                from workbench_shell.workspace_dashboard import build_workspace_home_v2, load_product_capability_owner_port, work_session_summary_owner_port
                capability_catalog = load_product_capability_owner_port(root)
                initial = build_workspace_home_v2(
                    root, root, capability_catalog_record=capability_catalog,
                )
                store = WorkSessionStore(state)
                created = store.create(
                    task={"task_id": "task:intellij-live", "owner_id": "workbench-shell"},
                    workspace={
                        "identity_id": initial["workspace"]["workspace_id"],
                        "canonical_root": initial["workspace"]["root"],
                        "source_revision": initial["workspace"]["source_revision"],
                        "dirty_fingerprint": initial["workspace"]["dirty_fingerprint"],
                    },
                    identities={
                        "core_id": "core:intellij-live",
                        "catalog_id": initial["catalog"]["catalog_digest"],
                        "host_adapter_id": "host:native",
                        "platform_profile_id": "platform:cleanroom",
                        "pack_profile_id": None,
                    },
                    frontend={"frontend_id": "frontend:intellij", "kind": "intellij-community", "version": "0.1.0"},
                    lifecycle="discovered",
                )
                session_id = created["session_id"]
                status = store.status(session_id)
                home = build_workspace_home_v2(
                    root, root,
                    session_record=work_session_summary_owner_port(status),
                    capability_catalog_record=capability_catalog,
                )
                os.environ["WORKBENCH_CLEANROOM_FIXTURE_GRADLEW"] = ""
                os.environ["WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME"] = ""
                fixture_home = build_workspace_home_v2(
                    root,
                    root / "profiles/platforms/cleanroom/fixtures/generic-mod-daily-loop",
                    capability_catalog_record=capability_catalog,
                )
                print(json.dumps({
                    "home": home,
                    "fixture_home": fixture_home,
                    "status": status,
                    "timeline": store.timeline(session_id),
                    "recovery": store.preview_recovery(session_id),
                }, sort_keys=True))
                """;
        Process process = new ProcessBuilder(
                "python3", "-c", script, repository.toString(), temporary.toString()
        ).directory(repository.toFile()).start();
        process.getOutputStream().close();
        byte[] stdout = process.getInputStream().readNBytes(64 * 1024 * 1024 + 1);
        byte[] stderr = process.getErrorStream().readNBytes(1024 * 1024 + 1);
        if (!process.waitFor(120, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new AssertionError("live Product Spine V2 fixture timed out");
        }
        assertEquals(0, process.exitValue(), new String(stderr, StandardCharsets.UTF_8));
        return JsonParser.parseString(new String(stdout, StandardCharsets.UTF_8)).getAsJsonObject();
    }

    private JsonObject liveHome() throws Exception {
        Path repository = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath().resolve("../..").normalize();
        Process process = new ProcessBuilder(
                "python3", repository.resolve("tools/workbench.py").toString(),
                "open", repository.toString(), "--json"
        ).directory(repository.toFile()).start();
        process.getOutputStream().close();
        byte[] stdout = process.getInputStream().readNBytes(16 * 1024 * 1024 + 1);
        byte[] stderr = process.getErrorStream().readNBytes(1024 * 1024 + 1);
        if (!process.waitFor(120, TimeUnit.SECONDS)) {
            process.destroyForcibly();
            throw new AssertionError("live Workspace Home V2 fixture timed out");
        }
        assertEquals(0, process.exitValue(), new String(stderr, StandardCharsets.UTF_8));
        return JsonParser.parseString(new String(stdout, StandardCharsets.UTF_8))
                .getAsJsonObject();
    }

    private static void reidentify(
            JsonObject row, String field, String prefix, boolean trailingNewline
    ) {
        JsonObject body = row.deepCopy();
        body.remove(field);
        row.addProperty(field, CanonicalJson.contentId(prefix, body, trailingNewline));
    }

    private static JsonObject withAvailableCleanroomConstructionOwner(JsonObject source) {
        JsonObject home = source.deepCopy();
        removeConstructionOwners(home);
        JsonObject owner = new JsonObject();
        owner.addProperty("kind", "new-project-construction");
        owner.addProperty("owner_id", "cleanroom-platform-profile");
        owner.addProperty(
                "record_format", "workbench-cleanroom-mod-construction-owner-v2"
        );
        owner.addProperty(
                "record_id",
                "workbench-cleanroom-mod-construction-owner:sha256:" + "b".repeat(64)
        );
        owner.add("session_id", com.google.gson.JsonNull.INSTANCE);
        owner.add("catalog_id", com.google.gson.JsonNull.INSTANCE);
        owner.addProperty("record_revision", "sha256:" + "a".repeat(64));
        owner.addProperty("record_digest", "sha256:" + "a".repeat(64));
        owner.add("bound_workspace_revision", com.google.gson.JsonNull.INSTANCE);
        owner.addProperty("freshness", "current");
        owner.addProperty("integrity", "verified");
        owner.add("validation_problem", com.google.gson.JsonNull.INSTANCE);
        reidentifyOwnerReference(owner);
        home.getAsJsonArray("owner_records").add(owner);
        sortOwnerReferences(home);

        JsonObject row = home.getAsJsonObject("new_project");
        row.addProperty("state", "available");
        row.add("admitted_kinds", strings("workbench-new-project-kind:cleanroom-mod"));
        row.add("owner_ref_ids", strings(owner.get("id").getAsString()));
        row.add("blockers", strings());
        row.addProperty(
                "reason", "The Cleanroom profile owns one exact fresh-project constructor."
        );
        row.addProperty(
                "next_safe_action", "workbench new cleanroom-mod preview --help"
        );
        reidentify(home, "home_id", "workspace-home", true);
        return home;
    }

    private static void assertNewProjectRejected(
            JsonObject available, Consumer<JsonObject> mutation
    ) {
        JsonObject changed = available.deepCopy();
        mutation.accept(changed.getAsJsonObject("new_project"));
        reidentify(changed, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(changed.toString()));
    }

    private static void assertConstructionOwnerRejected(
            JsonObject available, Consumer<JsonObject> mutation
    ) {
        JsonObject changed = available.deepCopy();
        String ownerRefId = changed.getAsJsonObject("new_project")
                .getAsJsonArray("owner_ref_ids").get(0).getAsString();
        JsonObject owner = null;
        for (var item : changed.getAsJsonArray("owner_records")) {
            JsonObject candidate = item.getAsJsonObject();
            if (ownerRefId.equals(candidate.get("id").getAsString())) {
                owner = candidate;
                break;
            }
        }
        assertNotNull(owner);
        mutation.accept(owner);
        reidentifyOwnerReference(owner);
        changed.getAsJsonObject("new_project").add(
                "owner_ref_ids", strings(owner.get("id").getAsString())
        );
        sortOwnerReferences(changed);
        reidentify(changed, "home_id", "workspace-home", true);
        assertThrows(IllegalArgumentException.class,
                () -> WorkspaceHomeV2.parse(changed.toString()));
    }

    private static void reidentifyOwnerReference(JsonObject owner) {
        JsonObject identity = new JsonObject();
        identity.addProperty("kind", owner.get("kind").getAsString());
        identity.addProperty("owner_id", owner.get("owner_id").getAsString());
        identity.addProperty("record_digest", owner.get("record_digest").getAsString());
        owner.addProperty("id", CanonicalJson.contentId("owner-ref", identity, true));
    }

    private static void removeConstructionOwners(JsonObject home) {
        JsonArray owners = home.getAsJsonArray("owner_records");
        for (int index = owners.size() - 1; index >= 0; index--) {
            if ("new-project-construction".equals(
                    owners.get(index).getAsJsonObject().get("kind").getAsString()
            )) {
                owners.remove(index);
            }
        }
    }

    private static void sortOwnerReferences(JsonObject home) {
        JsonArray owners = home.getAsJsonArray("owner_records");
        List<JsonObject> sorted = new ArrayList<>();
        for (var item : owners) sorted.add(item.getAsJsonObject());
        sorted.sort(Comparator
                .comparing((JsonObject owner) -> owner.get("kind").getAsString())
                .thenComparing(owner -> owner.get("owner_id").getAsString())
                .thenComparing(owner -> owner.get("id").getAsString()));
        while (!owners.isEmpty()) owners.remove(0);
        sorted.forEach(owners::add);
    }

    private static JsonArray strings(String... values) {
        JsonArray result = new JsonArray();
        for (String value : values) result.add(value);
        return result;
    }
}
