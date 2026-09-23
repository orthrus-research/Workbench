"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'subsurface-studio', version('workbench-subsurface-studio'),
        capabilities=(
            Capability('subsurface-studio.subsurface', ('subsurface',), 'workbench_registration_subsurface_studio:subsurface', 'Run subsurface'),
        ),
        requires=('crucible',),
    )


def subsurface(argv, *, context):
    context.check_cancelled()
    from workbench_subsurface_studio.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
