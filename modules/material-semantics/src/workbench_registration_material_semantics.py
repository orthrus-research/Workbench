"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'material-semantics', version('workbench-material-semantics'),
        capabilities=(
        ),
        requires=(),
    )
