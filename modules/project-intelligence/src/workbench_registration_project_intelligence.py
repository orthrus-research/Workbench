"""Installed capability declarations; handlers are loaded only on use."""

from importlib.metadata import version
from workbench_api import Capability, Module


def module():
    return Module(
        'project-intelligence', version('workbench-project-intelligence'),
        capabilities=(
            Capability('project-intelligence.acquire', ('project', 'acquire'), 'workbench_registration_project_intelligence:acquire', 'Acquire an explicitly selected profile'),
            Capability('project-intelligence.doctor', ('workspace', 'inspect'), 'workbench_registration_project_intelligence:doctor', 'Inspect the selected workspace without mutation'),
        ),
        requires=(),
    )


def doctor(argv, *, context):
    import argparse
    import json
    from pathlib import Path
    from workbench_project_intelligence.workspace_doctor import new_report
    context.check_cancelled()
    parser = argparse.ArgumentParser(prog='workbench doctor')
    parser.add_argument('workspace', nargs='?', type=Path, default=context.workspace)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    report = new_report(args.workspace, requested_path=args.workspace)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def acquire(argv, *, context):
    context.check_cancelled()
    from workbench_api.profiles import profile_resources
    from workbench_project_intelligence.project_acquisition import main
    return main(argv, profiles=profile_resources("acquisition"), default_state_root=context.state_root)
