"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'process-studio', version('workbench-process-studio'),
        capabilities=(
            Capability('process-studio.process-effects', ('process', 'effects', 'compare'), 'workbench_registration_process_studio:process_effects', 'Run process effects compare'),
        ),
        requires=('crucible',),
    )


def process_effects(argv, *, context):
    context.check_cancelled()
    from workbench_process_studio.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
