"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'worldgen-qualifier', version('workbench-worldgen-qualifier'),
        capabilities=(
            Capability('worldgen-qualifier.qualify', ('qualify',), 'workbench_registration_worldgen_qualifier:qualify', 'Run qualify'),
        ),
        requires=('crucible', 'worldgen-cockpit'),
    )


def qualify(argv, *, context):
    context.check_cancelled()
    from workbench_worldgen_qualifier.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
