"""Core's archive transport implementation behind the module API port."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import archive_exchange


class CoreArchiveExchange:
    def __init__(self, *, check_cancelled: Callable[[], None] | None = None):
        self.check_cancelled = check_cancelled or (lambda: None)

    def _cancelled(self, owner_cancelled: Callable[[], bool]) -> Callable[[], bool]:
        def poll() -> bool:
            self.check_cancelled()
            return owner_cancelled()

        return poll

    def build_manifest(
        self, members: Sequence[Mapping[str, object]], *, metadata: dict
    ) -> dict:
        self.check_cancelled()
        return archive_exchange.build_manifest(members, metadata=metadata)

    def verify_directory(
        self, directory: Path, *, cancelled: Callable[[], bool] = lambda: False
    ) -> dict:
        return archive_exchange.verify_directory(
            directory, cancelled=self._cancelled(cancelled)
        )

    def import_archive(
        self, archive: Path, destination: Path, *,
        validate: Callable[[Path, dict], dict] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict:
        return archive_exchange.import_archive(
            archive, destination, validate=validate,
            cancelled=self._cancelled(cancelled),
        )

    def export_archive(
        self, destination: Path, members: Mapping[str, Path], *,
        metadata: dict, expected_manifest: dict | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> dict:
        return archive_exchange.export_archive(
            destination, members, metadata=metadata,
            expected_manifest=expected_manifest,
            cancelled=self._cancelled(cancelled),
        )


__all__ = ["CoreArchiveExchange"]
