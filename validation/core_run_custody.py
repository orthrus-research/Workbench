"""Source-available Core host for retained repository validation runs.

Validation starts from the source checkout, including when optional modules or
the installed Workbench environment are unavailable. Keep that composition
here; the validation scheduler still owns suite admission and result meaning.
"""

from __future__ import annotations

from pathlib import Path
import sys


def allocate_validation_run(root: Path, run_id: str):
    """Register a fresh run at its historical path before exposing the tree."""

    source_root = Path(__file__).resolve().parents[1]
    for source in (source_root / "api/src", source_root / "core/src"):
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))

    from workbench_core.host_filesystem import secure_private_path
    from workbench_core.output_routing import _private_directory
    from workbench_core.user_config_home import default_user_config_home
    from workbench_core.working_allocations import CoreWorkingAllocations

    selected_root = Path(root).resolve(strict=True)
    runs = selected_root / ".workbench/validation/runs"
    _private_directory(runs)
    # Older validation runs used a 0755 parent. Protect the parent in place;
    # its historical children and their exact paths remain unchanged.
    secure_private_path(runs, directory=True)
    host = CoreWorkingAllocations(
        workspace=selected_root,
        configuration_home=default_user_config_home(),
        locations={"evidence": runs},
        location_sources={"evidence": "repository-validation"},
        owner_id="validation",
    )
    allocation = host.allocate(
        "python-suite-run", run_id, requested_path=runs / run_id,
    )
    return host, allocation


__all__ = ["allocate_validation_run"]
