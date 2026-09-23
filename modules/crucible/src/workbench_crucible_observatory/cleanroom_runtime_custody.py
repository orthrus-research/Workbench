"""Custody checks for exact Cleanroom runtime evidence.

Foundation's class dump and the fixture's loaded-mod inventory are independent
runtime authorities.  This module joins those authorities to admitted raw
actor *leads* without treating any raw self-description as proof.  In
particular, a code-source digest is never inverted into a mod identity: several
mods can legitimately share one jar.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .bundle import CaptureValidationError, canonical_json_bytes
from .cleanroom_raw import RawAdmission


FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT = (
    "workbench-foundation-class-dump-manifest-v1"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_NUMBERED_DIRECTORY_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_MAX_CLASS_FILE_BYTES = 64 * 1024 * 1024
_MAX_DUMP_CLASS_COUNT = 1_000_000
_MAX_DUMP_DIRECTORY_COUNT = 1_000_000
_MAX_DUMP_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
_HASH_CHUNK_BYTES = 1024 * 1024
_METHOD_IDENTITY_FIELDS = (
    "class_name",
    "method_name",
    "method_descriptor",
    "mapping_namespace",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureValidationError(message)


def _nonempty(value: Any, context: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{context} must be nonempty")
    return value


def _sha256(value: Any, context: str) -> str:
    _require(
        isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None,
        f"{context} must be a lowercase SHA-256",
    )
    return value


def _plain_directory(path: Path, context: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"{context} must not be a symlink: {path}")
    _require(stat.S_ISDIR(metadata.st_mode), f"{context} is not a directory: {path}")


def locate_foundation_class_dump(
    case_directory: str | os.PathLike[str],
) -> Path:
    """Return the sole numbered ``CLASS_DUMP`` generation for one case.

    A caller may supply either the case directory (which contains ``server``)
    or the server run directory itself.  No recursive search is performed.
    Multiple dump roots or generations are an admission failure.
    """

    case = Path(case_directory)
    _plain_directory(case, "runtime case directory")

    roots: list[Path] = []
    direct = case / "CLASS_DUMP"
    nested_parent = case / "server"
    nested = nested_parent / "CLASS_DUMP"
    for candidate in (direct, nested):
        if os.path.lexists(candidate):
            if candidate == nested:
                _plain_directory(nested_parent, "runtime server directory")
            _plain_directory(candidate, "Foundation CLASS_DUMP root")
            roots.append(candidate)
    _require(
        len(roots) == 1,
        "runtime case must contain exactly one Foundation CLASS_DUMP root",
    )

    numbered: list[Path] = []
    try:
        children = tuple(roots[0].iterdir())
    except OSError as exc:
        raise CaptureValidationError(
            f"cannot enumerate Foundation CLASS_DUMP root {roots[0]}: {exc}"
        ) from exc
    for child in children:
        if _NUMBERED_DIRECTORY_RE.fullmatch(child.name) is None:
            continue
        _plain_directory(child, "numbered Foundation class dump")
        numbered.append(child)
    _require(
        len(numbered) == 1,
        "Foundation CLASS_DUMP must contain exactly one numbered directory",
    )
    try:
        return numbered[0].resolve(strict=True)
    except OSError as exc:
        raise CaptureValidationError(
            f"cannot resolve numbered Foundation class dump {numbered[0]}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class FoundationClassDumpEntry:
    """One content-addressed regular class file in a dump generation."""

    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class FoundationClassDumpManifest:
    """Canonical receipt for every class file in one Foundation generation.

    ``class_dump_directory`` is local custody context and is deliberately not
    part of ``manifest_sha256``.  The digest binds the format, derived counts,
    total byte size, and ordered relative-path receipts.
    """

    class_dump_directory: Path
    format: str
    class_count: int
    total_size_bytes: int
    entries: tuple[FoundationClassDumpEntry, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


def _snapshot(metadata: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _path_snapshot(path: Path, context: str) -> _FileSnapshot:
    try:
        return _snapshot(path.lstat())
    except OSError as exc:
        raise CaptureValidationError(f"cannot inspect {context} {path}: {exc}") from exc


def _stable_regular_class_digest(
    source: Path,
    scanned: _FileSnapshot,
) -> tuple[int, str]:
    _require(not stat.S_ISLNK(scanned.mode), f"class dump must not be a symlink: {source}")
    _require(stat.S_ISREG(scanned.mode), f"class dump is not a regular file: {source}")
    _require(
        0 < scanned.size <= _MAX_CLASS_FILE_BYTES,
        f"class dump size is outside custody bounds: {source}",
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise CaptureValidationError(f"cannot open class dump {source}: {exc}") from exc
    try:
        before = _snapshot(os.fstat(descriptor))
        _require(before == scanned, f"class dump mutated before reading: {source}")
        hasher = hashlib.sha256()
        bytes_read = 0
        while True:
            block = os.read(descriptor, _HASH_CHUNK_BYTES)
            if not block:
                break
            bytes_read += len(block)
            _require(
                bytes_read <= _MAX_CLASS_FILE_BYTES,
                f"class dump grew outside custody bounds: {source}",
            )
            hasher.update(block)
        after = _snapshot(os.fstat(descriptor))
    except OSError as exc:
        raise CaptureValidationError(f"cannot hash class dump {source}: {exc}") from exc
    finally:
        os.close(descriptor)

    _require(before == after, f"class dump mutated while reading: {source}")
    _require(bytes_read == before.size, f"class dump size changed while reading: {source}")
    path_after = _path_snapshot(source, "class dump after hashing")
    _require(
        path_after == after,
        f"class dump path mutated while reading: {source}",
    )
    return bytes_read, hasher.hexdigest()


def _dump_component(name: str, context: str) -> str:
    _require(
        bool(name)
        and name not in {".", ".."}
        and "/" not in name
        and "\\" not in name
        and "\x00" not in name,
        f"invalid {context}",
    )
    return name


def build_foundation_class_dump_manifest(
    class_dump_directory: str | os.PathLike[str],
) -> FoundationClassDumpManifest:
    """Content-address an entire numbered Foundation dump generation.

    The walk does not follow links.  Every non-directory entry must be a
    nonempty regular ``.class`` file.  Directory and file snapshots are
    rechecked after hashing so an unstable generation cannot receive a receipt.
    """

    dump = Path(class_dump_directory)
    _plain_directory(dump, "numbered Foundation class dump")
    root_snapshot = _path_snapshot(dump, "numbered Foundation class dump")
    directories: dict[str, tuple[Path, _FileSnapshot]] = {
        "": (dump, root_snapshot)
    }
    pending: list[tuple[Path, tuple[str, ...], _FileSnapshot]] = [
        (dump, (), root_snapshot)
    ]
    receipts: list[FoundationClassDumpEntry] = []
    total_size = 0

    while pending:
        directory, relative_parts, expected_directory = pending.pop()
        _require(
            _path_snapshot(directory, "Foundation dump directory")
            == expected_directory,
            f"Foundation dump directory mutated while walking: {directory}",
        )
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise CaptureValidationError(
                f"cannot enumerate Foundation dump directory {directory}: {exc}"
            ) from exc
        for child in children:
            name = _dump_component(child.name, "Foundation dump path component")
            child_path = directory / name
            relative = relative_parts + (name,)
            relative_path = "/".join(relative)
            try:
                metadata = _snapshot(child.stat(follow_symlinks=False))
            except OSError as exc:
                raise CaptureValidationError(
                    f"cannot inspect Foundation dump entry {child_path}: {exc}"
                ) from exc
            if stat.S_ISLNK(metadata.mode):
                raise CaptureValidationError(
                    f"Foundation dump entry must not be a symlink: {child_path}"
                )
            if stat.S_ISDIR(metadata.mode):
                _require(
                    relative_path not in directories,
                    f"duplicate Foundation dump path {relative_path}",
                )
                directories[relative_path] = (child_path, metadata)
                _require(
                    len(directories) <= _MAX_DUMP_DIRECTORY_COUNT,
                    "Foundation dump directory count exceeds custody bounds",
                )
                pending.append((child_path, relative, metadata))
                continue
            _require(
                stat.S_ISREG(metadata.mode),
                f"Foundation dump entry is not a regular file: {child_path}",
            )
            _require(
                name.endswith(".class") and name != ".class",
                f"Foundation dump contains a non-class file: {relative_path}",
            )
            _require(
                len(receipts) < _MAX_DUMP_CLASS_COUNT,
                "Foundation dump class count exceeds custody bounds",
            )
            size, file_digest = _stable_regular_class_digest(child_path, metadata)
            total_size += size
            _require(
                total_size <= _MAX_DUMP_TOTAL_BYTES,
                "Foundation dump total size exceeds custody bounds",
            )
            receipts.append(
                FoundationClassDumpEntry(
                    relative_path=relative_path,
                    size=size,
                    sha256=file_digest,
                )
            )

    for relative_path, (directory, expected) in directories.items():
        _require(
            _path_snapshot(directory, "Foundation dump directory after hashing")
            == expected,
            "Foundation dump directory mutated while hashing: "
            + (relative_path or "."),
        )

    receipts.sort(key=lambda entry: entry.relative_path)
    _require(bool(receipts), "Foundation CLASS_DUMP generation is empty")
    material = {
        "format": FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT,
        "class_count": len(receipts),
        "total_size_bytes": total_size,
        "classes": [
            {
                "relative_path": entry.relative_path,
                "size": entry.size,
                "sha256": entry.sha256,
            }
            for entry in receipts
        ],
    }
    manifest_digest = hashlib.sha256(canonical_json_bytes(material)).hexdigest()
    return FoundationClassDumpManifest(
        class_dump_directory=dump.resolve(strict=True),
        format=FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT,
        class_count=len(receipts),
        total_size_bytes=total_size,
        entries=tuple(receipts),
        manifest_sha256=manifest_digest,
    )


def _class_name_parts(class_name: Any, context: str) -> tuple[str, ...]:
    name = _nonempty(class_name, context)
    _require("/" not in name and "\\" not in name and "\x00" not in name, f"invalid {context}")
    parts = tuple(name.split("."))
    _require(
        all(part not in {"", ".", ".."} for part in parts),
        f"invalid {context}",
    )
    return parts


def _modified_utf8(value: str) -> bytes:
    """Encode the JVM's modified UTF-8 representation of a class name."""

    units = value.encode("utf-16-be", errors="surrogatepass")
    result = bytearray()
    for offset in range(0, len(units), 2):
        unit = (units[offset] << 8) | units[offset + 1]
        if 0x0001 <= unit <= 0x007F:
            result.append(unit)
        elif unit <= 0x07FF:
            result.extend((0xC0 | (unit >> 6), 0x80 | (unit & 0x3F)))
        else:
            result.extend(
                (
                    0xE0 | (unit >> 12),
                    0x80 | ((unit >> 6) & 0x3F),
                    0x80 | (unit & 0x3F),
                )
            )
    return bytes(result)


class _ClassReader:
    def __init__(self, data: bytes, source: Path) -> None:
        self.data = data
        self.source = source
        self.offset = 0

    def take(self, size: int) -> bytes:
        end = self.offset + size
        _require(end <= len(self.data), f"truncated class file: {self.source}")
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def u1(self) -> int:
        return self.take(1)[0]

    def u2(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u4(self) -> int:
        return struct.unpack(">I", self.take(4))[0]


def _constant_utf8(
    pool: list[tuple[int, Any] | None],
    index: int,
    source: Path,
    context: str,
) -> bytes:
    _require(0 < index < len(pool), f"invalid {context} index: {source}")
    entry = pool[index]
    _require(
        entry is not None and entry[0] == 1 and isinstance(entry[1], bytes),
        f"{context} does not reference CONSTANT_Utf8: {source}",
    )
    return entry[1]


def _skip_attributes(reader: _ClassReader, count: int) -> None:
    for _ in range(count):
        reader.u2()  # attribute_name_index
        reader.take(reader.u4())


def _class_identity_and_methods(
    data: bytes,
    source: Path,
) -> tuple[bytes, frozenset[tuple[bytes, bytes]]]:
    reader = _ClassReader(data, source)
    _require(reader.u4() == 0xCAFEBABE, f"invalid class magic: {source}")
    reader.u2()  # minor_version
    reader.u2()  # major_version
    count = reader.u2()
    _require(count > 1, f"invalid constant pool count: {source}")
    pool: list[tuple[int, Any] | None] = [None] * count
    index = 1
    while index < count:
        tag = reader.u1()
        if tag == 1:  # CONSTANT_Utf8
            pool[index] = (tag, reader.take(reader.u2()))
        elif tag in {3, 4}:  # Integer, Float
            reader.take(4)
        elif tag in {5, 6}:  # Long, Double (two constant-pool slots)
            reader.take(8)
            _require(index + 1 < count, f"invalid wide constant-pool entry: {source}")
            index += 1
        elif tag == 7:  # CONSTANT_Class
            pool[index] = (tag, reader.u2())
        elif tag in {8, 16, 19, 20}:  # String, MethodType, Module, Package
            reader.take(2)
        elif tag in {9, 10, 11, 12, 17, 18}:  # two-u2 entries
            reader.take(4)
        elif tag == 15:  # MethodHandle
            reader.take(3)
        else:
            raise CaptureValidationError(
                f"unsupported constant-pool tag {tag} in {source}"
            )
        index += 1

    reader.u2()  # access_flags
    this_index = reader.u2()
    reader.u2()  # super_class
    _require(0 < this_index < count, f"invalid this_class index: {source}")
    class_entry = pool[this_index]
    _require(
        class_entry is not None and class_entry[0] == 7,
        f"this_class does not reference CONSTANT_Class: {source}",
    )
    name_index = class_entry[1]
    _require(
        isinstance(name_index, int) and 0 < name_index < count,
        f"invalid this_class name index: {source}",
    )
    this_name = _constant_utf8(pool, name_index, source, "this_class name")

    reader.take(2 * reader.u2())  # interfaces
    for _ in range(reader.u2()):  # fields
        reader.u2()  # access_flags
        reader.u2()  # name_index
        reader.u2()  # descriptor_index
        _skip_attributes(reader, reader.u2())

    methods: set[tuple[bytes, bytes]] = set()
    for _ in range(reader.u2()):
        reader.u2()  # access_flags
        method_name = _constant_utf8(pool, reader.u2(), source, "method name")
        descriptor = _constant_utf8(pool, reader.u2(), source, "method descriptor")
        methods.add((method_name, descriptor))
        _skip_attributes(reader, reader.u2())

    _skip_attributes(reader, reader.u2())  # class attributes
    _require(reader.offset == len(data), f"trailing bytes in class dump: {source}")
    return this_name, frozenset(methods)


def _expected_constant_utf8(value: str, context: str) -> bytes:
    encoded = _modified_utf8(value)
    _require(len(encoded) <= 65535, f"{context} exceeds a JVM UTF-8 constant")
    return encoded


@dataclass(frozen=True, slots=True)
class _VerifiedClass:
    digest: str
    methods: frozenset[tuple[bytes, bytes]]


def _verified_class(dump_directory: Path, class_name: str) -> _VerifiedClass:
    parts = _class_name_parts(class_name, "class name")
    cursor = dump_directory
    _plain_directory(cursor, "numbered Foundation class dump")
    for part in parts[:-1]:
        cursor /= part
        _require(
            os.path.lexists(cursor),
            f"final transformed class dump is missing for {class_name}",
        )
        _plain_directory(cursor, "Foundation class package directory")
    source = cursor / (parts[-1] + ".class")
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise CaptureValidationError(
            f"final transformed class dump is missing for {class_name}: {exc}"
        ) from exc
    _require(not stat.S_ISLNK(metadata.st_mode), f"class dump must not be a symlink: {source}")
    _require(stat.S_ISREG(metadata.st_mode), f"class dump is not a regular file: {source}")
    _require(
        0 < metadata.st_size <= _MAX_CLASS_FILE_BYTES,
        f"class dump size is outside custody bounds: {source}",
    )
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise CaptureValidationError(f"cannot read class dump {source}: {exc}") from exc
    _require(
        len(data) == metadata.st_size,
        f"class dump changed while it was read: {source}",
    )

    expected_internal_name = "/".join(parts)
    observed_internal_name, methods = _class_identity_and_methods(data, source)
    _require(
        observed_internal_name
        == _expected_constant_utf8(expected_internal_name, "class internal name"),
        f"class dump this_class does not match {class_name}: {source}",
    )
    # Hash only after the class-file identity and bounded structure pass validation.
    return _VerifiedClass(
        digest=hashlib.sha256(data).hexdigest(),
        methods=methods,
    )


def verify_foundation_class_dump_entry(
    class_dump_directory: str | os.PathLike[str],
    class_name: str,
) -> FoundationClassDumpEntry:
    """Verify one dump path, JVM ``this_class`` identity, size, and digest."""

    dump = Path(class_dump_directory)
    verified = _verified_class(dump, class_name)
    relative_path = class_name.replace(".", "/") + ".class"
    source = dump.joinpath(*relative_path.split("/"))
    snapshot = _path_snapshot(source, "verified Foundation class dump entry")
    _require(
        stat.S_ISREG(snapshot.mode) and not stat.S_ISLNK(snapshot.mode),
        f"verified Foundation dump entry is not a regular file: {source}",
    )
    return FoundationClassDumpEntry(
        relative_path=relative_path,
        size=snapshot.size,
        sha256=verified.digest,
    )


def _needed_class_names(
    admission: RawAdmission,
    probe_plan: Mapping[str, Any],
) -> tuple[str, ...]:
    _require(isinstance(admission, RawAdmission), "raw admission is required")
    _require(isinstance(probe_plan, Mapping), "probe plan must be an object")
    classes: set[str] = set()
    for index, row in enumerate(admission.rows):
        actor = row.get("actor")
        _require(isinstance(actor, Mapping), f"raw row {index} actor must be an object")
        class_name = actor.get("class_name")
        if class_name is not None:
            classes.add(".".join(_class_name_parts(class_name, f"raw row {index} actor class")))

    hooks = probe_plan.get("hooks")
    _require(isinstance(hooks, list) and bool(hooks), "probe plan must contain hooks")
    for index, hook in enumerate(hooks):
        _require(isinstance(hook, Mapping), f"probe plan hook {index} must be an object")
        target = ".".join(
            _class_name_parts(hook.get("target_class"), f"probe plan hook {index} target class")
        )
        classes.add(target)
    return tuple(sorted(classes))


def build_verified_class_dump_sha256(
    admission: RawAdmission,
    probe_plan: Mapping[str, Any],
    class_dump_directory: str | os.PathLike[str],
) -> Mapping[str, str]:
    """Hash every raw-actor and probe-target class after JVM-name validation."""

    dump = Path(class_dump_directory)
    _plain_directory(dump, "numbered Foundation class dump")
    result = {
        class_name: verify_foundation_class_dump_entry(dump, class_name).sha256
        for class_name in _needed_class_names(admission, probe_plan)
    }
    return MappingProxyType(result)


def _trusted_sources(
    loaded_mod_source_sha256: Mapping[str, str],
) -> dict[str, str]:
    _require(
        isinstance(loaded_mod_source_sha256, Mapping),
        "loaded mod source digests must be an object",
    )
    sources: dict[str, str] = {}
    for mod_id, digest in loaded_mod_source_sha256.items():
        identifier = _nonempty(mod_id, "loaded mod ID")
        _require(identifier not in sources, f"duplicate loaded mod ID {identifier}")
        sources[identifier] = _sha256(digest, f"loaded mod {identifier} source")
    return sources


def _trusted_prefixes(
    trusted_class_prefix_owners: Mapping[str, str],
    sources: Mapping[str, str],
) -> tuple[tuple[str, str], ...]:
    _require(
        isinstance(trusted_class_prefix_owners, Mapping),
        "trusted class-prefix owners must be an object",
    )
    prefixes: dict[str, str] = {}
    for raw_prefix, raw_owner in trusted_class_prefix_owners.items():
        prefix = _nonempty(raw_prefix, "trusted class prefix").rstrip(".")
        _class_name_parts(prefix, "trusted class prefix")
        owner = _nonempty(raw_owner, f"owner for class prefix {prefix}")
        _require(prefix not in prefixes, f"duplicate trusted class prefix {prefix}")
        _require(owner in sources, f"trusted owner {owner} has no exact loaded source digest")
        prefixes[prefix] = owner
    _require(bool(prefixes), "trusted class-prefix owners must not be empty")
    return tuple(sorted(prefixes.items()))


def _owner_for(class_name: str, prefixes: Iterable[tuple[str, str]]) -> str:
    matches = {
        owner
        for prefix, owner in prefixes
        if class_name == prefix
        or class_name.startswith(prefix + ".")
        or class_name.startswith(prefix + "$")
    }
    _require(bool(matches), f"no trusted class-prefix owner for {class_name}")
    _require(len(matches) == 1, f"ambiguous trusted class-prefix ownership for {class_name}")
    return next(iter(matches))


def build_exact_actor_inventory(
    admission: RawAdmission,
    class_dump_directory: str | os.PathLike[str],
    *,
    trusted_class_prefix_owners: Mapping[str, str],
    loaded_mod_source_sha256: Mapping[str, str],
) -> tuple[Mapping[str, str], ...]:
    """Build corroborated method receipts from admitted raw actor leads.

    Ownership always comes from a matching trusted class prefix.  The selected
    owner's loaded source digest must then match the raw lead.  Shared digests
    are allowed because they are never used to select an owner.

    Every admitted method descriptor is treated as an already-encoded JVM
    descriptor and must literally occur with the named method in the dumped
    class.  This rule also applies to ``java_source``: that label can describe
    the spelling of the method name, but custody neither translates Java-like
    signatures nor claims source provenance.  Any other namespace receives the
    same literal binary check; no mapping is inferred from its label.
    """

    _require(isinstance(admission, RawAdmission), "raw admission is required")
    sources = _trusted_sources(loaded_mod_source_sha256)
    prefixes = _trusted_prefixes(trusted_class_prefix_owners, sources)
    dump = Path(class_dump_directory)
    _plain_directory(dump, "numbered Foundation class dump")
    verified_classes: dict[str, _VerifiedClass] = {}
    receipts: dict[tuple[str, ...], Mapping[str, str]] = {}

    for index, row in enumerate(admission.rows):
        actor = row.get("actor")
        _require(isinstance(actor, Mapping), f"raw row {index} actor must be an object")
        identity = tuple(actor.get(field) for field in _METHOD_IDENTITY_FIELDS)
        present = tuple(value is not None for value in identity)
        if not any(present):
            continue
        _require(all(present), f"raw row {index} has a partial method-level actor lead")
        class_name, method_name, descriptor, namespace = (
            _nonempty(value, f"raw row {index} actor {field}")
            for field, value in zip(_METHOD_IDENTITY_FIELDS, identity)
        )
        class_name = ".".join(_class_name_parts(class_name, f"raw row {index} actor class"))
        owner = _owner_for(class_name, prefixes)

        lead_mod_id = actor.get("mod_id")
        _require(
            lead_mod_id is None or isinstance(lead_mod_id, str),
            f"raw row {index} actor mod ID must be a string or null",
        )
        if lead_mod_id is not None:
            _require(bool(lead_mod_id), f"raw row {index} actor mod ID must be nonempty")
            _require(
                lead_mod_id == owner,
                f"raw row {index} actor mod ID contradicts trusted owner for {class_name}",
            )

        lead_source = _sha256(
            actor.get("code_source_sha256"),
            f"raw row {index} actor code source",
        )
        _require(
            lead_source == sources[owner],
            f"raw row {index} actor code source contradicts loaded source for {owner}",
        )
        class_evidence = verified_classes.get(class_name)
        if class_evidence is None:
            class_evidence = _verified_class(dump, class_name)
            verified_classes[class_name] = class_evidence
        claimed_method = (
            _expected_constant_utf8(method_name, "actor method name"),
            _expected_constant_utf8(descriptor, "actor method descriptor"),
        )
        _require(
            claimed_method in class_evidence.methods,
            "raw row "
            f"{index} actor method is absent from final transformed class dump: "
            f"{class_name}#{method_name}{descriptor} "
            f"(mapping namespace {namespace}; no translation attempted)",
        )
        receipt = {
            "mod_id": owner,
            "code_source_sha256": sources[owner],
            "class_name": class_name,
            "method_name": method_name,
            "method_descriptor": descriptor,
            "mapping_namespace": namespace,
        }
        key = tuple(receipt[field] for field in (
            "mod_id",
            "code_source_sha256",
            "class_name",
            "method_name",
            "method_descriptor",
            "mapping_namespace",
        ))
        receipts[key] = MappingProxyType(receipt)

    _require(bool(receipts), "raw admission contains no exact method-level actor leads")
    return tuple(receipts[key] for key in sorted(receipts))


@dataclass(frozen=True, slots=True)
class CleanroomRuntimeCustody:
    """Inputs suitable for ``normalize_cleanroom_raw`` after custody checks."""

    class_dump_directory: Path
    class_dump_manifest: FoundationClassDumpManifest
    class_dump_sha256: Mapping[str, str]
    actor_inventory: tuple[Mapping[str, str], ...]


def collect_cleanroom_runtime_custody(
    case_directory: str | os.PathLike[str],
    admission: RawAdmission,
    probe_plan: Mapping[str, Any],
    *,
    trusted_class_prefix_owners: Mapping[str, str],
    loaded_mod_source_sha256: Mapping[str, str],
) -> CleanroomRuntimeCustody:
    """Collect exact normalizer inputs from one bounded runtime case."""

    dump = locate_foundation_class_dump(case_directory)
    dump_manifest = build_foundation_class_dump_manifest(dump)
    class_digests = build_verified_class_dump_sha256(admission, probe_plan, dump)
    inventory = build_exact_actor_inventory(
        admission,
        dump,
        trusted_class_prefix_owners=trusted_class_prefix_owners,
        loaded_mod_source_sha256=loaded_mod_source_sha256,
    )
    return CleanroomRuntimeCustody(
        class_dump_directory=dump,
        class_dump_manifest=dump_manifest,
        class_dump_sha256=class_digests,
        actor_inventory=inventory,
    )


__all__ = [
    "CleanroomRuntimeCustody",
    "FOUNDATION_CLASS_DUMP_MANIFEST_FORMAT",
    "FoundationClassDumpEntry",
    "FoundationClassDumpManifest",
    "build_exact_actor_inventory",
    "build_foundation_class_dump_manifest",
    "build_verified_class_dump_sha256",
    "collect_cleanroom_runtime_custody",
    "locate_foundation_class_dump",
]
