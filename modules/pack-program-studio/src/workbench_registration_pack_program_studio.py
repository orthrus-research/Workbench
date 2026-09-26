"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'pack-program-studio', version('workbench-pack-program-studio'),
        capabilities=(
            Capability('pack-program-studio.groovy', ('groovy',), 'workbench_registration_pack_program_studio:groovy', 'Run groovy'),
        ),
        requires=('material-semantics', 'project-intelligence'),
    )


def groovy(argv, *, context):
    context.check_cancelled()
    from workbench_pack_program_studio.cli import main
    from workbench_api.resources import repository_root
    if argv and argv[0] == 'session':
        from workbench_api.working_allocations import working_allocations
        return main(
            argv, root=repository_root(__file__),
            session_custody=working_allocations(),
        )
    return main(argv, root=repository_root(__file__))
