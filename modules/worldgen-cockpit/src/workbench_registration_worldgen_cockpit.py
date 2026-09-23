"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'worldgen-cockpit', version('workbench-worldgen-cockpit'),
        capabilities=(
            Capability('worldgen-cockpit.cockpit', ('cockpit',), 'workbench_registration_worldgen_cockpit:cockpit', 'Run cockpit'),
        ),
        requires=('atlas', 'crucible', 'subsurface-studio'),
    )


def cockpit(argv, *, context):
    context.check_cancelled()
    from workbench_worldgen_cockpit.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
