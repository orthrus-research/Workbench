"""Registered product compositions owned by the optional Shell module."""
from __future__ import annotations


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)


def recipe_capture(argv, *, context):
    context.check_cancelled()
    from .recipe_capture import main
    return main(list(argv), context=context)


def developer_context(argv, *, context):
    context.check_cancelled()
    from .developer_context_cli import main
    return main(list(argv), suite_root=ROOT)


def open(argv, *, context):
    from .workspace_commands import _open_main
    context.check_cancelled()
    return _open_main(list(argv))


def capabilities(argv, *, context):
    context.check_cancelled()
    from workbench_shell.product_spine_cli import capabilities_main
    return capabilities_main(list(argv), root=ROOT)


def launcher_setup(argv, *, context):
    context.check_cancelled()
    from workbench_shell.launcher_setup import main as launcher_setup_main
    return launcher_setup_main(list(argv), root=ROOT)


def session(argv, *, context):
    context.check_cancelled()
    from workbench_shell.product_spine_cli import session_main
    return session_main(list(argv), root=ROOT)


def adopt(argv, *, context):
    context.check_cancelled()
    from workbench_shell.product_spine_cli import adopt_main
    return adopt_main(list(argv), root=ROOT)


def reopen(argv, *, context):
    context.check_cancelled()
    from workbench_shell.product_spine_cli import reopen_main
    return reopen_main(list(argv), root=ROOT)


def project_qualify(argv, *, context):
    context.check_cancelled()
    from workbench_shell.project_qualification import main as qualification_main
    from workbench_api.state_paths import default_product_spine_state_root
    return qualification_main(list(argv), root=ROOT, default_state_root=default_product_spine_state_root(ROOT))


def diagnose(argv, *, context):
    context.check_cancelled()
    from workbench_shell.diagnose_reproduce_cli import diagnose_main
    return diagnose_main(list(argv), root=ROOT)


def dev_fixture(argv, *, context):
    context.check_cancelled()
    from workbench_shell.golden_journey_cli import dev_fixture_main
    fixture_root = context.locations.get("fixture_instances")
    return dev_fixture_main(
        list(argv),
        root=ROOT,
        default_state_root=(
            fixture_root / "cleanroom/generic-mod-daily-loop"
            if fixture_root is not None
            else None
        ),
    )


def dev(argv, *, context):
    from .development_commands import _dev_main
    context.check_cancelled()
    return _dev_main(list(argv))


def change(argv, *, context):
    context.check_cancelled()
    from workbench_shell.golden_journey_cli import change_main
    return change_main(list(argv), root=ROOT)


def new(argv, *, context):
    context.check_cancelled()
    if list(argv) in ([], ['--help'], ['-h']):
        print('usage: workbench new cleanroom-mod {preview,apply,recover} ...')
        return 0
    from workbench_shell.cleanroom_new_project_cli import new_project_main
    return new_project_main(list(argv), root=ROOT)


def console(argv, *, context):
    context.check_cancelled()
    from workbench_shell.console_cli import main as console_main
    return console_main(list(argv), root=ROOT)


def review(argv, *, context):
    from .review_commands import _review_main
    context.check_cancelled()
    if list(argv) in ([], ['--help'], ['-h']):
        print('usage: workbench review recipes ... | workbench review pr ...')
        return 0
    return _review_main(list(argv))


def doctor(argv, *, context):
    from .inspection_commands import _doctor_main
    context.check_cancelled()
    return _doctor_main(list(argv))


def run(argv, *, context):
    from .run_commands import _managed_run_main
    context.check_cancelled()
    return _managed_run_main(list(argv))


def feature(argv, *, context):
    context.check_cancelled()
    from workbench_shell.developer_feature_cli import main as feature_main
    return feature_main(list(argv), suite_root=ROOT)


def atlas_recipes(argv, *, context):
    context.check_cancelled()
    from workbench_shell.atlas_recipe_cli import main as atlas_recipe_main
    return atlas_recipe_main(list(argv), suite_root=ROOT, context=context)


def atlas_recipe_plan(argv, *, context):
    return atlas_recipes(['assess-plan', *argv], context=context)


def machine(argv, *, context):
    context.check_cancelled()
    from workbench_shell.machine_studio_cli import main as machine_main
    return machine_main(list(argv))


def assets(argv, *, context):
    context.check_cancelled()
    from workbench_shell.asset_studio_cli import main as assets_main
    return assets_main(list(argv))


def evolution(argv, *, context):
    context.check_cancelled()
    from workbench_shell.evolution_studio_cli import main as evolution_main
    return evolution_main(list(argv), suite_root=ROOT)


def process_recipes(argv, *, context):
    context.check_cancelled()
    from workbench_shell.process_recipe_cli import main as process_recipe_main
    return process_recipe_main(list(argv), root=ROOT)


def studio(argv, *, context):
    context.check_cancelled()
    from workbench_shell.cli import main as shell_main
    return shell_main(['feature', *list(argv), '--suite-root', str(ROOT)], feature_program='workbench studio')


def host_status(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['host-status', *argv]
    arguments += ["--suite-root", str(ROOT)]
    return main(arguments)


def service_call(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['service-call', *argv]
    arguments += ["--suite-root", str(ROOT)]
    return main(arguments)


def service_host_v3(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['service-host-v3', *argv]
    arguments += ["--suite-root", str(ROOT)]
    return main(arguments)


def service_probe_v3(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['service-probe-v3', *argv]
    arguments += ["--suite-root", str(ROOT)]
    return main(arguments)


def feature_service(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['feature-service', *argv]
    arguments += ["--suite-root", str(ROOT)]
    return main(arguments)


def config(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['config', *argv]
    return main(arguments)


def inspect(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['inspect', *argv]
    return main(arguments)


def initialize(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['initialize', *argv]
    return main(arguments)


def register(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['register', *argv]
    return main(arguments)


def blueprint_stage(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['blueprint-stage', *argv]
    return main(arguments, resolved_locations=context.locations)


def material_fluid(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['material-fluid', *argv]
    return main(arguments)


def runtime_plan(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-plan', *argv]
    return main(arguments)


def runtime_bootstrap(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-bootstrap', *argv]
    return main(arguments)


def runtime_java(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-java', *argv]
    return main(arguments)


def runtime_materialize(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-materialize', *argv]
    return main(arguments)


def runtime_launch(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-launch', *argv]
    from .launcher_setup import launcher_defaults_for_runtime
    arguments = launcher_defaults_for_runtime(arguments)
    return main(arguments)


def runtime_observe(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-observe', *argv]
    return main(arguments)


def runtime_diagnose(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-diagnose', *argv]
    return main(arguments)


def runtime_worldgen_audit(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-worldgen-audit', *argv]
    return main(arguments)


def runtime_worldgen_fingerprint(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-worldgen-fingerprint', *argv]
    return main(arguments)


def runtime_worldgen_compare(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-worldgen-compare', *argv]
    return main(arguments)


def runtime_worldgen_block_delta(argv, *, context):
    context.check_cancelled()
    from .cli import main
    arguments = ['runtime-worldgen-block-delta', *argv]
    return main(arguments)
