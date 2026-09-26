"""Core custody for the historical Packwiz V2 source scratch location."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
from typing import Iterator
from uuid import uuid4

from workbench_api.temporary_leases import TemporaryScratchError

from .temporary_leases import CoreTemporaryLeases, TemporaryLeaseError


_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class CorePackwizScratch:
    """Allocate one registered source copy and retain it for reviewed cleanup."""

    def __init__(self, *, configuration_home: Path, owner_id: str = "workbench-shell"):
        if not isinstance(configuration_home, Path) or not configuration_home.is_absolute():
            raise TemporaryScratchError("select an absolute Core configuration home")
        self.configuration_home = configuration_home
        self.owner_id = owner_id

    @contextmanager
    def packwiz_source(
        self, *, workspace: Path, state_root: Path, plan_digest: str,
    ) -> Iterator[Path]:
        if (not isinstance(workspace, Path) or not workspace.is_absolute()
                or not isinstance(state_root, Path) or not state_root.is_absolute()
                or not isinstance(plan_digest, str) or _DIGEST.fullmatch(plan_digest) is None):
            raise TemporaryScratchError("Packwiz source scratch identity is invalid")
        parent = state_root / "staging" / "packwiz-v2"
        leases = CoreTemporaryLeases(
            workspace=workspace, configuration_home=self.configuration_home,
            locations={"packwiz-v2": parent}, owner_id=self.owner_id,
        )
        try:
            reference = leases.allocate(
                "packwiz-v2", f"packwiz-{plan_digest[:16]}-{uuid4().hex}",
            )
            with leases.execution(reference):
                try:
                    yield reference.path
                except BaseException as exc:
                    try:
                        leases.retain(reference, outcome="failed")
                    except (OSError, TemporaryLeaseError) as retention_error:
                        exc.add_note("Core scratch retention could not be recorded: " + str(retention_error))
                    raise
                else:
                    leases.retain(reference, outcome="completed")
        except TemporaryLeaseError as exc:
            raise TemporaryScratchError(str(exc)) from exc


__all__ = ["CorePackwizScratch"]
