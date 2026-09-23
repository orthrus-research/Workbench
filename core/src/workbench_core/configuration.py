"""Load the one current Workbench configuration manifest.

The manifest selects profile authority and declares a deliberately small set
of physical path bindings.  It does not duplicate profile-owned versions,
artifact locks, recognition rules, or pack policy.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tomllib
from types import MappingProxyType
from typing import Any

import yaml


SCHEMA = "workbench/config/v1"
CONFIGURATION_PATH = Path("workbench.toml")
PROFILE_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_PROFILE_BYTES = 2 * 1024 * 1024
SUPPORTED_BINDINGS = ("java_candidate_home",)

_ROOT_FIELDS = {"bindings", "schema", "selection"}
_SELECTION_FIELDS = {"pack_document", "pack_variant", "platform_document"}
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROFILE_ID_PART = r"[a-z0-9][a-z0-9._-]*"
_PACK_PROFILE_ID = re.compile(
    rf"^workbench-pack:{_PROFILE_ID_PART}(?::{_PROFILE_ID_PART})*$"
)
_PLATFORM_PROFILE_ID = re.compile(
    rf"^workbench-platform:{_PROFILE_ID_PART}(?::{_PROFILE_ID_PART})*$"
)
_PACK_VARIANT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_COMMAND_ID = re.compile(r"^[a-z][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*$")


class WorkbenchConfigurationError(ValueError):
    """The active Workbench configuration cannot be resolved safely."""


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    """Immutable bytes and identity for one safely read source document."""

    path: Path
    relative_path: str | None
    source_bytes: bytes
    sha256: str


@dataclass(frozen=True, slots=True)
class ProfileSnapshot:
    """An immutable profile document plus its embedded identity."""

    source: SourceSnapshot
    profile_id: str
    schema_version: int
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BindingDeclaration:
    """One literal path or one explicitly named environment import."""

    name: str
    kind: str
    value: str

    @property
    def environment_variable(self) -> str | None:
        return self.value if self.kind == "environment" else None

    @property
    def literal_path(self) -> str | None:
        return self.value if self.kind == "literal" else None


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    """A binding value snapshotted for one operation."""

    name: str
    source: str
    value: str | None
    environment_variable: str | None = None

    @property
    def is_bound(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class ResolvedBindings:
    """Bindings resolved from one exact configuration/environment snapshot."""

    configuration_manifest_sha256: str
    selection_digest: str
    entries: tuple[ResolvedBinding, ...]

    def binding(self, name: str) -> ResolvedBinding:
        for entry in self.entries:
            if entry.name == name:
                return entry
        raise KeyError(name)

    def get(self, name: str) -> str | None:
        return self.binding(name).value

    def as_dict(self) -> dict[str, str | None]:
        return {entry.name: entry.value for entry in self.entries}

    def operation_digest(self, command_id: str, names: Iterable[str]) -> str:
        """Bind one command to exactly the named resolved path inputs."""

        if type(command_id) is not str or _COMMAND_ID.fullmatch(command_id) is None:
            _fail("/command_id", "must be a canonical command ID")
        selected: set[str] = set()
        for name in names:
            if type(name) is not str or name not in SUPPORTED_BINDINGS:
                _fail("/binding_names", f"contains unsupported binding {name!r}")
            if name in selected:
                _fail("/binding_names", f"contains duplicate binding {name!r}")
            selected.add(name)

        rows: list[dict[str, Any]] = []
        for name in sorted(selected):
            entry = self.binding(name)
            if entry.source == "environment":
                source: dict[str, str] = {
                    "kind": "environment",
                    "variable": entry.environment_variable or "",
                }
            else:
                source = {"kind": entry.source}
            rows.append(
                {
                    "name": entry.name,
                    "source": source,
                    "value": entry.value,
                }
            )
        payload = {
            "bindings": rows,
            "command_id": command_id,
            "schema": "workbench/operation-bindings/v1",
        }
        return "sha256:" + sha256(_canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkbenchConfiguration:
    """Immutable V1 manifest, selected authority, and binding declarations."""

    schema: str
    manifest: SourceSnapshot
    pack_document: ProfileSnapshot
    pack_variant: str
    platform_document: ProfileSnapshot
    selection_digest: str
    binding_declarations: tuple[BindingDeclaration, ...]

    @property
    def pack_profile_id(self) -> str:
        return self.pack_document.profile_id

    @property
    def platform_profile_id(self) -> str:
        return self.platform_document.profile_id

    def binding_declaration(self, name: str) -> BindingDeclaration | None:
        if name not in SUPPORTED_BINDINGS:
            raise KeyError(name)
        for declaration in self.binding_declarations:
            if declaration.name == name:
                return declaration
        return None

    def resolve_bindings(
        self,
        environment: Mapping[str, str] | None = None,
        *,
        names: Iterable[str] | None = None,
    ) -> ResolvedBindings:
        return resolve_bindings(
            self,
            environment=environment,
            names=names,
        )

    def require_binding_snapshot(
        self,
        resolved: ResolvedBindings,
        *,
        names: Iterable[str],
    ) -> None:
        """Reject bindings resolved for another configuration or name set."""

        expected_names = set(names)
        if any(
            type(name) is not str or name not in SUPPORTED_BINDINGS
            for name in expected_names
        ):
            _fail("/binding_names", "contains an unsupported binding")
        actual_names = tuple(entry.name for entry in resolved.entries)
        expected_order = tuple(
            name for name in SUPPORTED_BINDINGS if name in expected_names
        )
        if (
            resolved.configuration_manifest_sha256 != self.manifest.sha256
            or resolved.selection_digest != self.selection_digest
            or actual_names != expected_order
        ):
            _fail(
                "/resolved_bindings",
                "does not belong to this configuration and consumed binding set",
            )


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects keys hidden by later duplicates."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            )
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _fail(path: str, detail: str) -> None:
    raise WorkbenchConfigurationError(f"{path}: {detail}")


def _exact_table(value: object, fields: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "must be a table")
    assert isinstance(value, dict)
    if set(value) != fields:
        missing = sorted(fields - set(value))
        extra = sorted(set(value) - fields)
        _fail(path, f"fields differ (missing={missing}, extra={extra})")
    return value


def _read_regular_file(path: Path, *, maximum: int, label: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail(str(path), f"{label} must be a regular non-symlink file")
        if not 1 <= before.st_size <= maximum:
            _fail(str(path), f"{label} exceeds its permitted size")

        raw = bytearray()
        while len(raw) <= maximum:
            block = os.read(
                descriptor,
                min(64 * 1024, maximum - len(raw) + 1),
            )
            if not block:
                break
            raw.extend(block)

        after = os.fstat(descriptor)
        custody = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if (
            len(raw) != before.st_size
            or len(raw) > maximum
            or any(getattr(before, key) != getattr(after, key) for key in custody)
        ):
            _fail(str(path), f"{label} changed while being read")
        current = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(current.st_mode) or any(
            getattr(after, key) != getattr(current, key) for key in custody
        ):
            _fail(str(path), f"{label} pathname identity changed while being read")
        return bytes(raw)
    except WorkbenchConfigurationError:
        raise
    except OSError as exc:
        _fail(str(path), f"{label} cannot be opened safely ({type(exc).__name__})")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    raise AssertionError("unreachable")


def _source_snapshot(
    path: Path,
    *,
    maximum: int,
    label: str,
    relative_path: str | None,
) -> SourceSnapshot:
    raw = _read_regular_file(path, maximum=maximum, label=label)
    return SourceSnapshot(
        path=path,
        relative_path=relative_path,
        source_bytes=raw,
        sha256=sha256(raw).hexdigest(),
    )


def _manifest_path(suite: Path, config_path: Path | str) -> Path:
    configured = Path(config_path).expanduser()
    if not configured.is_absolute():
        configured = suite / configured
    return configured.absolute()


def _profile_relative_path(value: object, path: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        _fail(path, "must be a suite-relative POSIX path under profiles/")
    assert isinstance(value, str)
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or len(relative.parts) < 2
        or relative.parts[0] != "profiles"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        _fail(path, "must be a suite-relative POSIX path under profiles/")
    if relative.suffix not in {".yaml", ".yml"}:
        _fail(path, "must select a YAML profile document")
    return value


def _suite_profile_path(suite: Path, relative: str, path: str) -> Path:
    candidate = suite.joinpath(*PurePosixPath(relative).parts)
    current = suite
    try:
        for part in PurePosixPath(relative).parts[:-1]:
            current = current / part
            metadata = os.lstat(current)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                _fail(path, "profile path must traverse only real directories")
    except WorkbenchConfigurationError:
        raise
    except OSError as exc:
        _fail(path, f"profile path cannot be traversed safely ({type(exc).__name__})")
    return candidate


def _yaml_object(source: SourceSnapshot, label: str) -> dict[str, Any]:
    try:
        text = source.source_bytes.decode("utf-8", errors="strict")
        value = yaml.load(text, Loader=_UniqueKeyLoader)
    except (UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise WorkbenchConfigurationError(
            f"{source.path}: {label} must be strict UTF-8 YAML"
        ) from exc
    if type(value) is not dict:
        _fail(str(source.path), f"{label} root must be an object")
    return value


def _freeze(value: Any, active: set[int] | None = None) -> Any:
    active = set() if active is None else active
    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            _fail("/profiles", "recursive YAML values are not permitted")
        active.add(marker)
        try:
            return MappingProxyType(
                {key: _freeze(item, active) for key, item in value.items()}
            )
        finally:
            active.remove(marker)
    if isinstance(value, list):
        marker = id(value)
        if marker in active:
            _fail("/profiles", "recursive YAML values are not permitted")
        active.add(marker)
        try:
            return tuple(_freeze(item, active) for item in value)
        finally:
            active.remove(marker)
    if isinstance(value, set):
        return frozenset(_freeze(item, active) for item in value)
    return value


def _profile_schema(record: Mapping[str, Any], path: str) -> int:
    version = record.get("schema_version")
    if type(version) is not int or version != PROFILE_SCHEMA_VERSION:
        _fail(path, f"only profile schema_version {PROFILE_SCHEMA_VERSION} is supported")
    return version


def _embedded_id(
    record: Mapping[str, Any],
    field: str,
    pattern: re.Pattern[str],
    path: str,
) -> str:
    value = record.get(field)
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(path, f"must contain a canonical {field}")
    return value


def _binding_declaration(name: str, value: object) -> BindingDeclaration:
    path = f"/bindings/{name}"
    if type(value) is str:
        assert isinstance(value, str)
        _absolute_path(value, path)
        return BindingDeclaration(name=name, kind="literal", value=value)
    table = _exact_table(value, {"env"}, path)
    variable = table["env"]
    if type(variable) is not str or _ENVIRONMENT_NAME.fullmatch(variable) is None:
        _fail(f"{path}/env", "must be an uppercase environment variable name")
    return BindingDeclaration(name=name, kind="environment", value=variable)


def _absolute_path(value: object, path: str) -> str:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        _fail(path, "must be an absolute path")
    assert isinstance(value, str)
    if not (value.startswith("/") or PureWindowsPath(value).is_absolute()):
        _fail(path, "must be an absolute path")
    return value


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _selection_digest(
    *,
    pack: ProfileSnapshot,
    pack_variant: str,
    platform: ProfileSnapshot,
) -> str:
    value = {
        "pack_document": pack.source.relative_path,
        "pack_document_sha256": pack.source.sha256,
        "pack_profile_id": pack.profile_id,
        "pack_variant": pack_variant,
        "platform_document": platform.source.relative_path,
        "platform_document_sha256": platform.source.sha256,
        "platform_profile_id": platform.profile_id,
        "schema": SCHEMA,
    }
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def load_workbench_configuration(
    suite_root: Path | str,
    config_path: Path | str = CONFIGURATION_PATH,
) -> WorkbenchConfiguration:
    """Load and cross-check the exact current Workbench configuration.

    Relative manifest paths are resolved against ``suite_root``.  Selected
    profile document paths are always suite-relative and confined beneath the
    real ``profiles/`` directory tree.
    """

    suite = Path(suite_root).expanduser().resolve()
    if not suite.is_dir():
        _fail(str(suite), "suite root must be a directory")

    manifest_path = _manifest_path(suite, config_path)
    manifest = _source_snapshot(
        manifest_path,
        maximum=MAX_MANIFEST_BYTES,
        label="Workbench configuration manifest",
        relative_path=(
            manifest_path.relative_to(suite).as_posix()
            if manifest_path.is_relative_to(suite)
            else None
        ),
    )
    try:
        text = manifest.source_bytes.decode("utf-8", errors="strict")
        parsed = tomllib.loads(text)
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError) as exc:
        raise WorkbenchConfigurationError(
            f"{manifest.path}: must be strict UTF-8 TOML"
        ) from exc

    root = _exact_table(parsed, _ROOT_FIELDS, "/")
    if root["schema"] != SCHEMA:
        _fail("/schema", f"only {SCHEMA!r} is supported")
    selection = _exact_table(root["selection"], _SELECTION_FIELDS, "/selection")
    pack_relative = _profile_relative_path(
        selection["pack_document"],
        "/selection/pack_document",
    )
    platform_relative = _profile_relative_path(
        selection["platform_document"],
        "/selection/platform_document",
    )
    pack_variant = selection["pack_variant"]
    if type(pack_variant) is not str or _PACK_VARIANT.fullmatch(pack_variant) is None:
        _fail("/selection/pack_variant", "must be a canonical pack variant ID")

    binding_table = root["bindings"]
    if type(binding_table) is not dict:
        _fail("/bindings", "must be a table")
    assert isinstance(binding_table, dict)
    unsupported = sorted(set(binding_table) - set(SUPPORTED_BINDINGS))
    if unsupported:
        _fail("/bindings", f"unsupported fields {unsupported}")
    declarations = tuple(
        _binding_declaration(name, binding_table[name])
        for name in SUPPORTED_BINDINGS
        if name in binding_table
    )

    pack_path = _suite_profile_path(
        suite,
        pack_relative,
        "/selection/pack_document",
    )
    platform_path = _suite_profile_path(
        suite,
        platform_relative,
        "/selection/platform_document",
    )
    pack_source = _source_snapshot(
        pack_path,
        maximum=MAX_PROFILE_BYTES,
        label="pack profile",
        relative_path=pack_relative,
    )
    platform_source = _source_snapshot(
        platform_path,
        maximum=MAX_PROFILE_BYTES,
        label="platform profile",
        relative_path=platform_relative,
    )
    pack_values = _yaml_object(pack_source, "pack profile")
    platform_values = _yaml_object(platform_source, "platform profile")
    pack_schema = _profile_schema(pack_values, "/pack_document/schema_version")
    platform_schema = _profile_schema(
        platform_values,
        "/platform_document/schema_version",
    )
    pack_id = _embedded_id(
        pack_values,
        "profile_family_id",
        _PACK_PROFILE_ID,
        "/pack_document/profile_family_id",
    )
    platform_id = _embedded_id(
        platform_values,
        "profile_id",
        _PLATFORM_PROFILE_ID,
        "/platform_document/profile_id",
    )

    variants = pack_values.get("profiles")
    if type(variants) is not dict:
        _fail("/pack_document/profiles", "must be a table")
    assert isinstance(variants, dict)
    selected = variants.get(pack_variant)
    if type(selected) is not dict:
        _fail(
            "/selection/pack_variant",
            "must name a variant in the selected pack document",
        )
    assert isinstance(selected, dict)
    bound_platform = selected.get("platform_profile_id")
    if type(bound_platform) is not str or not bound_platform:
        _fail(
            f"/pack_document/profiles/{pack_variant}/platform_profile_id",
            "must bind a platform profile ID",
        )
    if bound_platform != platform_id:
        _fail(
            f"/pack_document/profiles/{pack_variant}/platform_profile_id",
            "does not match the selected platform document",
        )

    pack = ProfileSnapshot(
        source=pack_source,
        profile_id=pack_id,
        schema_version=pack_schema,
        values=_freeze(pack_values),
    )
    platform = ProfileSnapshot(
        source=platform_source,
        profile_id=platform_id,
        schema_version=platform_schema,
        values=_freeze(platform_values),
    )
    return WorkbenchConfiguration(
        schema=SCHEMA,
        manifest=manifest,
        pack_document=pack,
        pack_variant=pack_variant,
        platform_document=platform,
        selection_digest=_selection_digest(
            pack=pack,
            pack_variant=pack_variant,
            platform=platform,
        ),
        binding_declarations=declarations,
    )


def resolve_bindings(
    configuration: WorkbenchConfiguration,
    *,
    environment: Mapping[str, str] | None = None,
    names: Iterable[str] | None = None,
) -> ResolvedBindings:
    """Resolve requested imports once without mutating the environment.

    Omitting ``names`` resolves every V1 binding for configuration inspection.
    Operations pass their exact consumed names so an unrelated ambient value
    cannot make the operation fail or enter its identity.
    """

    # Snapshot the mapping before resolving the first binding. A caller may
    # provide a live or mutable environment view; one operation must never
    # observe values from two different moments.
    values = dict(os.environ if environment is None else environment)
    selected_names = set(SUPPORTED_BINDINGS if names is None else names)
    unsupported = sorted(
        repr(name)
        for name in selected_names
        if type(name) is not str or name not in SUPPORTED_BINDINGS
    )
    if unsupported:
        _fail(
            "/binding_names",
            "contains unsupported bindings " + ", ".join(unsupported),
        )
    resolved: list[ResolvedBinding] = []
    for name in SUPPORTED_BINDINGS:
        if name not in selected_names:
            continue
        declaration = configuration.binding_declaration(name)
        if declaration is None:
            resolved.append(
                ResolvedBinding(name=name, source="undeclared", value=None)
            )
            continue
        if declaration.kind == "literal":
            resolved.append(
                ResolvedBinding(
                    name=name,
                    source="literal",
                    value=declaration.value,
                )
            )
            continue

        variable = declaration.value
        if variable not in values:
            resolved.append(
                ResolvedBinding(
                    name=name,
                    source="environment",
                    value=None,
                    environment_variable=variable,
                )
            )
            continue
        imported = values[variable]
        if type(imported) is not str:
            _fail(
                f"/bindings/{name}",
                f"environment variable {variable} must contain a string path",
            )
        _absolute_path(imported, f"/bindings/{name}")
        resolved.append(
            ResolvedBinding(
                name=name,
                source="environment",
                value=imported,
                environment_variable=variable,
            )
        )
    return ResolvedBindings(
        configuration_manifest_sha256=configuration.manifest.sha256,
        selection_digest=configuration.selection_digest,
        entries=tuple(resolved),
    )
