"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'blueprints', version('workbench-blueprints'),
        capabilities=(
            Capability('blueprints.blueprints', ('blueprints',), 'workbench_registration_blueprints:blueprints', 'Run blueprints'),
        ),
        requires=('project-intelligence',),
    )


def blueprints(argv, *, context):
    context.check_cancelled()
    from workbench_blueprints.cli import main
    return main(argv)
