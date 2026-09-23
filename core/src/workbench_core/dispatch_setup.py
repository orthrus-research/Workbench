"""Apply explicitly saved user setup before dispatch."""
from __future__ import annotations
import sys

def _activate_user_setup(arguments: list[str]) -> bool:
    """Load physical user defaults while preserving explicit CLI authority."""

    if (
        arguments[:1] in (["setup"], ["repair"], ["tooling"], ["version"], ["--version"])
        or "--help" in arguments
        or "-h" in arguments
    ):
        return True
    from workbench_core.setup_cli import (
        SetupError,
        apply_setup_environment_defaults,
        default_setup_record_path,
        load_setup_record,
    )

    try:
        record = load_setup_record(default_setup_record_path())
        if record is not None:
            apply_setup_environment_defaults(record)
        return True
    except (OSError, SetupError, ValueError) as exc:
        print(
            "Workbench setup is invalid: "
            f"{exc}; run workbench setup --check or workbench setup --repair",
            file=sys.stderr,
        )
        return False
