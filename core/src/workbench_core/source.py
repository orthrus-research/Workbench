"""Conservative local source-location resolution for terminal navigation."""

from __future__ import annotations

from collections import defaultdict
import os
from pathlib import Path
from typing import Any, Mapping


SOURCE_SUFFIXES = {".java", ".groovy", ".kt", ".kts", ".py"}
SKIP_PARTS = {
    ".git",
    ".workbench",
    ".deconstruction",
    ".gradle",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "out",
    "target",
}
DEFAULT_MAX_SCANNED_ENTRIES = 100_000
DEFAULT_MAX_SOURCE_FILES = 50_000


class SourceIndex:
    """Resolve only exact paths or unique in-workspace filename matches."""

    def __init__(
        self,
        root: Path,
        *,
        max_scanned_entries: int = DEFAULT_MAX_SCANNED_ENTRIES,
        max_source_files: int = DEFAULT_MAX_SOURCE_FILES,
    ) -> None:
        if max_scanned_entries < 1 or max_source_files < 1:
            raise ValueError("source index bounds must be positive")
        self.root = root.expanduser().resolve()
        self.max_scanned_entries = max_scanned_entries
        self.max_source_files = max_source_files
        self._by_name: dict[str, list[Path]] | None = None
        self._limited = False
        self._scanned_entries = 0

    def presentation_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """Add a non-contract presentation field without mutating candidates."""

        value = dict(event)
        raw = value.get("source_locators")
        if not isinstance(raw, list):
            return value
        value["_resolved_source_locators"] = [
            self.resolve(locator) if isinstance(locator, Mapping) else locator
            for locator in raw
        ]
        return value

    # Compatibility spelling for callers that explicitly want a UI projection.
    resolve_event = presentation_event

    def resolve(self, locator: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(locator)
        candidate = value.get("candidate") or value.get("path") or value.get("file")
        if not isinstance(candidate, str) or not candidate:
            value["resolution"] = "unresolved"
            return value
        normalized = candidate.replace("\\", "/")
        path = Path(candidate).expanduser()
        exact: Path | None = None
        if path.is_absolute() and path.is_file():
            exact = path.resolve()
        elif (self.root / path).is_file():
            exact = (self.root / path).resolve()
        if exact is not None:
            value["path"] = self._display(exact)
            value["resolution"] = "exact"
            return value

        matches = list(self._names().get(Path(normalized).name, ()))
        suffix_parts = tuple(part for part in normalized.split("/") if part)
        if len(suffix_parts) > 1:
            narrowed = [
                item
                for item in matches
                if tuple(item.relative_to(self.root).parts[-len(suffix_parts) :])
                == suffix_parts
            ]
            if narrowed:
                matches = narrowed
        if len(matches) == 1 and not self._limited:
            value["path"] = self._display(matches[0])
            value["resolution"] = "unique"
        elif len(matches) > 1:
            value["resolution"] = "ambiguous"
            value["matches"] = [self._display(item) for item in matches[:20]]
            if len(matches) > 20:
                value["match_count"] = len(matches)
        elif matches and self._limited:
            value["resolution"] = "indeterminate-index-limited"
            value["matches"] = [self._display(matches[0])]
        elif self._limited:
            value["resolution"] = "unresolved-index-limited"
        else:
            value["resolution"] = "unresolved"
        return value

    def _names(self) -> dict[str, list[Path]]:
        if self._by_name is not None:
            return self._by_name
        result: dict[str, list[Path]] = defaultdict(list)
        pending = [self.root]
        source_files = 0
        while pending:
            directory = pending.pop()
            children: list[Path] = []
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    self._scanned_entries += 1
                    if self._scanned_entries > self.max_scanned_entries:
                        self._limited = True
                        pending.clear()
                        break
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name not in SKIP_PARTS:
                                children.append(Path(entry.path))
                            continue
                        if (
                            entry.is_file(follow_symlinks=False)
                            and Path(entry.name).suffix.casefold() in SOURCE_SUFFIXES
                        ):
                            result[entry.name].append(Path(entry.path).resolve())
                            source_files += 1
                            if source_files >= self.max_source_files:
                                self._limited = True
                                pending.clear()
                                children.clear()
                                break
                    except OSError:
                        continue
            pending.extend(reversed(sorted(children, key=lambda path: path.name)))
        self._by_name = {
            key: sorted(values) for key, values in result.items()
        }
        return self._by_name

    def _display(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


__all__ = [
    "DEFAULT_MAX_SCANNED_ENTRIES",
    "DEFAULT_MAX_SOURCE_FILES",
    "SOURCE_SUFFIXES",
    "SourceIndex",
]
