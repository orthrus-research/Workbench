"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'atlas', version('workbench-atlas'),
        capabilities=(
            Capability('atlas.check', ('check',), 'workbench_registration_atlas:check', 'Check semantic projections'),
            Capability('atlas.why', ('why',), 'workbench_registration_atlas:why', 'Explain semantic projections'),
            Capability('atlas.impact', ('impact',), 'workbench_registration_atlas:impact', 'Inspect semantic impact'),
            Capability('atlas.recipes', ('atlas',), 'workbench_registration_atlas:recipes', 'Query Atlas recipe evidence'),
            Capability('atlas.observations', ('atlas', 'observations'), 'workbench_registration_atlas:observations', 'Inspect retained observations across families'),
            Capability('atlas.scans', ('atlas', 'scans'), 'workbench_registration_atlas:scans', 'Import, inspect and share completed Atlas scans'),
        ),
        requires=('crucible',),
    )


def _semantic(command, argv, *, context):
    context.check_cancelled()
    from workbench_atlas_projection.cli import main
    from workbench_api.resources import repository_root
    return main([command, *argv], root=repository_root(__file__))


def check(argv, *, context):
    return _semantic('check', argv, context=context)


def why(argv, *, context):
    return _semantic('why', argv, context=context)


def impact(argv, *, context):
    return _semantic('impact', argv, context=context)


def recipes(argv, *, context):
    context.check_cancelled()
    arguments = list(argv)
    if arguments[:1] == ['recipes']:
        from workbench_atlas_recipe_health.cli import main
        return main(arguments[1:], context=context)

    # Owning the parent namespace leaves the legacy Shell recipe route
    # admissible during independent Atlas upgrades. Only explicit recipe
    # requests are forwarded to its parser.
    import argparse
    parser = argparse.ArgumentParser(prog='workbench atlas')
    actions = parser.add_subparsers(dest='command')
    actions.add_parser('recipes', help='Query Atlas recipe evidence')
    actions.add_parser('observations', help='Inspect retained observations across families')
    actions.add_parser('scans', help='Import, inspect and share completed Atlas scans')
    if arguments:
        parser.parse_args(arguments)
    else:
        parser.print_help()
    return 0


def observations(argv, *, context):
    context.check_cancelled()
    from workbench_atlas_observations.cli import main
    return main(argv, context=context)


def scans(argv, *, context):
    context.check_cancelled()
    from workbench_atlas_recipe_health.scan_cli import main
    return main(argv, context=context)
