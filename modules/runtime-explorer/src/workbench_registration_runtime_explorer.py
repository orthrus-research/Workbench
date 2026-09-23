"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'runtime-explorer', version('workbench-runtime-explorer'),
        capabilities=(
            Capability('runtime-explorer.explore', ('explore',), 'workbench_registration_runtime_explorer:explore', 'Run explore'),
        ),
        requires=('atlas', 'crucible', 'pack-program-studio', 'project-intelligence'),
    )


def explore(argv, *, context):
    context.check_cancelled()
    from workbench_runtime_explorer.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
