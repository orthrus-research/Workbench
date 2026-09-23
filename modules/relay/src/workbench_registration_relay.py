"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'relay', version('workbench-relay'),
        capabilities=(
            Capability('relay.relay-locate', ('relay', 'locate'), 'workbench_registration_relay:relay_locate', 'Run relay locate'),
        ),
        requires=('runtime-explorer',),
    )


def relay_locate(argv, *, context):
    context.check_cancelled()
    from workbench_relay.cli import main
    from workbench_api.resources import repository_root
    return main(argv, root=repository_root(__file__))
