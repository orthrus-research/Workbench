#!/usr/bin/env python3

"""Repository and vertical-slice validation for Workbench."""

from __future__ import annotations

import argparse
import ast
import json
import re
import signal
import stat
import subprocess
import sys
import tomllib
from collections.abc import Hashable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from orchestration import (
    OrchestrationFailure,
    fingerprint_paths,
)
from suite_catalog import PYTHON_TEST_SUITES, SUITES_BY_NAME
from invocation import Invocation
from suite_selection import select_impacted_suites
from suite_execution import (
    SuiteExecutionFailure,
    run_python_suites,
    _terminate_process,
)

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "api/src", ROOT / "core/src"):
    sys.path.insert(0, str(source))
sys.path.insert(0, str(ROOT / "tools"))

WORKBENCH_SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
if str(WORKBENCH_SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(WORKBENCH_SHELL_SOURCE))

from workbench_core.configuration import (  # noqa: E402
    WorkbenchConfigurationError,
    load_workbench_configuration,
)
from build_paths import (  # noqa: E402
    BuildPathError,
    load_build_paths,
)
from repository_policy import (  # noqa: E402
    PublicRepositoryError,
    load_public_repository,
    load_release_units,
)
from workbench_shell.product_capability_catalog import (  # noqa: E402
    SCHEMA_RELATIVE as PRODUCT_CAPABILITY_SCHEMA,
    ProductCapabilityCatalogError,
    build_product_capability_catalog,
)
from workbench_core.runtime_java import (  # noqa: E402
    JavaRuntimeError,
    load_java_runtime_policy,
)
DISALLOWED_SUFFIXES = {
    ".7z",
    ".bz2",
    ".class",
    ".dll",
    ".dmg",
    ".doc",
    ".docx",
    ".dylib",
    ".exe",
    ".gz",
    ".jar",
    ".msi",
    ".ppt",
    ".pptx",
    ".pyc",
    ".rar",
    ".so",
    ".tar",
    ".xls",
    ".xlsx",
    ".xz",
    ".zip",
}
MAX_SOURCE_BYTES = 2 * 1024 * 1024
LOCAL_PATH_RE = re.compile(r"(?:/" "home/|/" "mnt/[a-z]/)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
class ValidationFailure(RuntimeError):
    pass


def _positive_worker_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if count < 1 or count > 32:
        raise argparse.ArgumentTypeError("must be between 1 and 32")
    return count


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects keys hidden by a later duplicate."""


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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"found duplicate JSON key {key!r}")
        result[key] = value
    return result


def repository_files() -> list[Path]:
    """Return tracked and new source files without walking ignored tool trees."""

    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValidationFailure(f"cannot enumerate repository files: {detail}")
    paths = [
        ROOT / raw.decode("utf-8")
        for raw in completed.stdout.split(b"\0")
        if raw
    ]
    return sorted(path for path in paths if path.is_file())


def validate_serialized_files(files: list[Path]) -> None:
    for path in files:
        relative = path.relative_to(ROOT)
        try:
            if path.suffix == ".json":
                json.loads(
                    path.read_text(encoding="utf-8"),
                    object_pairs_hook=_unique_json_object,
                    parse_constant=lambda token: (_ for _ in ()).throw(
                        ValueError(f"unsupported JSON constant {token!r}")
                    ),
                )
            elif path.suffix in {".yaml", ".yml"}:
                yaml.load(
                    path.read_text(encoding="utf-8"),
                    Loader=_UniqueKeyLoader,
                )
            elif path.suffix == ".toml":
                tomllib.loads(path.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeError,
            ValueError,
            json.JSONDecodeError,
            tomllib.TOMLDecodeError,
            yaml.YAMLError,
        ) as exc:
            raise ValidationFailure(f"cannot parse {relative}: {exc}") from exc


def validate_checked_in_schema_instances(
    data_root: Path | None = None,
    schema_root: Path | None = None,
) -> None:
    """Validate each checked-in shell datum with its same-name transport schema.

    Semantic owner validators remain authoritative. This cheap positive-path
    check catches schema/data drift before any intensive behavioral suite.
    """

    data_root = data_root or ROOT / "modules/workbench-shell/data"
    schema_root = schema_root or ROOT / "modules/workbench-shell/schemas"

    def display(path: Path) -> Path:
        try:
            return path.relative_to(ROOT)
        except ValueError:
            return path

    checked = 0
    for data_path in sorted(data_root.glob("*.json")):
        schema_path = schema_root / f"{data_path.stem}.schema.json"
        if not schema_path.is_file():
            continue
        try:
            value = json.loads(data_path.read_text(encoding="utf-8"))
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).validate(value)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            SchemaError,
            ValidationError,
        ) as exc:
            raise ValidationFailure(
                f"checked-in schema instance failed: "
                f"{display(data_path)} against {display(schema_path)}: {exc}"
            ) from exc
        checked += 1
    if checked == 0:
        raise ValidationFailure("no checked-in shell data/schema pairs found")


def validate_file_policy(files: list[Path]) -> None:
    violations: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT)
        if path.suffix.lower() in DISALLOWED_SUFFIXES:
            violations.append(f"disallowed binary/archive: {relative}")
        if path.stat().st_size > MAX_SOURCE_BYTES:
            violations.append(
                f"source file exceeds {MAX_SOURCE_BYTES} bytes: {relative}"
            )
        if path.suffix in {".md", ".json", ".yaml", ".yml", ".py"}:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeError as exc:
                violations.append(f"invalid UTF-8: {relative}: {exc}")
                continue
            # Tests legitimately model WSL and Unix paths. The leak check is for
            # committed product data and documentation, not fixture literals.
            if "tests" not in relative.parts and LOCAL_PATH_RE.search(text):
                violations.append(f"local workstation path: {relative}")
    if violations:
        raise ValidationFailure("\n".join(violations))


def validate_links(files: list[Path]) -> None:
    missing: list[str] = []
    documents = sorted(path for path in files if path.suffix == ".md")
    for document in documents:
        if not document.is_file():
            continue
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
            ):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                missing.append(
                    f"{document.relative_to(ROOT)} -> {target}"
                )
    if missing:
        raise ValidationFailure(
            "broken documentation links:\n" + "\n".join(sorted(missing))
        )


def validate_python_sources(files: list[Path]) -> None:
    """Compile only source-controlled Python instead of traversing tool caches."""

    failures: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            compile(path.read_bytes(), str(path), "exec", flags=ast.PyCF_ONLY_AST)
        except (OSError, SyntaxError, ValueError) as exc:
            failures.append(f"{path.relative_to(ROOT)}: {exc}")
    if failures:
        raise ValidationFailure("Python compilation failed:\n" + "\n".join(failures))


def validate_profiles() -> None:
    try:
        configuration = load_workbench_configuration(ROOT)
    except WorkbenchConfigurationError as exc:
        raise ValidationFailure(
            f"cannot load the selected Workbench configuration: {exc}"
        ) from exc

    platform = configuration.platform_document.values
    platform_label = (
        f"selected platform profile {configuration.platform_profile_id}"
    )
    if not isinstance(platform, Mapping):
        raise ValidationFailure(f"{platform_label} must be an object")
    if platform.get("kind") != "cleanroom":
        raise ValidationFailure(
            f"{platform_label} is not supported by the Cleanroom runtime lane"
        )
    cleanroom_version = platform.get("cleanroom_version")
    if (
        not isinstance(cleanroom_version, str)
        or not cleanroom_version
        or cleanroom_version == "unresolved"
    ):
        raise ValidationFailure(
            f"{platform_label} lacks an exact Cleanroom version"
        )
    try:
        load_java_runtime_policy(ROOT, configuration=configuration)
    except JavaRuntimeError as exc:
        raise ValidationFailure(
            f"{platform_label} has invalid Java runtime policy: {exc}"
        ) from exc

    runtime_artifacts = platform.get("runtime_artifacts")
    if not isinstance(runtime_artifacts, Mapping):
        raise ValidationFailure(
            f"{platform_label} lacks runtime artifact locks"
        )
    for artifact_id in (
        "packwiz_installer",
        "cleanroom_client",
        "cleanroom_server",
    ):
        artifact = runtime_artifacts.get(artifact_id)
        if not isinstance(artifact, Mapping):
            raise ValidationFailure(
                f"{platform_label} lacks {artifact_id}"
            )
        url = artifact.get("url")
        source_revision = artifact.get("source_revision")
        size = artifact.get("size")
        digest = artifact.get("sha256")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValidationFailure(
                f"{platform_label} {artifact_id} lacks an HTTPS URL"
            )
        if (
            not isinstance(source_revision, str)
            or re.fullmatch(r"[0-9a-f]{40}", source_revision) is None
        ):
            raise ValidationFailure(
                f"{platform_label} {artifact_id} lacks an exact "
                "source revision"
            )
        if type(size) is not int or size <= 0:
            raise ValidationFailure(
                f"{platform_label} {artifact_id} lacks an exact size"
            )
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ValidationFailure(
                f"{platform_label} {artifact_id} lacks an exact SHA-256"
            )

    profile = configuration.pack_document.values
    profile_path = configuration.pack_document.source.path
    profile_label = f"selected pack profile {configuration.pack_profile_id}"
    if not isinstance(profile, Mapping):
        raise ValidationFailure(f"{profile_label} must be an object")
    profiles = profile.get("profiles")
    if not isinstance(profiles, Mapping):
        raise ValidationFailure(f"{profile_label} lacks pack variants")
    selected = profiles.get(configuration.pack_variant)
    if not isinstance(selected, Mapping):
        raise ValidationFailure(
            f"{profile_label} lacks selected variant "
            f"{configuration.pack_variant}"
        )
    if selected.get("maturity") == "experimental" and selected.get(
        "stable_release"
    ):
        raise ValidationFailure(
            "an experimental profile cannot claim stable release"
        )

    blueprints = profile.get("blueprints")
    if not isinstance(blueprints, Mapping):
        raise ValidationFailure(f"{profile_label} lacks Blueprints policy")
    experimental_patterns = blueprints.get("experimental_patterns")
    if (
        not isinstance(experimental_patterns, Sequence)
        or isinstance(experimental_patterns, (str, bytes))
        or not experimental_patterns
    ):
        raise ValidationFailure(
            f"{profile_label} lacks an experimental Blueprint pattern"
        )
    profile_root = profile_path.parent.resolve()
    for relative in experimental_patterns:
        if not isinstance(relative, str) or not relative:
            raise ValidationFailure(
                f"{profile_label} experimental Blueprint path is invalid"
            )
        candidate = (profile_root / relative).resolve()
        if not candidate.is_relative_to(profile_root):
            raise ValidationFailure(
                f"{profile_label} experimental Blueprint escapes its pack profile"
            )
        if "tests" in candidate.relative_to(profile_root).parts:
            raise ValidationFailure(
                f"{profile_label} cannot treat a test fixture as authority"
            )
        if (
            candidate.suffix not in {".json", ".yaml"}
            or not candidate.is_file()
        ):
            raise ValidationFailure(
                f"{profile_label} experimental Blueprint is missing: {relative}"
            )
    registry_relative = blueprints.get("experimental_registry")
    if not isinstance(registry_relative, str) or not registry_relative:
        raise ValidationFailure(
            f"{profile_label} lacks an experimental Blueprint registry"
        )
    registry_path = (profile_root / registry_relative).resolve()
    if (
        not registry_path.is_relative_to(profile_root)
        or not registry_path.is_file()
    ):
        raise ValidationFailure(
            f"{profile_label} experimental Blueprint registry is invalid"
        )

    catalog_relative = blueprints.get("registration_catalog")
    if not isinstance(catalog_relative, str) or not catalog_relative:
        raise ValidationFailure(
            f"{profile_label} lacks a registration catalog"
        )
    catalog_path = (profile_root / catalog_relative).resolve()
    if not catalog_path.is_relative_to(profile_root):
        raise ValidationFailure(
            f"{profile_label} registration catalog escapes its pack profile"
        )
    blueprints_source = ROOT / "modules/blueprints/src"
    source_text = str(blueprints_source)
    if source_text not in sys.path:
        sys.path.insert(0, source_text)
    try:
        from workbench_blueprints.registration_catalog import (
            RegistrationCatalogError,
            load_registration_catalog,
        )
    except ImportError as exc:
        raise ValidationFailure(
            f"{profile_label} registration catalog loader is unavailable: {exc}"
        ) from exc
    try:
        catalog = load_registration_catalog(catalog_path)
    except RegistrationCatalogError as exc:
        raise ValidationFailure(
            f"{profile_label} registration catalog is invalid: {exc}"
        ) from exc
    if catalog.get("profile_family_id") != configuration.pack_profile_id:
        raise ValidationFailure(
            f"{profile_label} registration catalog belongs to another profile"
        )


def _validate_migration_destinations(
    imports: object,
    *,
    root: Path = ROOT,
) -> None:
    if not isinstance(imports, list) or not imports:
        raise ValidationFailure("migration import groups are missing")
    identifiers: set[str] = set()
    for row in imports:
        if not isinstance(row, dict):
            raise ValidationFailure("migration import group must be an object")
        for field in ("id", "source_roots", "destination_roots", "disposition"):
            if not row.get(field):
                raise ValidationFailure(
                    f"migration import group lacks {field}: {row.get('id')}"
                )
        identifier = row["id"]
        if not isinstance(identifier, str) or identifier in identifiers:
            raise ValidationFailure(
                f"migration import group id is invalid or duplicated: {identifier!r}"
            )
        identifiers.add(identifier)
        destinations = row["destination_roots"]
        if (
            not isinstance(destinations, list)
            or not destinations
            or len(destinations) != len(set(destinations))
        ):
            raise ValidationFailure(
                f"migration destination list is invalid: {identifier}"
            )
        for destination in destinations:
            if (
                not isinstance(destination, str)
                or not destination
                or "\\" in destination
                or "\0" in destination
            ):
                raise ValidationFailure(
                    f"migration destination is invalid: {identifier}: {destination!r}"
                )
            relative = PurePosixPath(destination)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValidationFailure(
                    f"migration destination escapes the repository: "
                    f"{identifier}: {destination}"
                )
            candidate = root.joinpath(*relative.parts)
            try:
                mode = candidate.lstat().st_mode
            except OSError as exc:
                raise ValidationFailure(
                    f"migration destination is missing: {identifier}: {destination}"
                ) from exc
            if stat.S_ISLNK(mode) or not (
                stat.S_ISREG(mode) or stat.S_ISDIR(mode)
            ):
                raise ValidationFailure(
                    f"migration destination is not a regular file or directory: "
                    f"{identifier}: {destination}"
                )


def _validate_migration_privacy(manifest: object) -> None:
    if not isinstance(manifest, dict):
        raise ValidationFailure("migration manifest must be an object")
    source = manifest.get("source_repository", {})
    expected_source = {
        "origin": "private predecessor snapshot",
        "publication": "not retained",
        "license": "LGPL-3.0-only",
    }
    if source != expected_source:
        raise ValidationFailure(
            "migration source must omit private predecessor coordinates"
        )
    imports = manifest.get("imports")
    if not isinstance(imports, list):
        raise ValidationFailure("migration import groups are missing")

    serialized_manifest = json.dumps(manifest, sort_keys=True)
    if "docs/deconstruction/" in serialized_manifest:
        raise ValidationFailure(
            "migration manifest exposes a private predecessor path"
        )

    product_groups = [
        row for row in imports if row.get("id") == "workbench-product-definition"
    ]
    if len(product_groups) != 1:
        raise ValidationFailure(
            "migration must contain one workbench-product-definition group"
        )
    product_group = product_groups[0]
    if product_group.get("source") != "private predecessor snapshot (not published)":
        raise ValidationFailure(
            "Workbench product-definition source boundary changed"
        )
    if product_group.get("source_roots") != [
        "unpublished product-definition sources"
    ]:
        raise ValidationFailure(
            "Workbench product-definition source roots expose private details"
        )
    if "source_files" in product_group or "source_commit" in product_group:
        raise ValidationFailure(
            "Workbench product-definition exposes private source coordinates"
        )
    for row in imports:
        if not isinstance(row, dict):
            continue
        source_roots = row.get("source_roots")
        if not isinstance(source_roots, list) or not any(
            isinstance(value, str) and value.startswith("unpublished predecessor ")
            for value in source_roots
        ):
            continue
        if "source_commit" in row or "source_files" in row:
            raise ValidationFailure(
                f"private migration import exposes source coordinates: {row.get('id')}"
            )


def validate_migration() -> None:
    path = ROOT / "MIGRATION-MANIFEST.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    imports = manifest.get("imports")
    _validate_migration_destinations(imports)
    _validate_migration_privacy(manifest)


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    import os
    options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    process = subprocess.Popen(command, cwd=ROOT, env=env, **options)
    try:
        returncode = process.wait()
    except BaseException:
        _terminate_process(process)
        raise
    if returncode:
        raise ValidationFailure(
            f"command failed with exit {returncode}: "
            + " ".join(command)
        )


def validate_product_capabilities() -> None:
    """Validate the public inventory directly from registered product commands."""

    # A clean source environment has no installed Workbench distributions.
    # Resolve registrations from this checkout's native manifests explicitly,
    # just as the owner suite runner does, rather than inheriting host installs.
    from workbench_core.development import enable_source_checkout

    enable_source_checkout(ROOT)
    try:
        catalog = build_product_capability_catalog(ROOT)
        schema = json.loads(
            (ROOT / PRODUCT_CAPABILITY_SCHEMA).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(catalog)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ProductCapabilityCatalogError,
        SchemaError,
        ValidationError,
        ValueError,
    ) as exc:
        raise ValidationFailure(f"invalid product capability catalog: {exc}") from exc


def validate_repository_preflight(
    files: list[Path],
    *,
    authority: bool,
    policy: bool,
) -> None:
    """Run cheap repository checks and any explicitly requested deep proofs."""

    validate_serialized_files(files)
    validate_checked_in_schema_instances()
    try:
        load_build_paths(ROOT)
    except BuildPathError as exc:
        raise ValidationFailure(f"invalid build-path contract: {exc}") from exc
    try:
        load_public_repository(ROOT)
        load_release_units(ROOT)
    except PublicRepositoryError as exc:
        raise ValidationFailure(f"invalid public-repository plan: {exc}") from exc
    validate_file_policy(files)
    validate_product_capabilities()
    validate_profiles()
    validate_python_sources(files)
    if policy:
        validate_links(files)
        validate_migration()
    # The public capability truth is checked above from live registrations.
    # Internal task-ledger and evidence compilers are intentionally not
    # repository-validation inputs, including in authority mode.


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the Workbench repository and active vertical slices."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help=(
            "run every Python suite plus IDE-host integration, binary "
            "compatibility, and repository policy audits"
        ),
    )
    parser.add_argument(
        "--ide",
        action="store_true",
        help="also run native IDE unit, compile, and package checks",
    )
    parser.add_argument(
        "--policy",
        action="store_true",
        help=(
            "also audit documentation links, migration identities, and "
            "documented catalog counts"
        ),
    )
    parser.add_argument(
        "--tier",
        choices=("quick", "source-ci", "canonical"),
        default="quick",
        help=(
            "run cheap preflight and fast developer suites (default), or "
            "run all registered Python suites"
        ),
    )
    parser.add_argument(
        "--suite",
        action="append",
        choices=tuple(SUITES_BY_NAME),
        default=[],
        help=(
            "run one registered Python suite after the selected tier's "
            "repository preflight; may be repeated and intentionally omits "
            "IDE validation"
        ),
    )
    parser.add_argument(
        "--jobs",
        type=_positive_worker_count,
        default=2,
        metavar="N",
        help=(
            "run independent Python suites with up to N workers (default: 2; "
            "exclusive suites still run alone)"
        ),
    )
    parser.add_argument(
        "--list-suites",
        action="store_true",
        help="list registered Python suites and exit",
    )
    parser.add_argument("--result", type=Path, help="write a fresh terminal invocation result to PATH")
    parser.add_argument("--changed-since", metavar="REF", help="select impacted suites from the merge base with REF, including local changes")
    parser.add_argument("--explain-selection", action="store_true", help="print the changed-path selection plan without running validation")
    args = parser.parse_args()

    if args.list_suites:
        print(
            "tier       suite                                      "
            "timeout  mode       authority"
        )
        for suite in PYTHON_TEST_SUITES:
            mode = "exclusive" if suite.exclusive else "parallel"
            print(
                f"{suite.tier:<10} {suite.name:<42} "
                f"{suite.timeout_seconds:>7}s  {mode:<10} {suite.authority}"
            )
        return 0
    if args.full and args.suite:
        parser.error("--full cannot be combined with --suite")
    if args.ide and args.suite:
        parser.error("--ide cannot be combined with --suite")

    if args.explain_selection and not args.changed_since:
        parser.error("--explain-selection requires --changed-since")
    if args.changed_since and (args.suite or args.full or args.ide or args.tier == "canonical"):
        parser.error("--changed-since cannot be combined with --suite, --full, --ide or --tier canonical")
    selection = None
    if args.changed_since:
        selection = select_impacted_suites(changed_paths_since(ROOT, args.changed_since), root=ROOT).to_document()
        if args.explain_selection:
            print(json.dumps(selection, indent=2))
            return 0
        if selection["selection_kind"] == "canonical":
            args.tier = "canonical"
        else:
            args.suite = selection["selected_suites"]
    return execute_validation(args, selection)


def changed_paths_since(root: Path, reference: str) -> list[str]:
    try:
        revision = subprocess.check_output(["git", "rev-parse", "--verify", "--end-of-options", reference + "^{commit}"], cwd=root, text=True, stderr=subprocess.PIPE).strip()
        base = subprocess.check_output(["git", "merge-base", revision, "HEAD"], cwd=root, text=True, stderr=subprocess.PIPE).strip()
        tracked = subprocess.check_output(["git", "diff", "--name-only", "--no-renames", "-z", base, "--"], cwd=root)
        untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root)
        return sorted({name.decode("utf-8") for name in (tracked + untracked).split(b"\0") if name})
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValidationFailure(f"cannot determine changed source paths: {error}") from error


def execute_validation(args, selection=None) -> int:
    tier = "canonical" if args.full else args.tier
    run_ide = args.full or args.ide
    run_policy = args.full or args.policy
    selected_suites = tuple(dict.fromkeys(args.suite))
    phases = ("preflight", "python", "ide") if run_ide else ("preflight", "python")
    try:
        invocation = Invocation(ROOT, args.result, phases, selection)
    except OSError as error:
        raise ValidationFailure(f"cannot create fresh invocation result: {error}") from error
    with invocation:
        with invocation.phase("preflight"):
            files = repository_files()
            try:
                source_fingerprint = fingerprint_paths(ROOT, files)
            except OrchestrationFailure as exc:
                raise ValidationFailure(f"cannot bind validation source tree: {exc}") from exc
            invocation.document["source_fingerprint"] = source_fingerprint
            invocation.write()
            validate_repository_preflight(
                files,
                authority=tier in {"source-ci", "canonical"},
                policy=run_policy,
            )
            try:
                preflight_source_fingerprint = fingerprint_paths(ROOT, repository_files())
            except OrchestrationFailure as exc:
                raise ValidationFailure(
                    f"cannot recheck validation source tree after preflight: {exc}"
                ) from exc
            if preflight_source_fingerprint != source_fingerprint:
                raise ValidationFailure(
                    "validation source fingerprint drifted during repository preflight: "
                    f"expected {source_fingerprint}, "
                    f"observed {preflight_source_fingerprint}"
                )
        with invocation.phase("python"):
            try:
                run_paths = run_python_suites(
                    tier=tier,
                    selected=selected_suites,
                    jobs=args.jobs,
                    source_fingerprint=source_fingerprint,
                    repository_files=repository_files,
                    run_id=invocation.run_id,
                    selection_plan=selection,
                )
            except SuiteExecutionFailure as exc:
                raise ValidationFailure(str(exc)) from exc
        if run_ide:
            with invocation.phase("ide"):
                ide_command = [sys.executable, "validation/validate_ide.py"]
                if args.full:
                    ide_command.append("--full")
                run(ide_command)
                try:
                    final_source_fingerprint = fingerprint_paths(ROOT, repository_files())
                except OrchestrationFailure as exc:
                    raise ValidationFailure(
                        f"cannot recheck validation source tree after IDE checks: {exc}"
                    ) from exc
                if final_source_fingerprint != source_fingerprint:
                    raise ValidationFailure(
                        "validation source fingerprint drifted during IDE checks: "
                        f"expected {source_fingerprint}, observed {final_source_fingerprint}"
                    )

    if args.full:
        label = "Workbench full validation"
    elif args.suite:
        label = "Workbench focused validation (not canonical)"
    elif tier == "quick":
        label = "Workbench developer validation"
    elif tier == "source-ci":
        label = "Workbench public source validation (native fixtures not run)"
    else:
        label = "Workbench Python validation"
    print(
        f"{label} passed; run artifacts: {run_paths.root.relative_to(ROOT)}.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    def interrupt_validation(_signal, _frame):
        raise KeyboardInterrupt("validation received a termination signal")

    signal.signal(signal.SIGTERM, interrupt_validation)
    try:
        raise SystemExit(main())
    except (OrchestrationFailure, ValidationFailure) as exc:
        print(f"VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
