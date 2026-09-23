"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'workbench-shell', version('workbench-shell'),
        capabilities=(
            Capability("workbench-shell.recipe-capture", ('capture', 'recipes'), "workbench_shell.commands:recipe_capture", "Capture a selected developer pack and audit its recipes"),
            Capability("workbench-shell.context", ('context',), "workbench_shell.commands:developer_context", "Select and observe developer context"),
            Capability("workbench-shell.open", ('open',), "workbench_shell.commands:open", "Run open"),
            Capability("workbench-shell.capabilities", ('capabilities',), "workbench_shell.commands:capabilities", "Run capabilities"),
            Capability("workbench-shell.launcher-setup", ('launcher', 'setup'), "workbench_shell.commands:launcher_setup", "Run launcher setup"),
            Capability("workbench-shell.session", ('session',), "workbench_shell.commands:session", "Run session"),
            Capability("workbench-shell.adopt", ('adopt',), "workbench_shell.commands:adopt", "Run adopt"),
            Capability("workbench-shell.reopen", ('reopen',), "workbench_shell.commands:reopen", "Run reopen"),
            Capability("workbench-shell.project-qualify", ('project', 'qualify'), "workbench_shell.commands:project_qualify", "Run project qualify", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.diagnose", ('diagnose',), "workbench_shell.commands:diagnose", "Run diagnose"),
            Capability("workbench-shell.dev-fixture", ('dev', 'fixture'), "workbench_shell.commands:dev_fixture", "Run dev fixture", requires_profiles=('cleanroom',)),
            Capability("workbench-shell.dev", ('dev',), "workbench_shell.commands:dev", "Run dev", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.change", ('change',), "workbench_shell.commands:change", "Run change", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.new", ('new',), "workbench_shell.commands:new", "Run new", requires_profiles=('cleanroom',)),
            Capability("workbench-shell.console", ('console',), "workbench_shell.commands:console", "Run console"),
            Capability("workbench-shell.review", ('review',), "workbench_shell.commands:review", "Run review", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.doctor", ('doctor',), "workbench_shell.commands:doctor", "Run doctor"),
            Capability("workbench-shell.run", ('run',), "workbench_shell.commands:run", "Run run"),
            Capability("workbench-shell.feature", ('feature',), "workbench_shell.commands:feature", "Run feature", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.atlas-recipes", ('atlas', 'recipes', 'assess-plan'), "workbench_shell.commands:atlas_recipe_plan", "Assess owner-validated recipe plans with Atlas", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.machine", ('machine',), "workbench_shell.commands:machine", "Run machine"),
            Capability("workbench-shell.assets", ('assets',), "workbench_shell.commands:assets", "Run assets"),
            Capability("workbench-shell.evolution", ('evolution',), "workbench_shell.commands:evolution", "Run evolution"),
            Capability("workbench-shell.process-recipes", ('process', 'recipes', 'compare'), "workbench_shell.commands:process_recipes", "Run process recipes compare"),
            Capability("workbench-shell.studio", ('studio',), "workbench_shell.commands:studio", "Run studio"),
            Capability("workbench-shell.host-status", ('host-status',), "workbench_shell.commands:host_status", "Run host-status"),
            Capability("workbench-shell.service-call", ('service-call',), "workbench_shell.commands:service_call", "Run service-call"),
            Capability("workbench-shell.service-host-v3", ('service-host-v3',), "workbench_shell.commands:service_host_v3", "Run service-host-v3"),
            Capability("workbench-shell.service-probe-v3", ('service-probe-v3',), "workbench_shell.commands:service_probe_v3", "Run service-probe-v3"),
            Capability("workbench-shell.feature-service", ('feature-service',), "workbench_shell.commands:feature_service", "Run feature-service"),
            Capability("workbench-shell.config", ('config',), "workbench_shell.commands:config", "Run config"),
            Capability("workbench-shell.inspect", ('inspect',), "workbench_shell.commands:inspect", "Run inspect"),
            Capability("workbench-shell.initialize", ('initialize',), "workbench_shell.commands:initialize", "Run initialize"),
            Capability("workbench-shell.register", ('register',), "workbench_shell.commands:register", "Run register"),
            Capability("workbench-shell.blueprint-stage", ('blueprint-stage',), "workbench_shell.commands:blueprint_stage", "Run blueprint-stage", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.material-fluid", ('material-fluid',), "workbench_shell.commands:material_fluid", "Run material-fluid", requires_profiles=('supersymmetry',)),
            Capability("workbench-shell.runtime-plan", ('runtime-plan',), "workbench_shell.commands:runtime_plan", "Run runtime-plan"),
            Capability("workbench-shell.runtime-bootstrap", ('runtime-bootstrap',), "workbench_shell.commands:runtime_bootstrap", "Run runtime-bootstrap"),
            Capability("workbench-shell.runtime-java", ('runtime-java',), "workbench_shell.commands:runtime_java", "Run runtime-java"),
            Capability("workbench-shell.runtime-materialize", ('runtime-materialize',), "workbench_shell.commands:runtime_materialize", "Run runtime-materialize"),
            Capability("workbench-shell.runtime-launch", ('runtime-launch',), "workbench_shell.commands:runtime_launch", "Run runtime-launch"),
            Capability("workbench-shell.runtime-observe", ('runtime-observe',), "workbench_shell.commands:runtime_observe", "Run runtime-observe"),
            Capability("workbench-shell.runtime-diagnose", ('runtime-diagnose',), "workbench_shell.commands:runtime_diagnose", "Run runtime-diagnose"),
            Capability("workbench-shell.runtime-worldgen-audit", ('runtime-worldgen-audit',), "workbench_shell.commands:runtime_worldgen_audit", "Run runtime-worldgen-audit"),
            Capability("workbench-shell.runtime-worldgen-fingerprint", ('runtime-worldgen-fingerprint',), "workbench_shell.commands:runtime_worldgen_fingerprint", "Run runtime-worldgen-fingerprint"),
            Capability("workbench-shell.runtime-worldgen-compare", ('runtime-worldgen-compare',), "workbench_shell.commands:runtime_worldgen_compare", "Run runtime-worldgen-compare"),
            Capability("workbench-shell.runtime-worldgen-block-delta", ('runtime-worldgen-block-delta',), "workbench_shell.commands:runtime_worldgen_block_delta", "Run runtime-worldgen-block-delta"),
            Capability('workbench-shell.shell', ('shell',), 'workbench_registration_workbench_shell:run_shell', 'Run the optional domain Shell'),
        ),
        requires=('atlas', 'crucible', 'pack-program-studio', 'project-intelligence', 'runtime-explorer'),
    )


def run_shell(argv, *, context):
    context.check_cancelled()
    from workbench_api.resources import repository_root
    from workbench_shell.cli import main
    return main([*argv, '--suite-root', str(repository_root(__file__))])
