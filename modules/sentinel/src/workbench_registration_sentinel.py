"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'sentinel', version('workbench-sentinel'),
        capabilities=(
            Capability('sentinel.diagnose-mixins', ('diagnose', 'mixins'), 'workbench_registration_sentinel:diagnose_mixins', 'Run diagnose mixins'),
        ),
        requires=(),
    )


def diagnose_mixins(argv, *, context):
    context.check_cancelled()
    from workbench_sentinel.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
