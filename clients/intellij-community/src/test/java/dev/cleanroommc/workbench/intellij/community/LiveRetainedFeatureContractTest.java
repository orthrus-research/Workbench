package dev.cleanroommc.workbench.intellij.community;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

/** Cross-module proof for the current record-discovery-to-presentation seam. */
final class LiveRetainedFeatureContractTest {
    @TempDir
    Path temporary;

    @Test
    void currentShellRecordReopensAsExactDigestVerifiedNativeBytes() throws Exception {
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
                from workbench_core.development import enable_source_checkout
                enable_source_checkout(root)
                from workbench_core.host_services import install_local_host_services
                install_local_host_services()
                sys.path.insert(0, str(root / 'modules/workbench-shell/tests/developer_feature'))
                from test_developer_feature import _checkout
                from workbench_shell.developer_feature import (
                    build_material_fluid_recipe_plan,
                    retain_feature_record,
                )
                from workbench_shell.developer_feature_presentation import (
                    discover_feature_records,
                    present_feature_record,
                )
                from workbench_shell.developer_feature_transaction_view import (
                    build_feature_transaction_view,
                )
                parent = temporary / 'material'
                parent.mkdir()
                workspace = _checkout(parent)
                state = temporary / 'state'
                plan = build_material_fluid_recipe_plan(
                    root,
                    workspace,
                    name='IDE Native Diff Solvent',
                    color='#2266aa',
                    translation='IDE Native Diff Solvent',
                    symbol='IdeNativeDiffSolvent',
                    recipe_script='groovy/postInit/chemistry/Probe.groovy',
                    recipe_map='MIXER',
                    input_fluid='steam',
                    input_amount=1000,
                    output_amount=1000,
                    duration=80,
                    voltage_tier='LV',
                )
                retain_feature_record(state, 'plans', plan)
                catalog = discover_feature_records(root, state)
                presentation = present_feature_record(
                    root,
                    state,
                    family='material-fluid-recipe',
                    collection='plans',
                    reference=plan['id'],
                )
                transaction = build_feature_transaction_view(
                    root,
                    state,
                    family='material-fluid-recipe',
                    plan_reference=plan['id'],
                )
                print(json.dumps(
                    {
                        'catalog': catalog,
                        'presentation': presentation,
                        'transaction': transaction,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ))
                """;
        String output = CommandProcess.capture(
                CoreLaunch.resolve("python3", false, null),
                List.of("-c", script, root.toString(), temporary.toString()),
                FeaturePresentation.MAX_BYTES,
                60,
                root.toString()
        );
        JsonObject envelope = JsonParser.parseString(output).getAsJsonObject();
        FeatureRecordCatalog catalog = FeatureRecordCatalog.parse(
                envelope.get("catalog").toString()
        );
        FeaturePresentation presentation = FeaturePresentation.parse(
                envelope.get("presentation").toString()
        );
        RetainedFeatureClient.TransactionView transaction =
                RetainedFeatureClient.TransactionView.parse(
                        envelope.get("transaction").toString()
                );
        assertEquals(1, catalog.records().size());
        assertEquals(4, presentation.operations().size());
        assertEquals("planned", transaction.currentEffectiveState());
        RetainedFeatureClient.validateTransactionLink(
                catalog.records().getFirst(), presentation, transaction
        );
    }

    @Test
    void currentShellV2ProjectionReopensPerSideRetainedEvidence() throws Exception {
        Path root = Path.of(System.getProperty("user.dir"))
                .toAbsolutePath()
                .resolve("../..")
                .normalize();
        String script = """
                from copy import deepcopy
                import json
                from pathlib import Path
                import sys
                root = Path(sys.argv[1])
                temporary = Path(sys.argv[2])
                sys.path.insert(0, str(root / 'api/src'))
                sys.path.insert(0, str(root / 'core/src'))
                from workbench_core.development import enable_source_checkout
                enable_source_checkout(root)
                from workbench_core.host_services import install_local_host_services
                install_local_host_services()
                sys.path.insert(0, str(root / 'modules/workbench-shell/tests/developer_feature'))
                from test_developer_source_feature import _checkout
                from workbench_shell.developer_feature import retain_feature_record
                from workbench_blueprints.profile_construction import recipe_change_authority
                from workbench_shell import developer_feature_presentation as presentation

                parent = temporary / 'source'
                parent.mkdir()
                workspace = _checkout(parent)
                state = temporary / 'state'
                plan = recipe_change_authority("supersymmetry").build_recipe_add_plan(
                    root,
                    workspace,
                    recipe_script='groovy/postInit/chemistry/Probe.groovy',
                    recipe_map='BR',
                    item_inputs=[{'kind': 'ore', 'name': 'dustSulfur', 'amount': 2}],
                    fluid_inputs=[{'name': 'water', 'amount': 1000}],
                    fluid_outputs=[{'name': 'sulfuric_acid', 'amount': 1000}],
                    duration=100,
                    voltage_tier='LV',
                )
                retain_feature_record(state, 'plans', plan)
                base = presentation.present_feature_record(
                    root,
                    state,
                    family='recipe-change',
                    collection='plans',
                    reference=plan['id'],
                )
                digest = 'e' * 64
                evidence = {
                    'final_launch_receipt': {
                        'id': 'sha256:' + digest,
                        'sha256': digest,
                        'size': 12,
                        'uri': (temporary / 'runtime-launch-v3.json').as_uri(),
                    },
                    'groovy_log': {
                        'sha256': digest,
                        'size': 42,
                        'uri': (temporary / 'minecraft-groovy.log').as_uri(),
                    },
                    'runtime_session_receipt': {
                        'id': 'sha256:' + digest,
                        'sha256': digest,
                        'size': 24,
                        'uri': (temporary / 'runtime-observation-v1.json').as_uri(),
                    },
                }
                record_id = (
                    'workbench-developer-recipe-change-runtime-comparison:sha256:'
                    + 'a' * 64
                )
                owner = {
                    'id': record_id,
                    'limitations': ['Runtime comparison is client-only.'],
                    'outcome': 'failed',
                    'sides': {
                        'baseline': {
                            'assessment': {
                                'assessment_id': 'assessment-baseline',
                                'state': 'failed',
                            },
                            'error': {
                                'kind': 'MarkerError',
                                'message': 'marker missing',
                                'phase': 'assertion',
                            },
                            'outcome': 'failed',
                            'probe': {
                                'overlay_id': 'overlay-baseline',
                                'overlay_spec_uri': (temporary / 'overlay-v1.json').as_uri(),
                                'probe_id': 'probe-baseline',
                                'script_uri': (temporary / 'Probe.groovy').as_uri(),
                            },
                            'retained_evidence': evidence,
                            'state': 'incomplete',
                        },
                        'candidate': {
                            'assessment': None,
                            'error': {
                                'kind': 'PriorSideFailed',
                                'message': 'not run',
                                'phase': 'blocked',
                            },
                            'outcome': 'not-run',
                            'probe': None,
                            'retained_evidence': None,
                            'state': 'incomplete',
                        },
                    },
                    'state': 'incomplete',
                }
                body = deepcopy(base)
                body.pop('id')
                body.update({
                    'actions': [],
                    'collection': 'runs',
                    'format': presentation.FORMAT_V2,
                    'limitations': presentation._limitations_projection(
                        plan, owner, include_owner=True
                    ),
                    'owner_record': {
                        'diagnostic_code': None,
                        'id': record_id,
                        'kind': 'workbench-developer-recipe-change-runtime-comparison',
                        'state': 'incomplete',
                        'uri': (temporary / 'record.json').as_uri(),
                    },
                    'runtime': presentation._runtime_projection(
                        'recipe-change', plan, 'runs', owner
                    ),
                    'schema_version': 2,
                })
                value = presentation.validate_feature_presentation(
                    presentation._seal(body)
                )
                print(json.dumps(value, ensure_ascii=False, sort_keys=True))
                """;
        String output = CommandProcess.capture(
                CoreLaunch.resolve("python3", false, null),
                List.of("-c", script, root.toString(), temporary.toString()),
                FeaturePresentation.MAX_BYTES,
                60,
                root.toString()
        );
        FeaturePresentation value = FeaturePresentation.parse(output);
        assertEquals(2, value.schemaVersion());
        assertEquals(List.of("baseline", "candidate"), value.runtime().sides().stream()
                .map(FeaturePresentation.RuntimeSide::role)
                .toList());
        assertNotNull(value.runtime().sides().getFirst().receipt());
        assertEquals("probe-baseline", value.runtime().sides().getFirst().probe().id());
    }
}
