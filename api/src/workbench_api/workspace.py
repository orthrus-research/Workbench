"""Optional workspace-inspection provider boundary."""

from importlib import metadata
from pathlib import Path

from .modules import ModuleError


def inspect_workspace(workspace: Path):
    entries = tuple(metadata.entry_points(group="workbench.workspace_inspectors"))
    if len(entries) != 1:
        raise ModuleError("workspace inspection requires exactly one installed inspector")
    try:
        result = entries[0].load()(workspace, requested_path=workspace)
        if not isinstance(result, dict) or not isinstance(result["findings"], list):
            raise ValueError("invalid workspace report")
        for value in (result["summary"]["status"], result["target"]["workspace"]["kind"], result["target"]["workspace"]["root"]):
            if not isinstance(value, str):
                raise ValueError("invalid workspace report identity")
        if not isinstance(result["target"]["platform"], dict) or any(not isinstance(row.get("id"), str) for row in result["findings"]):
            raise ValueError("invalid workspace report findings")
        return result
    except (Exception, SystemExit) as exc:
        raise ModuleError(f"workspace inspector failed: {type(exc).__name__}") from exc
