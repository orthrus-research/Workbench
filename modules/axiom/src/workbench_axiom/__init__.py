"""Registration has no JVM startup or implicit source-checkout dependency."""

from importlib.metadata import version
from workbench_api.modules import Capability, Module


def check(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("check", arguments, context)


def query(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("query", arguments, context)


def coverage(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("coverage", arguments, context)


def target(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("target", arguments, context)


def platform(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("platform", arguments, context)


def material_program(arguments, *, context):
    from .cli import main
    context.check_cancelled()
    return main("material-program", arguments, context)


def module():
    return Module(
        id="axiom", version=version("workbench-axiom"),
        capabilities=tuple(
            Capability(
                id="axiom." + operation, command=("axiom", operation),
                handler="workbench_axiom:" + operation.replace("-", "_"), description=description,
            )
            for operation, description in (
                ("check", "Check an explicit recipe definition program with Axiom"),
                ("query", "Evaluate ordinary MIXER start admission with Axiom"),
                ("coverage", "Inspect Axiom's source identity and exact coverage boundaries"),
                ("target", "Verify a profile-owned source target and inspect loader inputs; not recipe qualification"),
                ("platform", "Verify explicit platform metadata and library bytes without launching Minecraft"),
                ("material-program", "Execute a complete saved material program in an explicit native context; qualification is separate"),
            )
        ),
    )
