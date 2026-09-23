"""Apply explicit, disposable compatibility patches to a runtime projection."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Sequence
import zipfile


PATCH_FORMAT = "workbench-runtime-compatibility-patch-v1"
TEXT_PATCH_FORMAT = "workbench-runtime-compatibility-text-patch-v1"
FILE_OVERLAY_FORMAT = "workbench-runtime-compatibility-file-overlay-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT_PATCH_BYTES = 16 * 1024 * 1024
MAX_REPLACEMENT_BYTES = 64 * 1024
MAX_FILE_OVERLAY_BYTES = 16 * 1024 * 1024


class RuntimeCompatibilityError(ValueError):
    """Raised when a declared disposable compatibility patch is unsafe."""


def _sha256_bytes(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeCompatibilityError(
            f"compatibility patch {label} must be a non-empty path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise RuntimeCompatibilityError(
            f"compatibility patch {label} must stay within the projection"
        )
    normalized = path.as_posix()
    if normalized in {"", "."} or normalized != value:
        raise RuntimeCompatibilityError(
            f"compatibility patch {label} must be normalized"
        )
    return normalized


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RuntimeCompatibilityError(
            f"compatibility patch {label} must be a SHA-256"
        )
    return value


def _replacement(
    target: dict[str, Any],
    *,
    length_preserving: bool,
) -> dict[str, Any]:
    find_utf8 = target.get("find_utf8")
    replace_utf8 = target.get("replace_utf8")
    expected_matches = target.get("expected_matches")
    if (
        not isinstance(find_utf8, str)
        or not find_utf8
        or not isinstance(replace_utf8, str)
        or not replace_utf8
    ):
        raise RuntimeCompatibilityError(
            "compatibility patch UTF-8 replacement must be non-empty"
        )
    find_bytes = find_utf8.encode("utf-8")
    replace_bytes = replace_utf8.encode("utf-8")
    if (
        len(find_bytes) > MAX_REPLACEMENT_BYTES
        or len(replace_bytes) > MAX_REPLACEMENT_BYTES
    ):
        raise RuntimeCompatibilityError(
            "compatibility patch UTF-8 replacement exceeds the byte limit"
        )
    if length_preserving and len(find_bytes) != len(replace_bytes):
        raise RuntimeCompatibilityError(
            "archive compatibility patch replacement must be length preserving"
        )
    if type(expected_matches) is not int or expected_matches <= 0:
        raise RuntimeCompatibilityError(
            "compatibility patch expected_matches must be positive"
        )
    return {
        "find_utf8": find_utf8,
        "replace_utf8": replace_utf8,
        "expected_matches": expected_matches,
    }


def _load_patch(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeCompatibilityError(
            f"compatibility patch is not a regular file: {path}"
        )
    try:
        raw = path.read_bytes()
        patch = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeCompatibilityError(
            f"compatibility patch cannot be read: {path}"
        ) from exc
    if (
        not isinstance(patch, dict)
        or patch.get("format") not in {
            PATCH_FORMAT,
            TEXT_PATCH_FORMAT,
            FILE_OVERLAY_FORMAT,
        }
        or patch.get("schema_version") != 1
        or not isinstance(patch.get("patch_id"), str)
        or not patch["patch_id"].strip()
    ):
        raise RuntimeCompatibilityError(
            "compatibility patch has an unsupported identity"
        )
    target = patch.get("target")
    if not isinstance(target, dict):
        raise RuntimeCompatibilityError(
            "compatibility patch lacks a target"
        )
    normalized_target = {
        "path": _relative_path(target.get("path"), "target.path"),
    }
    if patch["format"] == FILE_OVERLAY_FORMAT:
        if target.get("must_be_absent") is not True:
            raise RuntimeCompatibilityError(
                "compatibility file overlay target must require absence"
            )
        source = patch.get("source")
        if not isinstance(source, dict):
            raise RuntimeCompatibilityError(
                "compatibility file overlay lacks a source"
            )
        normalized_target["must_be_absent"] = True
        normalized_source = {
            "path": _relative_path(source.get("path"), "source.path"),
            "sha256": _digest(source.get("sha256"), "source.sha256"),
        }
        patch_kind = "file-overlay"
    elif patch["format"] == PATCH_FORMAT:
        normalized_target["sha256"] = _digest(
            target.get("sha256"),
            "target.sha256",
        )
        normalized_target.update({
            "entry": _relative_path(target.get("entry"), "target.entry"),
            "entry_sha256": _digest(
                target.get("entry_sha256"),
                "target.entry_sha256",
            ),
            **_replacement(target, length_preserving=True),
        })
        patch_kind = "archive-entry-replacement"
    else:
        normalized_target["sha256"] = _digest(
            target.get("sha256"),
            "target.sha256",
        )
        normalized_target.update(
            _replacement(target, length_preserving=False)
        )
        patch_kind = "text-file-replacement"
    normalized = dict(patch)
    normalized["target"] = normalized_target
    if patch["format"] == FILE_OVERLAY_FORMAT:
        normalized["source"] = normalized_source
    normalized["patch_kind"] = patch_kind
    return normalized, _sha256_bytes(raw)


def _projection_target(projection: Path, relative: str) -> Path:
    root = projection.resolve()
    candidate = root / Path(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCompatibilityError(
                f"compatibility patch target contains a symbolic link: "
                f"{relative}"
            )
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeCompatibilityError(
            "compatibility patch target escapes the projection"
        ) from exc
    if target.is_symlink() or not target.is_file():
        raise RuntimeCompatibilityError(
            f"compatibility patch target is not a regular file: {relative}"
        )
    return target


def _new_projection_target(projection: Path, relative: str) -> Path:
    root = projection.resolve()
    candidate = root / Path(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCompatibilityError(
                f"compatibility overlay target contains a symbolic link: "
                f"{relative}"
            )
    target = candidate.absolute()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeCompatibilityError(
            "compatibility overlay target escapes the projection"
        ) from exc
    if target.exists() or target.is_symlink():
        raise RuntimeCompatibilityError(
            f"compatibility overlay target already exists: {relative}"
        )
    if not target.parent.is_dir():
        raise RuntimeCompatibilityError(
            f"compatibility overlay target parent is absent: {relative}"
        )
    return target


def _overlay_source(specification: Path, relative: str) -> Path:
    root = specification.parent.resolve()
    candidate = root / Path(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current /= part
        if current.is_symlink():
            raise RuntimeCompatibilityError(
                f"compatibility overlay source contains a symbolic link: "
                f"{relative}"
            )
    source = candidate.resolve()
    try:
        source.relative_to(root)
    except ValueError as exc:
        raise RuntimeCompatibilityError(
            "compatibility overlay source escapes its specification directory"
        ) from exc
    if source.is_symlink() or not source.is_file():
        raise RuntimeCompatibilityError(
            f"compatibility overlay source is not a regular file: {relative}"
        )
    return source


def _rewrite_jar_entry(
    jar_path: Path,
    *,
    entry_name: str,
    entry_sha256: str,
    find_utf8: str,
    replace_utf8: str,
    expected_matches: int,
) -> dict[str, Any]:
    before_jar = _sha256_path(jar_path)
    if not zipfile.is_zipfile(jar_path):
        raise RuntimeCompatibilityError(
            f"compatibility patch target is not a ZIP/JAR: {jar_path}"
        )
    try:
        with zipfile.ZipFile(jar_path, "r") as source:
            names = source.namelist()
            if entry_name not in names:
                raise RuntimeCompatibilityError(
                    f"compatibility patch entry is missing: {entry_name}"
                )
            entry_bytes = source.read(entry_name)
            actual_entry_sha256 = _sha256_bytes(entry_bytes)
            if actual_entry_sha256 != entry_sha256:
                raise RuntimeCompatibilityError(
                    "compatibility patch entry identity changed"
                )
            old = find_utf8.encode("utf-8")
            new = replace_utf8.encode("utf-8")
            matches = entry_bytes.count(old)
            if matches != expected_matches:
                raise RuntimeCompatibilityError(
                    "compatibility patch match count changed: "
                    f"expected {expected_matches}, found {matches}"
                )
            patched_entry = entry_bytes.replace(old, new)
            if patched_entry == entry_bytes:
                raise RuntimeCompatibilityError(
                    "compatibility patch produced no byte change"
                )
            if patched_entry.count(old) != 0:
                raise RuntimeCompatibilityError(
                    "compatibility patch left the selected bytes unchanged"
                )
            entries = [
                (info, source.read(info.filename))
                for info in source.infolist()
            ]
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"compatibility patch JAR cannot be read: {jar_path}"
        ) from exc

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{jar_path.name}.",
            suffix=".compat.tmp",
            dir=jar_path.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
        with zipfile.ZipFile(
            temporary_name,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as destination:
            for info, contents in entries:
                if info.filename == entry_name:
                    contents = patched_entry
                destination.writestr(info, contents)
        os.replace(temporary_name, jar_path)
        temporary_name = None
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeCompatibilityError(
            f"compatibility patch JAR cannot be published: {jar_path}"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass

    after_jar = _sha256_path(jar_path)
    return {
        "operation": "archive-entry-replacement",
        "entry": entry_name,
        "entry_sha256_before": entry_sha256,
        "entry_sha256_after": _sha256_bytes(patched_entry),
        "jar_sha256_before": before_jar,
        "jar_sha256_after": after_jar,
        "find_utf8": find_utf8,
        "replace_utf8": replace_utf8,
        "matches": expected_matches,
    }


def _rewrite_text_file(
    target: Path,
    *,
    find_utf8: str,
    replace_utf8: str,
    expected_matches: int,
) -> dict[str, Any]:
    try:
        before = target.read_bytes()
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"compatibility text target cannot be read: {target}"
        ) from exc
    if len(before) > MAX_TEXT_PATCH_BYTES:
        raise RuntimeCompatibilityError(
            "compatibility text target exceeds the byte limit"
        )
    try:
        before.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeCompatibilityError(
            "compatibility text target is not valid UTF-8"
        ) from exc
    old = find_utf8.encode("utf-8")
    new = replace_utf8.encode("utf-8")
    matches = before.count(old)
    if matches != expected_matches:
        raise RuntimeCompatibilityError(
            "compatibility patch match count changed: "
            f"expected {expected_matches}, found {matches}"
        )
    after_size = len(before) + matches * (len(new) - len(old))
    if after_size > MAX_TEXT_PATCH_BYTES:
        raise RuntimeCompatibilityError(
            "compatibility text result exceeds the byte limit"
        )
    after = before.replace(old, new)
    if after == before:
        raise RuntimeCompatibilityError(
            "compatibility patch produced no byte change"
        )
    if after.count(old) != 0:
        raise RuntimeCompatibilityError(
            "compatibility patch left the selected bytes unchanged"
        )
    temporary_name: str | None = None
    try:
        mode = target.stat().st_mode & 0o777
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".compat.tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(after)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, target)
        temporary_name = None
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"compatibility text target cannot be published: {target}"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
    return {
        "operation": "text-file-replacement",
        "file_sha256_before": _sha256_bytes(before),
        "file_sha256_after": _sha256_bytes(after),
        "file_size_before": len(before),
        "file_size_after": len(after),
        "find_utf8": find_utf8,
        "replace_utf8": replace_utf8,
        "matches": expected_matches,
    }


def _install_file_overlay(
    source: Path,
    target: Path,
    *,
    expected_sha256: str,
) -> dict[str, Any]:
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"compatibility overlay source cannot be read: {source}"
        ) from exc
    if len(payload) > MAX_FILE_OVERLAY_BYTES:
        raise RuntimeCompatibilityError(
            "compatibility overlay source exceeds the byte limit"
        )
    observed_sha256 = _sha256_bytes(payload)
    if observed_sha256 != expected_sha256:
        raise RuntimeCompatibilityError(
            "compatibility overlay source identity changed"
        )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".compat.tmp",
            dir=target.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o644)
        os.link(temporary_name, target)
        Path(temporary_name).unlink()
        temporary_name = None
    except OSError as exc:
        raise RuntimeCompatibilityError(
            f"compatibility overlay cannot be published: {target}"
        ) from exc
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except OSError:
                pass
    return {
        "operation": "file-overlay",
        "source_sha256": observed_sha256,
        "source_size": len(payload),
        "file_sha256_after": observed_sha256,
        "file_size_after": len(payload),
    }


def apply_compatibility_patches(
    projection: Path | str,
    patch_paths: Sequence[Path | str] = (),
) -> list[dict[str, Any]]:
    """Apply verified patches to one disposable launcher projection."""

    root = Path(projection).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise RuntimeCompatibilityError(
            "compatibility patch projection is not a regular directory"
        )
    records: list[dict[str, Any]] = []
    targets: set[str] = set()
    for raw_path in patch_paths:
        patch_path = Path(raw_path).expanduser().resolve()
        patch, spec_sha256 = _load_patch(patch_path)
        target_spec = patch["target"]
        target_relative = target_spec["path"]
        if target_relative in targets:
            raise RuntimeCompatibilityError(
                f"compatibility patch target repeated: {target_relative}"
            )
        targets.add(target_relative)
        if patch["patch_kind"] == "file-overlay":
            target = _new_projection_target(root, target_relative)
            source = _overlay_source(
                patch_path,
                patch["source"]["path"],
            )
            rewrite = _install_file_overlay(
                source,
                target,
                expected_sha256=patch["source"]["sha256"],
            )
            rewrite["source_path"] = patch["source"]["path"]
        else:
            target = _projection_target(root, target_relative)
            before_target_sha256 = _sha256_path(target)
            if before_target_sha256 != target_spec["sha256"]:
                raise RuntimeCompatibilityError(
                    "compatibility patch target identity changed: "
                    f"{target_relative}"
                )
        if patch["patch_kind"] == "archive-entry-replacement":
            rewrite = _rewrite_jar_entry(
                target,
                entry_name=target_spec["entry"],
                entry_sha256=target_spec["entry_sha256"],
                find_utf8=target_spec["find_utf8"],
                replace_utf8=target_spec["replace_utf8"],
                expected_matches=target_spec["expected_matches"],
            )
        elif patch["patch_kind"] == "text-file-replacement":
            rewrite = _rewrite_text_file(
                target,
                find_utf8=target_spec["find_utf8"],
                replace_utf8=target_spec["replace_utf8"],
                expected_matches=target_spec["expected_matches"],
            )
        records.append({
            "patch_id": patch["patch_id"],
            "spec_sha256": spec_sha256,
            "description": patch.get("description"),
            "target_path": target_relative,
            **rewrite,
        })
    return records
