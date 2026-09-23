"""CLI for one bounded observed-effect comparison."""

from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import secrets
import stat
import sys
from typing import Any, Mapping, NoReturn, Sequence

from workbench_api.canonical import CanonicalJsonError, parse_json_strict

from .adapters import SYNTHETIC_FIXTURE_ADAPTERS
from .comparator import (
    BoundedEffectComparisonError,
    DEFAULT_BOUNDS,
    compare_bounded_effects,
)


_FD_RELATIVE_READ_AVAILABLE = (
    os.name == "posix"
    and hasattr(os, "O_NOFOLLOW")
    and {os.open, os.stat}.issubset(os.supports_dir_fd)
    and os.stat in os.supports_follow_symlinks
)
_FD_RELATIVE_PUBLICATION_AVAILABLE = (
    _FD_RELATIVE_READ_AVAILABLE
    and {os.link, os.mkdir, os.unlink}.issubset(os.supports_dir_fd)
)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {_terminal(message)}\n")


def _terminal(value: Any) -> str:
    return "".join(
        character if character in "\n\t" or ord(character) >= 32 else "�"
        for character in str(value)
    )


def _resolve(path: Path, *, root: Path) -> Path:
    selected = path if path.is_absolute() else root / path
    return Path(os.path.abspath(os.fspath(selected)))


def _fd_relative_custody_available(*, publication: bool) -> bool:
    return (
        _FD_RELATIVE_PUBLICATION_AVAILABLE
        if publication
        else _FD_RELATIVE_READ_AVAILABLE
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _path_error(
    *,
    output: bool,
    label: str,
    selected: Path,
    message: str,
    code: str | None = None,
) -> BoundedEffectComparisonError:
    return BoundedEffectComparisonError(
        code or ("output.write" if output else "input.unavailable"),
        "/output" if output else f"/{label}",
        f"{message}: {selected}",
    )


class _RootCustody:
    __slots__ = ("descriptor", "identity", "path")

    def __init__(self, path: Path, descriptor: int, identity: tuple[int, int]) -> None:
        self.path = path
        self.descriptor = descriptor
        self.identity = identity

    def close(self) -> None:
        if self.descriptor >= 0:
            descriptor = self.descriptor
            self.descriptor = -1
            os.close(descriptor)


def _pin_root(
    root: Path,
    *,
    output: bool,
    label: str,
) -> _RootCustody:
    if not _fd_relative_custody_available(publication=output):
        raise _path_error(
            output=output,
            label=label,
            selected=root,
            message="fd-relative POSIX filesystem custody is unavailable",
            code="output.filesystem" if output else "input.filesystem",
        )
    descriptor = -1
    try:
        visible = os.stat(root, follow_symlinks=False)
        if stat.S_ISLNK(visible.st_mode):
            raise _path_error(
                output=output,
                label=label,
                selected=root,
                message="symbolic-link CLI root is refused",
                code="output.symlink" if output else "input.symlink",
            )
        if not stat.S_ISDIR(visible.st_mode):
            raise _path_error(
                output=output,
                label=label,
                selected=root,
                message="CLI root is not a directory",
                code="output.write" if output else "input.type",
            )
        descriptor = os.open(root, _directory_flags())
        opened = os.fstat(descriptor)
    except BoundedEffectComparisonError:
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise _path_error(
            output=output,
            label=label,
            selected=root,
            message=f"cannot pin the CLI root: {exc}",
            code="output.changed" if output else "input.changed",
        ) from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or _directory_identity(visible) != _directory_identity(opened)
    ):
        os.close(descriptor)
        raise _path_error(
            output=output,
            label=label,
            selected=root,
            message="CLI root identity changed while being pinned",
            code="output.changed" if output else "input.changed",
        )
    return _RootCustody(root, descriptor, _directory_identity(opened))


def _open_pinned_parent(
    selected: Path,
    *,
    custody: _RootCustody,
    create: bool,
    output: bool,
    label: str,
) -> tuple[int, tuple[int, int]]:
    """Pin ``root`` and traverse one descendant parent without symlinks."""

    if not _fd_relative_custody_available(publication=output):
        raise _path_error(
            output=output,
            label=label,
            selected=selected,
            message="fd-relative POSIX filesystem custody is unavailable",
            code="output.filesystem" if output else "input.filesystem",
        )
    root = custody.path
    if (
        custody.descriptor < 0
        or not root.is_absolute()
        or not selected.is_absolute()
        or selected.name in {"", ".", ".."}
    ):
        raise _path_error(
            output=output,
            label=label,
            selected=selected,
            message="path has no safe final filename component",
            code="output.write" if output else "input.type",
        )
    try:
        relative = selected.relative_to(root)
    except ValueError:
        raise _path_error(
            output=output,
            label=label,
            selected=selected,
            message=f"path is outside the pinned CLI root {root}",
            code="output.write" if output else "input.unavailable",
        ) from None

    descriptor = -1
    try:
        pinned_root = os.fstat(custody.descriptor)
        if (
            not stat.S_ISDIR(pinned_root.st_mode)
            or _directory_identity(pinned_root) != custody.identity
        ):
            raise _path_error(
                output=output,
                label=label,
                selected=root,
                message="pinned CLI root capability changed",
                code="output.changed" if output else "input.changed",
            )
        descriptor = os.dup(custody.descriptor)

        walked = root
        for component in relative.parent.parts:
            walked /= component
            created = False
            try:
                visible = os.stat(
                    component,
                    dir_fd=descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                if not create:
                    raise _path_error(
                        output=output,
                        label=label,
                        selected=walked,
                        message="path ancestor is unavailable",
                    ) from None
                try:
                    os.mkdir(component, 0o777, dir_fd=descriptor)
                    created = True
                    _fsync_directory_descriptor(descriptor)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise _path_error(
                        output=output,
                        label=label,
                        selected=walked,
                        message=f"cannot create path ancestor: {exc}",
                    ) from exc
                try:
                    visible = os.stat(
                        component,
                        dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise _path_error(
                        output=output,
                        label=label,
                        selected=walked,
                        message=f"cannot inspect created path ancestor: {exc}",
                    ) from exc
            except OSError as exc:
                raise _path_error(
                    output=output,
                    label=label,
                    selected=walked,
                    message=f"cannot inspect path ancestor: {exc}",
                ) from exc

            if stat.S_ISLNK(visible.st_mode):
                raise _path_error(
                    output=output,
                    label=label,
                    selected=walked,
                    message="symbolic-link path ancestor is refused",
                    code="output.symlink" if output else "input.symlink",
                )
            if not stat.S_ISDIR(visible.st_mode):
                raise _path_error(
                    output=output,
                    label=label,
                    selected=walked,
                    message="path ancestor is not a directory",
                    code="output.write" if output else "input.type",
                )
            child = -1
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
                opened = os.fstat(child)
            except OSError as exc:
                if child >= 0:
                    os.close(child)
                changed = not created or exc.errno in {errno.ELOOP, errno.ENOTDIR}
                raise _path_error(
                    output=output,
                    label=label,
                    selected=walked,
                    message=f"path ancestor changed while being pinned: {exc}",
                    code=("output.changed" if output else "input.changed")
                    if changed
                    else None,
                ) from exc
            if (
                not stat.S_ISDIR(opened.st_mode)
                or _directory_identity(visible) != _directory_identity(opened)
            ):
                os.close(child)
                raise _path_error(
                    output=output,
                    label=label,
                    selected=walked,
                    message="path ancestor identity changed while being pinned",
                    code="output.changed" if output else "input.changed",
                )
            os.close(descriptor)
            descriptor = child
        opened_parent = os.fstat(descriptor)
        if not stat.S_ISDIR(opened_parent.st_mode):
            raise _path_error(
                output=output,
                label=label,
                selected=selected.parent,
                message="pinned parent is not a directory",
                code="output.changed" if output else "input.changed",
            )
        return descriptor, _directory_identity(opened_parent)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _require_visible_parent(
    selected: Path,
    expected: tuple[int, int],
    *,
    custody: _RootCustody,
    output: bool,
    label: str,
) -> None:
    visible_custody: _RootCustody | None = None
    descriptor = -1
    try:
        try:
            visible_custody = _pin_root(
                custody.path,
                output=output,
                label=label,
            )
            if visible_custody.identity != custody.identity:
                raise _path_error(
                    output=output,
                    label=label,
                    selected=custody.path,
                    message="CLI root namespace identity changed during access",
                    code="output.changed" if output else "input.changed",
                )
            descriptor, observed = _open_pinned_parent(
                selected,
                custody=visible_custody,
                create=False,
                output=output,
                label=label,
            )
        except BoundedEffectComparisonError as exc:
            raise _path_error(
                output=output,
                label=label,
                selected=selected.parent,
                message=f"parent namespace changed during access ({exc.message})",
                code="output.changed" if output else "input.changed",
            ) from exc
        if observed != expected:
            raise _path_error(
                output=output,
                label=label,
                selected=selected.parent,
                message="parent namespace identity changed during access",
                code="output.changed" if output else "input.changed",
            )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if visible_custody is not None:
            visible_custody.close()


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _read_json(
    path: Path,
    *,
    root: Path,
    label: str,
    custody: _RootCustody | None = None,
) -> dict[str, Any]:
    selected = _resolve(path, root=root)
    owned_custody: _RootCustody | None = None
    active_custody = custody
    if active_custody is None:
        owned_custody = _pin_root(root, output=False, label=label)
        active_custody = owned_custody
    elif active_custody.path != root:
        raise _path_error(
            output=False,
            label=label,
            selected=root,
            message="CLI root does not match its pinned custody",
            code="input.changed",
        )
    parent = -1
    descriptor = -1
    try:
        parent, parent_identity = _open_pinned_parent(
            selected,
            custody=active_custody,
            create=False,
            output=False,
            label=label,
        )
        try:
            before = os.stat(
                selected.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise BoundedEffectComparisonError(
                "input.unavailable",
                f"/{label}",
                f"cannot inspect {selected}: {exc}",
            ) from exc
        if stat.S_ISLNK(before.st_mode):
            raise BoundedEffectComparisonError(
                "input.symlink",
                f"/{label}",
                f"symbolic-link input is refused: {selected}",
            )
        if not stat.S_ISREG(before.st_mode):
            raise BoundedEffectComparisonError(
                "input.type",
                f"/{label}",
                f"input is not a regular file: {selected}",
            )
        if before.st_size > DEFAULT_BOUNDS.max_input_bytes:
            raise BoundedEffectComparisonError(
                "bounds.input-bytes",
                f"/{label}",
                f"input exceeds {DEFAULT_BOUNDS.max_input_bytes} bytes: {selected}",
            )
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        try:
            descriptor = os.open(selected.name, flags, dir_fd=parent)
        except OSError as exc:
            raise BoundedEffectComparisonError(
                "input.changed",
                f"/{label}",
                f"input changed between inspection and no-follow open: {selected}: {exc}",
            ) from exc
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or _file_identity(before) != _file_identity(opened)
            ):
                raise BoundedEffectComparisonError(
                    "input.changed",
                    f"/{label}",
                    f"input changed between inspection and open: {selected}",
                )
            chunks: list[bytes] = []
            remaining = DEFAULT_BOUNDS.max_input_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            finished = os.fstat(descriptor)
        except BoundedEffectComparisonError:
            raise
        except OSError as exc:
            raise BoundedEffectComparisonError(
                "input.unavailable", f"/{label}", f"cannot read {selected}: {exc}"
            ) from exc
        finally:
            os.close(descriptor)
            descriptor = -1
        try:
            final_path = os.stat(
                selected.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise BoundedEffectComparisonError(
                "input.changed",
                f"/{label}",
                f"input changed during read: {selected}: {exc}",
            ) from exc
        if (
            len(data) > DEFAULT_BOUNDS.max_input_bytes
            or len(data) != opened.st_size
            or _file_identity(before) != _file_identity(opened)
            or _file_identity(opened) != _file_identity(finished)
            or _file_identity(finished) != _file_identity(final_path)
        ):
            raise BoundedEffectComparisonError(
                "input.changed",
                f"/{label}",
                f"input changed while being read: {selected}",
            )
        _require_visible_parent(
            selected,
            parent_identity,
            custody=active_custody,
            output=False,
            label=label,
        )
        if data.startswith(b"\xef\xbb\xbf"):
            raise BoundedEffectComparisonError(
                "json.bom", f"/{label}", f"UTF-8 BOM is unsupported: {selected}"
            )
        try:
            value = parse_json_strict(data)
        except (CanonicalJsonError, RecursionError, TypeError, UnicodeError) as exc:
            raise BoundedEffectComparisonError(
                "json.invalid",
                f"/{label}",
                f"invalid strict JSON in {selected}: {exc}",
            ) from exc
        if type(value) is not dict:
            raise BoundedEffectComparisonError(
                "json.root", f"/{label}", f"JSON root must be an object: {selected}"
            )
        return value
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent >= 0:
            os.close(parent)
        if owned_custody is not None:
            owned_custody.close()


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise OSError("short output write")
        offset += written


def _write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    root: Path,
    custody: _RootCustody | None = None,
) -> None:
    selected = _resolve(path, root=root)
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8", errors="strict")
    owned_custody: _RootCustody | None = None
    active_custody = custody
    if active_custody is None:
        owned_custody = _pin_root(root, output=True, label="output")
        active_custody = owned_custody
    elif active_custody.path != root:
        raise _path_error(
            output=True,
            label="output",
            selected=root,
            message="CLI root does not match its pinned custody",
            code="output.changed",
        )
    parent = -1
    descriptor = -1
    temporary: str | None = None
    try:
        parent, parent_identity = _open_pinned_parent(
            selected,
            custody=active_custody,
            create=True,
            output=True,
            label="output",
        )
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(128):
            temporary = ".workbench-process-studio-" + secrets.token_hex(16) + ".tmp"
            try:
                descriptor = os.open(
                    temporary,
                    flags,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                continue
            break
        else:
            raise BoundedEffectComparisonError(
                "output.write",
                "/output",
                f"cannot allocate a fresh temporary output name: {selected}",
            )
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
        _require_visible_parent(
            selected,
            parent_identity,
            custody=active_custody,
            output=True,
            label="output",
        )
        try:
            os.link(
                temporary,
                selected.name,
                src_dir_fd=parent,
                dst_dir_fd=parent,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise BoundedEffectComparisonError(
                "output.exists",
                "/output",
                f"output path must be fresh: {selected}",
            ) from exc
        os.unlink(temporary, dir_fd=parent)
        temporary = None
        _fsync_directory_descriptor(parent)
        _require_visible_parent(
            selected,
            parent_identity,
            custody=active_custody,
            output=True,
            label="output",
        )
        retained = os.stat(
            selected.name,
            dir_fd=parent,
            follow_symlinks=False,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(retained.st_mode)
            or _file_identity(retained) != _file_identity(opened)
        ):
            raise BoundedEffectComparisonError(
                "output.changed",
                "/output",
                f"published output identity changed: {selected}",
            )
    except BoundedEffectComparisonError:
        raise
    except OSError as exc:
        raise BoundedEffectComparisonError(
            "output.write", "/output", f"cannot write {selected}: {exc}"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None and parent >= 0:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            except OSError:
                pass
        if parent >= 0:
            os.close(parent)
        if owned_custody is not None:
            owned_custody.close()


def _fsync_directory_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise


def build_parser(*, prog: str = "workbench process effects compare") -> argparse.ArgumentParser:
    parser = _Parser(
        prog=prog,
        description=(
            "Compare two aligned Crucible stage snapshots through exact family-owned "
            "correspondence and fingerprint policies. All file paths must stay beneath "
            "the pinned CLI root."
        ),
    )
    parser.add_argument(
        "--baseline", required=True, type=Path, help="snapshot beneath the CLI root"
    )
    parser.add_argument(
        "--candidate", required=True, type=Path, help="snapshot beneath the CLI root"
    )
    parser.add_argument(
        "--envelope",
        type=Path,
        help="closed declared expected-effect envelope beneath the CLI root",
    )
    parser.add_argument(
        "--fixture-adapters",
        action="store_true",
        help=(
            "explicitly opt into synthetic GT/Forge-shaped test adapters; "
            "they are not production semantic authority"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional fresh retained JSON path beneath the CLI root",
    )
    parser.add_argument("--json", action="store_true", help="emit canonical comparison JSON")
    return parser


def _render(result: Mapping[str, Any]) -> str:
    summary = result["summary"]
    counts = summary["by_classification"]
    assessment = result["envelope_assessment"]
    lines = [
        "BOUNDED OBSERVED-EFFECT COMPARISON",
        f"state={summary['state']}  comparisons={summary['comparisons']}",
        "  " + "  ".join(f"{name}={counts[name]}" for name in counts),
        f"  structural_changes={summary['structural_changes']}  uncertain={summary['uncertain']}",
        "",
        "Envelope assessment",
        f"  expected-and-observed={len(assessment['expected_and_observed_comparison_ids'])}",
        f"  unexpected-observed={len(assessment['unexpected_observed_comparison_ids'])}",
        f"  expected-not-observed={len(assessment['expected_not_observed'])}",
        f"  unsupported-or-unobservable={len(assessment['unsupported_or_unobservable'])}",
        f"  expectation-mismatches={len(assessment['expectation_mismatches'])}",
        "",
        f"comparison={result['comparison_id']}",
    ]
    if summary["state"] == "comparison-only":
        lines.insert(
            2,
            "  no change envelope was supplied; this output carries no envelope-match claim",
        )
    return "\n".join(lines) + "\n"


def _error_payload(exc: BoundedEffectComparisonError) -> dict[str, Any]:
    return {
        "format": "workbench-process-studio-effect-compare-error-v2",
        "state": "incomparable",
        "error": {
            "code": exc.code,
            "path": exc.path,
            "message": exc.message,
        },
    }


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    """Run comparison; ``root`` supports the Workbench Shell router."""

    selected_root = Path(os.path.abspath(os.fspath(root or Path.cwd())))
    args = build_parser().parse_args(argv)
    custody: _RootCustody | None = None
    try:
        if not args.fixture_adapters:
            raise BoundedEffectComparisonError(
                "adapter.unavailable",
                "/adapters",
                "this incomplete slice has no production adapters; use --fixture-adapters only for synthetic fixtures",
            )
        custody = _pin_root(selected_root, output=False, label="baseline")
        baseline = _read_json(
            args.baseline,
            root=selected_root,
            label="baseline",
            custody=custody,
        )
        candidate = _read_json(
            args.candidate,
            root=selected_root,
            label="candidate",
            custody=custody,
        )
        envelope = (
            None
            if args.envelope is None
            else _read_json(
                args.envelope,
                root=selected_root,
                label="change_envelope",
                custody=custody,
            )
        )
        result = compare_bounded_effects(
            baseline,
            candidate,
            adapters=SYNTHETIC_FIXTURE_ADAPTERS,
            change_envelope=envelope,
        )
        if args.output is not None:
            _write_json(
                args.output,
                result,
                root=selected_root,
                custody=custody,
            )
    except BoundedEffectComparisonError as exc:
        error = _error_payload(exc)
        if args.json:
            print(json.dumps(error, ensure_ascii=False, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        else:
            print(
                f"INCOMPARABLE · {_terminal(exc.code)} · {_terminal(exc.path)} · {_terminal(exc.message)}",
                file=sys.stderr,
            )
        return 2
    finally:
        if custody is not None:
            custody.close()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    else:
        print(_render(result), end="")
    return 1 if result["summary"]["state"] in {"mismatch", "unresolved"} else 0


__all__ = ["build_parser", "main"]
