"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'crucible', version('workbench-crucible'),
        capabilities=(
            Capability('crucible.worldgen-dev', ('worldgen', 'dev'), 'workbench_registration_crucible:worldgen_dev', 'Run worldgen dev'),
        ),
        requires=(),
    )


def worldgen_dev(argv, *, context):
    context.check_cancelled()
    from workbench_crucible_worldgen_iteration.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
