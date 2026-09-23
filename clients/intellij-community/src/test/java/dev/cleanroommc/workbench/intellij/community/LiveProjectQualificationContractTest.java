package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Cross-module proof that the Community parser accepts current core plans. */
final class LiveProjectQualificationContractTest {
    @TempDir
    Path temporary;

    @Test
    void currentCoreReadyAttentionAndIncompatiblePlansRemainStrictlyConsumable()
            throws Exception {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .resolve("../..")
                .normalize();
        String script = """
                import json
                from pathlib import Path
                import sys
                root = Path(sys.argv[1])
                temporary = Path(sys.argv[2])
                sys.path.insert(0, str(root / 'api/src'))
                sys.path.insert(0, str(root / 'core/src'))
                from workbench_core.host_services import install_local_host_services
                install_local_host_services()
                for source in sorted((root / 'modules').glob('*/src')):
                    sys.path.insert(0, str(source))
                sys.path.insert(0, str(root / 'modules/workbench-shell/tests'))
                from supersymmetry_project_fixture import create_supersymmetry_project
                from workbench_shell.project_qualification import (
                    apply_qualification_plan,
                    build_qualification_plan,
                    qualification_status,
                )
                project = create_supersymmetry_project(temporary)
                state_root = temporary / 'external-state'
                def plan():
                    return build_qualification_plan(qualification_status(
                        root,
                        project,
                        profile_selector='supersymmetry',
                        state_root=state_root,
                    ))
                def apply(value):
                    return apply_qualification_plan(
                        root,
                        project,
                        profile_selector='supersymmetry',
                        state_root=state_root,
                        expected_plan_id=value['plan_id'],
                    )
                ready = plan()
                ready_result = apply(ready)
                (project / 'index.toml').write_text('\\n', encoding='utf-8')
                attention = plan()
                attention_result = apply(attention)
                manifest = project / 'pack.toml'
                manifest.write_text(
                    manifest.read_text(encoding='utf-8').replace(
                        'name = "Supersymmetry"', 'name = "Another Pack"'
                    ),
                    encoding='utf-8',
                )
                incompatible = plan()
                print(json.dumps({
                    'workspace': str(project.resolve()),
                    'ready': ready,
                    'ready_result': ready_result,
                    'attention': attention,
                    'attention_result': attention_result,
                    'incompatible': incompatible,
                }, ensure_ascii=False, sort_keys=True))
                """;
        String output = CommandProcess.capture(
                CoreLaunch.resolve("python3", false, null),
                List.of("-c", script, root.toString(), temporary.toString()),
                8 * 1024 * 1024,
                60,
                root.toString()
        );
        JsonObject envelope = JsonParser.parseString(output).getAsJsonObject();
        String workspace = envelope.get("workspace").getAsString();

        ProjectQualification.Plan ready = ProjectQualification.parsePlan(
                envelope.get("ready").toString(), workspace
        );
        ProjectQualification.Plan attention = ProjectQualification.parsePlan(
                envelope.get("attention").toString(), workspace
        );
        ProjectQualification.Plan incompatible = ProjectQualification.parsePlan(
                envelope.get("incompatible").toString(), workspace
        );
        ProjectQualification.Result readyResult = ProjectQualification.parseResult(
                envelope.get("ready_result").toString(), ready
        );
        ProjectQualification.Result attentionResult = ProjectQualification.parseResult(
                envelope.get("attention_result").toString(), attention
        );

        assertEquals("ready", ready.state());
        assertTrue(ready.canApply());
        assertEquals("qualified", readyResult.outcome());
        assertEquals("attention", attention.state());
        assertTrue(attention.canApply());
        assertEquals("requalified", attentionResult.outcome());
        assertEquals("incompatible", incompatible.state());
        assertFalse(incompatible.canApply());
        assertNull(incompatible.action());
    }
}
