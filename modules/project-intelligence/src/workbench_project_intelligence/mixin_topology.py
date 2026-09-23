"""Deterministic, read-only topology receipts for Mixin-bearing archives.

The scanner deliberately stops at facts available from archive bytes.  It does
not claim that a discovered configuration was prepared or that a configured
mixin was applied at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Any, Iterable, Mapping, Sequence
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo


RECEIPT_FORMAT = (
    "workbench-project-intelligence-mixin-component-topology-receipt-v1"
)
RECEIPT_ID_PREFIX = "workbench-mixin-topology-receipt:sha256:"
COMPATIBILITY_RESOURCE = "cleanmix_version_compatibility.json"
MANIFEST_PATH = "META-INF/MANIFEST.MF"

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_COUNT = 200_000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024

_MANIFEST_ATTRIBUTE_RE = re.compile(r"^[A-Za-z0-9_-]{1,70}$")
_COMPATIBILITY_VERSION_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
_CLASS_NAME_RE = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*$"
)
_MULTI_RELEASE_MEMBER_RE = re.compile(
    r"^META-INF/versions/[1-9][0-9]*/(?P<logical>.+)$"
)

_EMBEDDED_PACKAGES = (
    ("asm", "org/objectweb/asm/"),
    ("guava", "com/google/common/"),
    ("mixin", "org/spongepowered/asm/"),
    ("mixinextras", "com/llamalad7/mixinextras/"),
)

_MIXINBOOTER_INTERFACES = {
    "zone/rong/mixinbooter/IEarlyMixinLoader": "mixinbooter-early-loader",
    "zone/rong/mixinbooter/ILateMixinLoader": "mixinbooter-late-loader",
}

_LIMITATIONS = (
    "Archive discovery does not prove runtime configuration preparation or mixin application.",
    "Configured mixin target selectors and programmatic registrations require runtime observation.",
    "Owner IDs are collision inputs inferred from static metadata or an artifact-name fallback, not runtime actor identity.",
)


class ArtifactScanError(RuntimeError):
    """Raised when an archive cannot produce an unambiguous receipt."""


@dataclass(frozen=True)
class ArtifactInput:
    """Exact archive bytes and the stable label used in the receipt."""

    label: str
    data: bytes


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical byte representation used by receipt identities."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def render_receipt(receipt: Mapping[str, Any], *, compact: bool = False) -> bytes:
    """Render a receipt deterministically with one trailing newline."""

    if compact:
        return canonical_json_bytes(receipt) + b"\n"
    return (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def normalize_cleanmix_owner_id(value: str) -> str:
    """Mirror CleanMix's lowercase ASCII-alphanumeric owner normalization."""

    if not isinstance(value, str):
        raise TypeError("owner ID must be a string")
    result: list[str] = []
    for character in value:
        if "0" <= character <= "9" or "a" <= character <= "z":
            result.append(character)
        elif "A" <= character <= "Z":
            result.append(chr(ord(character) + ord("a") - ord("A")))
    return "".join(result)


class _DuplicateJsonKey(ValueError):
    pass


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _load_json_bytes(raw: bytes, context: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeError,
        json.JSONDecodeError,
        _DuplicateJsonKey,
        ValueError,
    ) as exc:
        raise ArtifactScanError(f"{context} is malformed JSON: {exc}") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name:
        raise ArtifactScanError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ArtifactScanError(f"unsafe ZIP member name: {name!r}")


def _read_member(zf: ZipFile, info: ZipInfo, context: str) -> bytes:
    try:
        return zf.read(info)
    except (BadZipFile, RuntimeError, OSError, EOFError) as exc:
        raise ArtifactScanError(f"cannot read {context}: {exc}") from exc


def _inventory_archive(
    zf: ZipFile,
) -> tuple[dict[str, ZipInfo], list[dict[str, Any]], int]:
    try:
        infos = zf.infolist()
    except (BadZipFile, LargeZipFile, OSError) as exc:
        raise ArtifactScanError(f"cannot enumerate ZIP members: {exc}") from exc
    if len(infos) > MAX_MEMBER_COUNT:
        raise ArtifactScanError(
            f"ZIP has {len(infos)} members, limit is {MAX_MEMBER_COUNT}"
        )

    by_name: dict[str, ZipInfo] = {}
    inventory: list[dict[str, Any]] = []
    total_size = 0
    for info in infos:
        _validate_member_name(info.filename)
        if info.filename in by_name:
            raise ArtifactScanError(
                f"ZIP contains duplicate member {info.filename!r}"
            )
        by_name[info.filename] = info
        if info.flag_bits & 0x1:
            raise ArtifactScanError(
                f"encrypted ZIP member is unsupported: {info.filename}"
            )
        if info.file_size > MAX_MEMBER_BYTES:
            raise ArtifactScanError(
                f"ZIP member exceeds {MAX_MEMBER_BYTES} bytes: {info.filename}"
            )
        total_size += info.file_size
        if total_size > MAX_UNCOMPRESSED_BYTES:
            raise ArtifactScanError(
                "ZIP uncompressed size exceeds "
                f"{MAX_UNCOMPRESSED_BYTES} bytes"
            )
        if info.is_dir():
            continue
        raw = _read_member(zf, info, f"ZIP member {info.filename}")
        if len(raw) != info.file_size:
            raise ArtifactScanError(
                f"ZIP member size changed while reading: {info.filename}"
            )
        inventory.append(
            {
                "crc32": f"{info.CRC:08x}",
                "path": info.filename,
                "sha256": _sha256(raw),
                "size_bytes": info.file_size,
            }
        )
    return by_name, sorted(inventory, key=lambda row: row["path"]), total_size


def _find_case_insensitive(
    members: Mapping[str, ZipInfo], wanted: str
) -> str | None:
    matches = sorted(name for name in members if name.casefold() == wanted.casefold())
    if len(matches) > 1:
        raise ArtifactScanError(
            f"ZIP contains ambiguous case variants for {wanted}: {matches}"
        )
    return matches[0] if matches else None


def _parse_manifest(raw: bytes) -> tuple[list[dict[str, str]], dict[str, str]]:
    if b"\x00" in raw:
        raise ArtifactScanError("manifest contains a NUL byte")
    physical_lines = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")
    sections: list[list[bytes]] = [[]]
    current: bytearray | None = None
    for physical in physical_lines:
        if physical.startswith(b" "):
            if current is None:
                raise ArtifactScanError(
                    "manifest continuation line has no preceding attribute"
                )
            current.extend(physical[1:])
            continue
        if current is not None:
            sections[-1].append(bytes(current))
            current = None
        if not physical:
            if sections[-1]:
                sections.append([])
            continue
        current = bytearray(physical)
    if current is not None:
        sections[-1].append(bytes(current))
    sections = [section for section in sections if section]

    parsed_sections: list[list[tuple[str, str]]] = []
    for section_index, section in enumerate(sections):
        parsed: list[tuple[str, str]] = []
        seen: set[str] = set()
        for logical in section:
            separator = logical.find(b": ")
            if separator <= 0:
                raise ArtifactScanError(
                    f"manifest section {section_index} contains an invalid attribute"
                )
            try:
                name = logical[:separator].decode("ascii")
                value = logical[separator + 2 :].decode("utf-8")
            except UnicodeError as exc:
                raise ArtifactScanError(
                    f"manifest section {section_index} has invalid text: {exc}"
                ) from exc
            if _MANIFEST_ATTRIBUTE_RE.fullmatch(name) is None:
                raise ArtifactScanError(
                    f"manifest attribute name is invalid: {name!r}"
                )
            folded = name.casefold()
            if folded in seen:
                raise ArtifactScanError(
                    f"manifest section {section_index} repeats {name!r}"
                )
            seen.add(folded)
            parsed.append((name, value))
        if section_index and parsed[0][0].casefold() != "name":
            raise ArtifactScanError(
                f"manifest entry section {section_index} does not begin with Name"
            )
        parsed_sections.append(parsed)

    main = parsed_sections[0] if parsed_sections else []
    rows = [
        {"name": name, "value": value}
        for name, value in sorted(main, key=lambda pair: pair[0].casefold())
    ]
    lookup = {name.casefold(): value for name, value in main}
    return rows, lookup


def _manifest_values(lookup: Mapping[str, str], attribute: str) -> list[str]:
    raw = lookup.get(attribute.casefold())
    if raw is None:
        return []
    values = [part.strip() for part in raw.split(",")]
    if not values or any(not value for value in values):
        raise ArtifactScanError(
            f"manifest attribute {attribute} contains an empty value"
        )
    if len(values) != len(set(values)):
        raise ArtifactScanError(
            f"manifest attribute {attribute} repeats a value"
        )
    return sorted(values)


def _resource_path(value: str, context: str) -> str:
    if "\\" in value or "\x00" in value:
        raise ArtifactScanError(f"{context} is not a safe archive path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value.endswith("/"):
        raise ArtifactScanError(f"{context} is not a safe archive path")
    return value


def _service_providers(raw: bytes, context: str) -> list[str]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ArtifactScanError(f"{context} has invalid UTF-8: {exc}") from exc
    providers: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        provider = line.split("#", 1)[0].strip()
        if not provider:
            continue
        if _CLASS_NAME_RE.fullmatch(provider) is None:
            raise ArtifactScanError(
                f"{context} line {line_number} has invalid provider {provider!r}"
            )
        providers.append(provider)
    if len(providers) != len(set(providers)):
        raise ArtifactScanError(f"{context} repeats a provider")
    return sorted(providers)


def _class_interfaces(raw: bytes, context: str) -> list[str]:
    """Read only the constant pool and direct interface table of a class."""

    position = 0

    def take(size: int) -> bytes:
        nonlocal position
        if position + size > len(raw):
            raise ArtifactScanError(f"{context} has a truncated class header")
        value = raw[position : position + size]
        position += size
        return value

    def u1() -> int:
        return take(1)[0]

    def u2() -> int:
        return struct.unpack(">H", take(2))[0]

    if take(4) != b"\xca\xfe\xba\xbe":
        raise ArtifactScanError(f"{context} has an invalid class magic")
    take(4)  # minor and major versions
    pool_count = u2()
    pool: list[tuple[str, Any] | None] = [None] * pool_count
    index = 1
    while index < pool_count:
        tag = u1()
        if tag == 1:
            length = u2()
            encoded = take(length)
            pool[index] = ("utf8", encoded)
        elif tag in {3, 4}:
            take(4)
        elif tag in {5, 6}:
            take(8)
            index += 1
        elif tag == 7:
            pool[index] = ("class", u2())
        elif tag == 8:
            take(2)
        elif tag in {9, 10, 11, 12, 17, 18}:
            take(4)
        elif tag == 15:
            take(3)
        elif tag in {16, 19, 20}:
            take(2)
        else:
            raise ArtifactScanError(
                f"{context} has unknown class constant tag {tag}"
            )
        index += 1
    take(6)  # access flags, this class, superclass
    interface_count = u2()
    class_indices = [u2() for _ in range(interface_count)]

    result: list[str] = []
    for class_index in class_indices:
        try:
            class_entry = pool[class_index]
            if class_entry is None or class_entry[0] != "class":
                raise IndexError
            name_entry = pool[class_entry[1]]
            if name_entry is None or name_entry[0] != "utf8":
                raise IndexError
            name = name_entry[1].decode("ascii")
        except (IndexError, TypeError, UnicodeError, AttributeError):
            raise ArtifactScanError(
                f"{context} has an invalid interface constant reference"
            ) from None
        result.append(str(name))
    return sorted(result)


def _class_member(class_name: str, context: str) -> str:
    if _CLASS_NAME_RE.fullmatch(class_name) is None:
        raise ArtifactScanError(f"{context} has invalid class name {class_name!r}")
    return class_name.replace(".", "/") + ".class"


def _registration_routes(
    zf: ZipFile,
    members: Mapping[str, ZipInfo],
    manifest: Mapping[str, str],
) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []

    def add(kind: str, source: str, values: Sequence[str], dynamic: bool) -> None:
        if values:
            routes.append(
                {
                    "dynamic": dynamic,
                    "kind": kind,
                    "source": source,
                    "values": sorted(set(values)),
                }
            )

    config_paths = [
        _resource_path(value, "manifest MixinConfigs value")
        for value in _manifest_values(manifest, "MixinConfigs")
    ]
    add(
        "manifest-mixin-configs",
        f"{MANIFEST_PATH}:MixinConfigs",
        config_paths,
        False,
    )
    connectors = _manifest_values(manifest, "MixinConnector")
    for connector in connectors:
        _class_member(connector, "manifest MixinConnector")
    add(
        "manifest-mixin-connector",
        f"{MANIFEST_PATH}:MixinConnector",
        connectors,
        True,
    )
    tweak_classes = _manifest_values(manifest, "TweakClass")
    for tweak_class in tweak_classes:
        _class_member(tweak_class, "manifest TweakClass")
    add(
        "launchwrapper-tweak-class",
        f"{MANIFEST_PATH}:TweakClass",
        tweak_classes,
        True,
    )
    core_plugins = _manifest_values(manifest, "FMLCorePlugin")
    for core_plugin in core_plugins:
        member = _class_member(core_plugin, "manifest FMLCorePlugin")
        add(
            "forge-core-plugin",
            f"{MANIFEST_PATH}:FMLCorePlugin",
            [core_plugin],
            True,
        )
        info = members.get(member)
        if info is None:
            continue
        interfaces = _class_interfaces(
            _read_member(zf, info, f"core plugin class {member}"),
            f"core plugin class {member}",
        )
        for interface in interfaces:
            kind = _MIXINBOOTER_INTERFACES.get(interface)
            if kind is not None:
                add(kind, f"class-interface:{interface}", [core_plugin], True)

    for name, info in sorted(members.items()):
        prefix = "META-INF/services/"
        if not name.startswith(prefix) or info.is_dir():
            continue
        service = name[len(prefix) :]
        if "mixin" not in service.casefold() and "spongepowered.asm" not in service:
            continue
        providers = _service_providers(
            _read_member(zf, info, f"service provider {name}"),
            f"service provider {name}",
        )
        add("java-service-provider", name, providers, True)
    return sorted(
        routes,
        key=lambda row: (row["kind"], row["source"], tuple(row["values"])),
    )


def _is_mixin_config_name(path: str) -> bool:
    name = PurePosixPath(path).name.casefold()
    if not name.endswith(".json") or "refmap" in name:
        return False
    return (
        name.startswith("mixins.")
        or name.endswith(".mixins.json")
        or name.endswith(".mixin.json")
        or ".mixin." in name
    )


def _optional_string(value: Any, context: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ArtifactScanError(f"{context} must be a non-empty string or null")
    return value


def _optional_integer(value: Any, context: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ArtifactScanError(f"{context} must be an integer or null")
    return value


def _configuration_classes(
    config: Mapping[str, Any],
    config_path: str,
    members: Mapping[str, ZipInfo],
    compatibility_entries: Mapping[str, str],
) -> list[dict[str, Any]]:
    package = _optional_string(config.get("package"), f"{config_path}.package")
    package_prefix = None
    if package is not None:
        package_prefix = package if package.endswith(".") else package + "."
    result: list[dict[str, Any]] = []
    for environment, key in (
        ("common", "mixins"),
        ("client", "client"),
        ("server", "server"),
    ):
        declared = config.get(key, [])
        if not isinstance(declared, list):
            raise ArtifactScanError(f"{config_path}.{key} must be an array")
        seen: set[str] = set()
        for index, value in enumerate(declared):
            if not isinstance(value, str) or not value:
                raise ArtifactScanError(
                    f"{config_path}.{key}[{index}] must be a non-empty string"
                )
            class_name = value if package_prefix is None else package_prefix + value
            member = _class_member(class_name, f"{config_path}.{key}[{index}]")
            if class_name in seen:
                raise ArtifactScanError(
                    f"{config_path}.{key} repeats mixin {class_name!r}"
                )
            seen.add(class_name)
            result.append(
                {
                    "class_member": member,
                    "class_name": class_name,
                    "compatibility_version": compatibility_entries.get(class_name),
                    "declared_name": value,
                    "environment": environment,
                    "package_resolvable": package_prefix is not None,
                    "present": member in members,
                }
            )
    return sorted(
        result,
        key=lambda row: (row["class_name"], row["environment"], row["declared_name"]),
    )


def _compatibility_metadata(
    zf: ZipFile, members: Mapping[str, ZipInfo]
) -> dict[str, Any]:
    info = members.get(COMPATIBILITY_RESOURCE)
    if info is None:
        return {
            "entries": {},
            "invalid_entries": [],
            "path": COMPATIBILITY_RESOURCE,
            "present": False,
            "sha256": None,
        }
    raw = _read_member(zf, info, COMPATIBILITY_RESOURCE)
    value = _load_json_bytes(raw, COMPATIBILITY_RESOURCE)
    if not isinstance(value, dict):
        raise ArtifactScanError(
            f"{COMPATIBILITY_RESOURCE} root must be an object"
        )
    entries: dict[str, str] = {}
    invalid_entries: list[dict[str, Any]] = []
    for key, version in sorted(value.items()):
        if not isinstance(key, str) or not key:
            raise ArtifactScanError(
                f"{COMPATIBILITY_RESOURCE} contains an invalid key"
            )
        match = (
            _COMPATIBILITY_VERSION_RE.fullmatch(version)
            if isinstance(version, str)
            else None
        )
        if match is None or any(int(part) > 999 for part in match.groups()):
            invalid_entries.append(
                {
                    "key": key,
                    "value": version,
                    "reason": (
                        "version must have three decimal parts no greater than 999"
                    ),
                }
            )
            continue
        entries[key] = version
    return {
        "entries": entries,
        "invalid_entries": invalid_entries,
        "path": COMPATIBILITY_RESOURCE,
        "present": True,
        "sha256": _sha256(raw),
    }


def _scan_mixin_configs(
    zf: ZipFile,
    members: Mapping[str, ZipInfo],
    routes: Sequence[Mapping[str, Any]],
    compatibility: Mapping[str, Any],
) -> list[dict[str, Any]]:
    registered: dict[str, list[str]] = {}
    for route in routes:
        if route["kind"] != "manifest-mixin-configs":
            continue
        for path in route["values"]:
            registered.setdefault(path, []).append(str(route["kind"]))
            if path not in members:
                raise ArtifactScanError(
                    f"manifest registers missing mixin config {path!r}"
                )
    candidates = {
        name for name in members if _is_mixin_config_name(name)
    } | set(registered)
    result: list[dict[str, Any]] = []
    compatibility_entries = compatibility["entries"]
    for path in sorted(candidates):
        info = members[path]
        raw = _read_member(zf, info, f"mixin config {path}")
        config = _load_json_bytes(raw, f"mixin config {path}")
        if not isinstance(config, dict):
            raise ArtifactScanError(f"mixin config {path} root must be an object")
        if not any(key in config for key in ("mixins", "client", "server")):
            raise ArtifactScanError(
                f"mixin config {path} declares no mixin arrays"
            )
        required = config.get("required")
        if required is not None and not isinstance(required, bool):
            raise ArtifactScanError(f"{path}.required must be a boolean")
        package = _optional_string(config.get("package"), f"{path}.package")
        plugin_name = _optional_string(config.get("plugin"), f"{path}.plugin")
        plugin: dict[str, Any] | None = None
        if plugin_name is not None:
            plugin_member = _class_member(plugin_name, f"{path}.plugin")
            plugin_info = members.get(plugin_member)
            plugin = {
                "class_member": plugin_member,
                "class_name": plugin_name,
                "interfaces": (
                    None
                    if plugin_info is None
                    else _class_interfaces(
                        _read_member(
                            zf,
                            plugin_info,
                            f"mixin config plugin class {plugin_member}",
                        ),
                        f"mixin config plugin class {plugin_member}",
                    )
                ),
                "present": plugin_info is not None,
            }
        refmap = _optional_string(config.get("refmap"), f"{path}.refmap")
        if refmap is not None:
            refmap = _resource_path(refmap, f"{path}.refmap")
        injectors = config.get("injectors")
        if injectors is not None and not isinstance(injectors, dict):
            raise ArtifactScanError(f"{path}.injectors must be an object or null")
        required_features = config.get("requiredFeatures", [])
        if not isinstance(required_features, list):
            raise ArtifactScanError(f"{path}.requiredFeatures must be an array")
        normalized_features: list[str] = []
        for index, feature in enumerate(required_features):
            if not isinstance(feature, str) or not feature:
                raise ArtifactScanError(
                    f"{path}.requiredFeatures[{index}] must be a non-empty string"
                )
            normalized_features.append(feature)
        if len(normalized_features) != len(set(normalized_features)):
            raise ArtifactScanError(f"{path}.requiredFeatures repeats a value")
        result.append(
            {
                "compatibility_level": _optional_string(
                    config.get("compatibilityLevel"),
                    f"{path}.compatibilityLevel",
                ),
                "injector_defaults": injectors,
                "min_version": _optional_string(
                    config.get("minVersion"), f"{path}.minVersion"
                ),
                "mixins": _configuration_classes(
                    config,
                    path,
                    members,
                    compatibility_entries,
                ),
                "package": package,
                "path": path,
                "plugin": plugin,
                "priority": _optional_integer(
                    config.get("priority"), f"{path}.priority"
                ),
                "mixin_priority": _optional_integer(
                    config.get("mixinPriority"), f"{path}.mixinPriority"
                ),
                "refmap_path": refmap,
                "registered_via": sorted(set(registered.get(path, []))),
                "required": required,
                "required_features": sorted(normalized_features),
                "semantic_options_sha256": _sha256(
                    canonical_json_bytes(config)
                ),
                "sha256": _sha256(raw),
                "target_phase": _optional_string(
                    config.get("target"), f"{path}.target"
                ),
            }
        )
    return result


def _mapping_count(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    count = 0
    for mappings in value.values():
        if isinstance(mappings, dict):
            count += len(mappings)
    return count


def _scan_refmaps(
    zf: ZipFile,
    members: Mapping[str, ZipInfo],
    configs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    declarations: dict[str, list[str]] = {}
    for config in configs:
        path = config["refmap_path"]
        if path is not None:
            declarations.setdefault(path, []).append(str(config["path"]))
    discovered = {
        name
        for name in members
        if PurePosixPath(name).name.casefold().endswith("refmap.json")
    }
    result: list[dict[str, Any]] = []
    for path in sorted(discovered | set(declarations)):
        info = members.get(path)
        if info is None:
            result.append(
                {
                    "declared_by": sorted(declarations[path]),
                    "mapping_count": 0,
                    "mapping_namespaces": [],
                    "path": path,
                    "present": False,
                    "root_keys": [],
                    "sha256": None,
                }
            )
            continue
        raw = _read_member(zf, info, f"refmap {path}")
        value = _load_json_bytes(raw, f"refmap {path}")
        if not isinstance(value, dict):
            raise ArtifactScanError(f"refmap {path} root must be an object")
        data = value.get("data", {})
        if data is not None and not isinstance(data, dict):
            raise ArtifactScanError(f"refmap {path}.data must be an object")
        mappings = value.get("mappings", {})
        if mappings is not None and not isinstance(mappings, dict):
            raise ArtifactScanError(f"refmap {path}.mappings must be an object")
        result.append(
            {
                "declared_by": sorted(declarations.get(path, [])),
                "mapping_count": _mapping_count(mappings),
                "mapping_namespaces": sorted(data),
                "path": path,
                "present": True,
                "root_keys": sorted(value),
                "sha256": _sha256(raw),
            }
        )
    return result


def _embedded_packages(members: Mapping[str, ZipInfo]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    class_members = sorted(
        name for name, info in members.items() if not info.is_dir() and name.endswith(".class")
    )
    for component, prefix in _EMBEDDED_PACKAGES:
        matches = []
        for name in class_members:
            multi_release = _MULTI_RELEASE_MEMBER_RE.fullmatch(name)
            logical = (
                multi_release.group("logical")
                if multi_release is not None
                else name
            )
            if logical.startswith(prefix):
                matches.append(name)
        result.append(
            {
                "class_count": len(matches),
                "component": component,
                "prefix": prefix,
                "present": bool(matches),
                "sample_members": matches[:10],
            }
        )
    return result


def _jar_base_name(label: str) -> str:
    name = PurePosixPath(label.replace("\\", "/")).name
    if name.endswith(".jar"):
        name = name[: -len(".jar")]
    index = len(name)
    while index > 0 and (name[index - 1].isdigit() or name[index - 1] == "."):
        index -= 1
    if index not in {0, len(name)}:
        if name[index - 1] == "-":
            index -= 1
        name = name[:index]
    return name


def _owner_id_inputs(
    zf: ZipFile, members: Mapping[str, ZipInfo], label: str
) -> list[dict[str, str]]:
    info = members.get("mcmod.info")
    raw_ids: list[tuple[str, str]] = []
    if info is not None:
        value = _load_json_bytes(
            _read_member(zf, info, "mcmod.info"), "mcmod.info"
        )
        rows: Any
        if isinstance(value, list):
            rows = value
        elif isinstance(value, dict) and isinstance(value.get("modList"), list):
            rows = value["modList"]
        else:
            raise ArtifactScanError(
                "mcmod.info must be an array or contain a modList array"
            )
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ArtifactScanError(f"mcmod.info[{index}] must be an object")
            mod_id = row.get("modid")
            if mod_id is None:
                continue
            if not isinstance(mod_id, str) or not mod_id:
                raise ArtifactScanError(
                    f"mcmod.info[{index}].modid must be a non-empty string"
                )
            raw_ids.append((mod_id, f"mcmod.info[{index}].modid"))
    if not raw_ids:
        fallback = _jar_base_name(label)
        if not fallback:
            raise ArtifactScanError(
                f"artifact label {label!r} cannot provide an owner-ID fallback"
            )
        raw_ids.append((fallback, "artifact-file-name-fallback"))
    result = []
    for raw_id, source in raw_ids:
        normalized = normalize_cleanmix_owner_id(raw_id)
        if not normalized:
            raise ArtifactScanError(
                f"owner ID {raw_id!r} from {source} has no ASCII-alphanumeric identity"
            )
        result.append(
            {
                "normalized_owner_id": normalized,
                "raw_owner_id": raw_id,
                "source": source,
            }
        )
    unique = {
        (row["raw_owner_id"], row["normalized_owner_id"], row["source"]): row
        for row in result
    }
    return [unique[key] for key in sorted(unique)]


def scan_artifact_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    """Scan one exact JAR/ZIP byte sequence without executing its contents."""

    if not isinstance(data, bytes):
        raise TypeError("artifact data must be bytes")
    if not isinstance(label, str) or not label or "\x00" in label:
        raise ArtifactScanError("artifact label must be a non-empty string")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArtifactScanError(
            f"archive exceeds {MAX_ARCHIVE_BYTES} input bytes: {label}"
        )
    archive_sha256 = _sha256(data)
    try:
        with ZipFile(BytesIO(data), "r", allowZip64=True) as zf:
            members, inventory, uncompressed_size = _inventory_archive(zf)
            manifest_path = _find_case_insensitive(members, MANIFEST_PATH)
            manifest_rows: list[dict[str, str]] = []
            manifest_lookup: dict[str, str] = {}
            manifest_sha256: str | None = None
            if manifest_path is not None:
                manifest_raw = _read_member(
                    zf, members[manifest_path], "JAR manifest"
                )
                manifest_rows, manifest_lookup = _parse_manifest(manifest_raw)
                manifest_sha256 = _sha256(manifest_raw)
            routes = _registration_routes(zf, members, manifest_lookup)
            compatibility = _compatibility_metadata(zf, members)
            configs = _scan_mixin_configs(
                zf, members, routes, compatibility
            )
            refmaps = _scan_refmaps(zf, members, configs)
            packages = _embedded_packages(members)
            owner_inputs = _owner_id_inputs(zf, members, label)
    except (BadZipFile, LargeZipFile, OSError, EOFError) as exc:
        raise ArtifactScanError(f"{label} is not a valid ZIP archive: {exc}") from exc

    return {
        "compatibility_metadata": compatibility,
        "embedded_packages": packages,
        "identity": {
            "member_count": len(inventory),
            "member_inventory_sha256": _sha256(canonical_json_bytes(inventory)),
            "sha256": archive_sha256,
            "size_bytes": len(data),
            "uncompressed_size_bytes": uncompressed_size,
        },
        "label": label,
        "member_inventory": inventory,
        "manifest": {
            "main_attributes": manifest_rows,
            "path": manifest_path,
            "present": manifest_path is not None,
            "sha256": manifest_sha256,
        },
        "mixin_configs": configs,
        "owner_id_inputs": owner_inputs,
        "refmaps": refmaps,
        "registration_routes": routes,
    }


def _owner_id_collisions(
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, str]]] = {}
    for artifact in artifacts:
        digest = str(artifact["identity"]["sha256"])
        label = str(artifact["label"])
        for owner in artifact["owner_id_inputs"]:
            groups.setdefault(owner["normalized_owner_id"], []).append(
                {
                    "artifact_label": label,
                    "artifact_sha256": digest,
                    "raw_owner_id": owner["raw_owner_id"],
                    "source": owner["source"],
                }
            )
    result: list[dict[str, Any]] = []
    for normalized, inputs in sorted(groups.items()):
        artifact_ids = {item["artifact_sha256"] for item in inputs}
        if len(artifact_ids) < 2:
            continue
        result.append(
            {
                "inputs": sorted(
                    inputs,
                    key=lambda row: (
                        row["artifact_sha256"],
                        row["raw_owner_id"],
                        row["source"],
                    ),
                ),
                "normalized_owner_id": normalized,
            }
        )
    return result


def _embedded_package_overlaps(
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for artifact in artifacts:
        for package in artifact["embedded_packages"]:
            if not package["present"]:
                continue
            key = (package["component"], package["prefix"])
            groups.setdefault(key, []).append(
                {
                    "artifact_label": artifact["label"],
                    "artifact_sha256": artifact["identity"]["sha256"],
                    "class_count": package["class_count"],
                }
            )
    result: list[dict[str, Any]] = []
    for (component, prefix), rows in sorted(groups.items()):
        if len({row["artifact_sha256"] for row in rows}) < 2:
            continue
        result.append(
            {
                "artifacts": sorted(
                    rows,
                    key=lambda row: (row["artifact_sha256"], row["artifact_label"]),
                ),
                "component": component,
                "prefix": prefix,
            }
        )
    return result


def build_topology_receipt(
    artifacts: Iterable[ArtifactInput | tuple[str, bytes]],
) -> dict[str, Any]:
    """Scan one or more archives and bind their deterministic receipt."""

    scanned: list[dict[str, Any]] = []
    for artifact in artifacts:
        if isinstance(artifact, ArtifactInput):
            item = artifact
        else:
            try:
                label, data = artifact
            except (TypeError, ValueError) as exc:
                raise TypeError(
                    "artifacts must contain ArtifactInput or (label, bytes) pairs"
                ) from exc
            item = ArtifactInput(label=label, data=data)
        scanned.append(scan_artifact_bytes(item.data, label=item.label))
    if not scanned:
        raise ArtifactScanError("at least one artifact is required")

    scanned.sort(key=lambda row: (row["identity"]["sha256"], row["label"]))
    identities = [row["identity"]["sha256"] for row in scanned]
    if len(identities) != len(set(identities)):
        raise ArtifactScanError("the artifact set repeats an exact archive identity")
    labels = [row["label"] for row in scanned]
    if len(labels) != len(set(labels)):
        raise ArtifactScanError("the artifact set repeats a label")

    from .mixin_receipt import build_contract_receipt

    return build_contract_receipt(scanned)


def scan_artifact_paths(paths: Iterable[Path | str]) -> dict[str, Any]:
    """Read exact regular files and return a topology receipt."""

    inputs: list[ArtifactInput] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_symlink() or not path.is_file():
            raise ArtifactScanError(
                f"artifact path is not a regular non-symlink file: {path}"
            )
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactScanError(f"cannot read artifact {path}: {exc}") from exc
        inputs.append(ArtifactInput(label=path.name, data=data))
    return build_topology_receipt(inputs)
