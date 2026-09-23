"""Truthful command metadata for the Workbench terminal console.

The catalog is deliberately an argv composer.  It does not reimplement any
downstream validator, planner, policy, or lifecycle decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
from pathlib import Path
import re
import shlex
import sys
from typing import Any, Iterable, Mapping, Sequence

from workbench_api.console_policy import example_choices, experiment_choices, profile_choices


FORMAT_VERSION = "workbench-live-console-command-catalog-v2"
ACTION_BINDING_FORMAT = "workbench-live-console-action-binding-v2"
_PLACEHOLDER_RE = re.compile(r"^\{options:(\d+)\}$")


class CatalogError(ValueError):
    """A catalog selection or argv composition is invalid."""


@dataclass(frozen=True)
class FieldSpec:
    """One exact CLI input exposed by a console wizard."""

    key: str
    label: str
    help: str
    flags: tuple[str, ...] = ()
    kind: str = "text"
    required: bool = False
    positional: bool = False
    choices: tuple[str, ...] = ()
    default: Any = None
    nargs: str | int = "one"
    repeat: bool = False
    placement: int = 0
    mutex_group: str | None = None
    required_group: bool = False
    console_managed: bool = False
    metavar: str | None = None
    sensitive: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.key):
            raise CatalogError(f"invalid field key: {self.key!r}")
        if self.kind not in {"text", "path", "integer", "boolean", "choice", "json"}:
            raise CatalogError(f"unsupported field kind for {self.key}: {self.kind}")
        if self.positional == bool(self.flags):
            raise CatalogError(
                f"field {self.key} must have either positional=True or option flags"
            )
        # Optional profile extensions may supply no choices. Catalog assembly
        # removes an empty optional input or marks its required action unavailable.
        if self.placement < 0:
            raise CatalogError(f"field {self.key} has a negative placement")
        if self.required_group and not self.mutex_group:
            raise CatalogError(
                f"field {self.key} declares a required group without a mutex group"
            )

    @property
    def primary_flag(self) -> str:
        return self.flags[0] if self.flags else self.key

    def public_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "help": self.help,
            "flags": list(self.flags),
            "kind": self.kind,
            "required": self.required,
            "positional": self.positional,
            "choices": list(self.choices),
            "nargs": self.nargs,
            "repeat": self.repeat,
            "placement": self.placement,
            "sensitive": self.sensitive,
            "required_group": self.required_group,
            "console_managed": self.console_managed,
        }
        if self.default is not None:
            value["default"] = self.default
        if self.mutex_group:
            value["mutex_group"] = self.mutex_group
        if self.metavar:
            value["metavar"] = self.metavar
        return value


@dataclass(frozen=True)
class CommandSpec:
    """A leaf command and its console behavior."""

    command_id: str
    suite_id: str
    title: str
    summary: str
    authority: str
    risk: str
    preview: str
    argv_template: tuple[str, ...] = ()
    fields: tuple[FieldSpec, ...] = ()
    documentation: str | None = None
    document: str | None = None
    availability: str = "available"
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9.-]*", self.command_id):
            raise CatalogError(f"invalid command id: {self.command_id!r}")
        if self.risk not in {"read-only", "writes-output", "mutating", "destructive"}:
            raise CatalogError(f"invalid risk for {self.command_id}: {self.risk}")
        if self.preview not in {"none", "append-show", "plan-then-apply", "inert-only", "confirm-and-show"}:
            raise CatalogError(f"invalid preview strategy for {self.command_id}")
        if self.availability not in {"available", "unavailable", "experimental"}:
            raise CatalogError(f"invalid availability for {self.command_id}")
        if bool(self.argv_template) == bool(self.document):
            raise CatalogError(
                f"command {self.command_id} must be executable or a document"
            )
        keys = [item.key for item in self.fields]
        if len(keys) != len(set(keys)):
            raise CatalogError(f"duplicate field key in {self.command_id}")
        placements = {
            int(match.group(1))
            for token in self.argv_template
            if (match := _PLACEHOLDER_RE.fullmatch(token))
        }
        field_placements = {item.placement for item in self.fields}
        if field_placements - placements:
            raise CatalogError(
                f"command {self.command_id} has fields without argv placement"
            )

    @property
    def executable(self) -> bool:
        return bool(self.argv_template)

    @property
    def needs_execute_consent(self) -> bool:
        return self.risk in {"writes-output", "mutating", "destructive"}

    def field(self, key: str) -> FieldSpec:
        try:
            return next(item for item in self.fields if item.key == key)
        except StopIteration as exc:
            raise CatalogError(f"unknown option for {self.command_id}: {key}") from exc

    def public_dict(self, *, root: Path) -> dict[str, Any]:
        value = self._public_material(root=root)
        value["action_digest"] = self.action_digest(root=root)
        return value

    def action_digest(self, *, root: Path) -> str:
        """Bind the exact catalog action semantics for cross-process clients."""

        material = {
            "format_version": ACTION_BINDING_FORMAT,
            "action": self._public_material(root=root),
            # Runtime paths are execution inputs, not action semantics.  Keep
            # the declared placeholders in the content identity so the same
            # packaged action has one digest in a checkout, wheel, or WSL
            # launch while build_argv still resolves the exact live paths.
            "argv_template": list(self.argv_template),
        }
        return "sha256:" + sha256(_canonical_bytes(material)).hexdigest()

    def _public_material(self, *, root: Path) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "suite_id": self.suite_id,
            "title": self.title,
            "summary": self.summary,
            "authority": self.authority,
            "risk": self.risk,
            "preview": self.preview,
            "availability": self.availability,
            "documentation": self.documentation,
            "document": self.document,
            "options": [item.public_dict() for item in self.fields],
            "limitations": list(self.limitations),
            "command_preview": self.render_template(root),
        }

    def render_template(self, root: Path) -> str:
        tokens: list[str] = []
        for token in self.argv_template:
            match = _PLACEHOLDER_RE.fullmatch(token)
            if match:
                placement = int(match.group(1))
                tokens.extend(
                    f"<{field.key}>"
                    for field in self.fields
                    if field.placement == placement
                )
            else:
                tokens.append(
                    token.replace("{python}", "python")
                    .replace("{root}/", "")
                    .replace("{root}", ".")
                )
        if self.document:
            return f"open {self.document}"
        return shlex.join(tokens)

    def build_argv(
        self,
        values: Mapping[str, Any],
        *,
        root: Path,
        execute: bool,
    ) -> tuple[list[str], str]:
        """Compose an argv and return its intent (execute, preview, or inert)."""

        if not self.executable:
            raise CatalogError(f"{self.command_id} is a document, not a process")
        if self.availability == "unavailable":
            raise CatalogError(f"{self.command_id} is unavailable")
        unknown = sorted(set(values) - {item.key for item in self.fields})
        if unknown:
            raise CatalogError(
                f"unknown option(s) for {self.command_id}: {', '.join(unknown)}"
            )
        prepared = dict(values)
        self._validate_values(prepared)

        intent = "execute"
        if self.preview == "append-show":
            if execute:
                prepared.pop("show", None)
            else:
                # Output encoding is orthogonal to mutation intent.  A JSON
                # preview must still carry the owner's explicit inert guard.
                prepared["show"] = True
                intent = "preview"
        elif self.preview == "plan-then-apply":
            if execute:
                prepared["apply"] = True
                prepared.pop("show", None)
                prepared.pop("json", None)
            else:
                prepared.pop("apply", None)
                intent = "preview"
        elif self.preview == "confirm-and-show":
            if execute:
                if not prepared.get("confirm"):
                    raise CatalogError(
                        f"{self.command_id} execution requires the exact confirm value"
                    )
                prepared.pop("show", None)
                prepared.pop("json", None)
            else:
                prepared["show"] = True
                intent = "preview"
        elif self.preview == "inert-only" and not execute:
            intent = "inert"

        rendered: dict[int, list[str]] = {}
        for option in self.fields:
            if option.key not in prepared:
                continue
            value = prepared[option.key]
            if value is None or value == "":
                continue
            rendered.setdefault(option.placement, []).extend(
                _render_field(option, value)
            )
        argv: list[str] = []
        for token in self.argv_template:
            match = _PLACEHOLDER_RE.fullmatch(token)
            if match:
                argv.extend(rendered.get(int(match.group(1)), []))
            else:
                argv.append(_resolve_token(token, root))
        return argv, intent

    def _validate_values(self, values: Mapping[str, Any]) -> None:
        selected_groups: dict[str, str] = {}
        required_groups = {
            option.mutex_group
            for option in self.fields
            if option.required_group and option.mutex_group
        }
        for option in self.fields:
            present = (
                option.key in values
                and values[option.key] is not None
                and values[option.key] != ""
                and values[option.key] != []
                and not (option.kind == "boolean" and values[option.key] is False)
            )
            if option.required and not present:
                raise CatalogError(
                    f"{self.command_id} requires {option.primary_flag}"
                )
            if not present:
                continue
            if option.mutex_group:
                previous = selected_groups.setdefault(option.mutex_group, option.key)
                if previous != option.key:
                    raise CatalogError(
                        f"{previous} and {option.key} are mutually exclusive"
                    )
            _coerce_field(option, values[option.key])
        for group in sorted(required_groups):
            if group in selected_groups:
                continue
            choices = [
                option.primary_flag
                for option in self.fields
                if option.mutex_group == group
            ]
            raise CatalogError(
                f"{self.command_id} requires exactly one of {', '.join(choices)}"
            )


@dataclass(frozen=True)
class SuiteSpec:
    suite_id: str
    title: str
    summary: str
    authority: str
    availability: str = "available"

    def public_dict(self, command_count: int) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "title": self.title,
            "summary": self.summary,
            "authority": self.authority,
            "availability": self.availability,
            "command_count": command_count,
        }


@dataclass
class Catalog:
    root: Path
    suites: tuple[SuiteSpec, ...]
    commands: tuple[CommandSpec, ...]
    _by_id: dict[str, CommandSpec] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        suite_ids_in_order = [item.suite_id for item in self.suites]
        if len(set(suite_ids_in_order)) != len(suite_ids_in_order):
            raise CatalogError("duplicate suite IDs")
        self._by_id = {item.command_id: item for item in self.commands}
        if len(self._by_id) != len(self.commands):
            raise CatalogError("duplicate command IDs")
        suite_ids = {item.suite_id for item in self.suites}
        missing = {item.suite_id for item in self.commands} - suite_ids
        if missing:
            raise CatalogError(f"commands reference unknown suites: {sorted(missing)}")

    def command(self, command_id: str) -> CommandSpec:
        try:
            return self._by_id[command_id]
        except KeyError as exc:
            suggestions = self.search(command_id)[:5]
            suffix = (
                "; closest: " + ", ".join(item.command_id for item in suggestions)
                if suggestions
                else ""
            )
            raise CatalogError(f"unknown console command {command_id!r}{suffix}") from exc

    def for_suite(self, suite_id: str) -> list[CommandSpec]:
        return [item for item in self.commands if item.suite_id == suite_id]

    def search(self, query: str, *, suite_id: str | None = None) -> list[CommandSpec]:
        words = [word.casefold() for word in query.split() if word]
        pool = self.commands if suite_id is None else tuple(self.for_suite(suite_id))
        if not words:
            return list(pool)

        def score(command: CommandSpec) -> tuple[int, str]:
            identity = f"{command.command_id} {command.title}".casefold()
            haystack = f"{identity} {command.summary} {command.authority}".casefold()
            total = 0
            for word in words:
                if word in identity:
                    total += 20
                elif word in haystack:
                    total += 8
                else:
                    # Ordered-subsequence matching keeps the palette forgiving.
                    iterator = iter(haystack)
                    if all(character in iterator for character in word):
                        total += 1
                    else:
                        return (-1, command.command_id)
            return (total, command.command_id)

        ranked = [(score(item), item) for item in pool]
        return [
            item
            for (value, _), item in sorted(ranked, key=lambda row: (-row[0][0], row[0][1]))
            if value >= 0
        ]

    def public_dict(self) -> dict[str, Any]:
        material = self._public_material()
        return {
            "format_version": FORMAT_VERSION,
            "catalog_digest": "sha256:" + sha256(
                _canonical_bytes(material)
            ).hexdigest(),
            "suites": material["suites"],
            "commands": material["commands"],
        }

    @property
    def catalog_digest(self) -> str:
        material = self._public_material()
        return "sha256:" + sha256(_canonical_bytes(material)).hexdigest()

    def _public_material(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "suites": [
                suite.public_dict(len(self.for_suite(suite.suite_id)))
                for suite in self.suites
            ],
            "commands": [item.public_dict(root=self.root) for item in self.commands],
        }


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def parse_assignments(spec: CommandSpec, definitions: Sequence[str]) -> dict[str, Any]:
    """Parse repeatable ``KEY=VALUE`` / ``KEY:=JSON`` console assignments."""

    result: dict[str, Any] = {}
    for definition in definitions:
        if ":=" in definition:
            key, encoded = definition.split(":=", 1)
            try:
                value = json.loads(encoded)
            except json.JSONDecodeError as exc:
                raise CatalogError(f"invalid JSON for {key}: {exc.msg}") from exc
        elif "=" in definition:
            key, value = definition.split("=", 1)
        else:
            raise CatalogError(f"assignment must be KEY=VALUE: {definition!r}")
        option = spec.field(key)
        value = _coerce_field(option, value)
        if option.repeat:
            result.setdefault(key, [])
            if isinstance(option.nargs, int):
                result[key].append(value)
            elif isinstance(value, list):
                result[key].extend(value)
            else:
                result[key].append(value)
        elif key in result:
            raise CatalogError(f"option {key} was supplied more than once")
        else:
            result[key] = value
    return result


def redact_argv(argv: Sequence[str], fields: Sequence[FieldSpec]) -> list[str]:
    """Redact catalog-declared sensitive values before session persistence."""

    sensitive_flags = {
        flag for field in fields if field.sensitive for flag in field.flags
    }
    result: list[str] = []
    redact_next = False
    for token in argv:
        if redact_next:
            result.append("<redacted>")
            redact_next = False
        elif token in sensitive_flags:
            result.append(token)
            redact_next = True
        elif any(token.startswith(flag + "=") for flag in sensitive_flags):
            result.append(token.split("=", 1)[0] + "=<redacted>")
        else:
            result.append(token)
    return result


def _coerce_field(option: FieldSpec, value: Any) -> Any:
    if (
        option.repeat
        and isinstance(option.nargs, int)
        and isinstance(value, (list, tuple))
        and value
        and all(isinstance(group, (list, tuple)) for group in value)
    ):
        single = replace(option, repeat=False)
        return [_coerce_field(single, group) for group in value]
    repeat_scalar_values = (
        option.repeat
        and option.nargs in {"one", "optional"}
        and isinstance(value, (list, tuple))
    )
    is_many = option.nargs not in {"one", "optional"} or repeat_scalar_values
    if is_many:
        if isinstance(value, (list, tuple)):
            raw_values = list(value)
        else:
            raw_values = [part for part in str(value).split(",") if part != ""]
    else:
        raw_values = [value]

    def scalar(raw: Any) -> Any:
        if option.kind == "boolean":
            if isinstance(raw, bool):
                return raw
            if str(raw).casefold() in {"1", "true", "yes", "on", "y"}:
                return True
            if str(raw).casefold() in {"0", "false", "no", "off", "n"}:
                return False
            raise CatalogError(f"{option.key} expects true or false")
        if option.kind == "integer":
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise CatalogError(f"{option.key} expects an integer") from exc
        if option.kind == "json":
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise CatalogError(
                        f"{option.key} expects JSON: {exc.msg}"
                    ) from exc
            return raw
        return raw

    converted = [scalar(raw) for raw in raw_values]
    coerced: Any = converted if is_many else converted[0]
    if isinstance(option.nargs, int):
        if not isinstance(coerced, list) or len(coerced) != option.nargs:
            raise CatalogError(f"{option.key} expects exactly {option.nargs} values")
    if option.nargs == "one_or_more" and not coerced:
        raise CatalogError(f"{option.key} expects one or more values")
    if option.kind == "choice":
        candidates = coerced if isinstance(coerced, list) else [coerced]
        invalid = [str(item) for item in candidates if str(item) not in option.choices]
        if invalid:
            raise CatalogError(
                f"{option.key} expects one of {', '.join(option.choices)}"
            )
    return coerced


def _render_field(option: FieldSpec, raw_value: Any) -> list[str]:
    value = _coerce_field(option, raw_value)
    if option.kind == "boolean":
        return [option.primary_flag] if value else []
    if option.repeat and isinstance(option.nargs, int):
        groups = (
            value
            if value and isinstance(value[0], list)
            else [value]
        )
        return [
            part
            for group in groups
            for part in (
                option.primary_flag,
                *(str(item) for item in group),
            )
        ]
    values = value if isinstance(value, list) else [value]
    strings = [
        json.dumps(item, separators=(",", ":"), sort_keys=True)
        if option.kind == "json" and not isinstance(item, str)
        else str(item)
        for item in values
    ]
    if option.positional:
        return strings
    if option.repeat:
        return [part for item in strings for part in (option.primary_flag, item)]
    return [option.primary_flag, *strings]


def _resolve_token(token: str, root: Path) -> str:
    return (
        token.replace("{python}", sys.executable)
        .replace("{root}", str(root))
    )


def _f(
    key: str,
    help: str,
    *,
    flags: Sequence[str] = (),
    label: str | None = None,
    kind: str = "text",
    required: bool = False,
    positional: bool = False,
    choices: Sequence[str] = (),
    default: Any = None,
    nargs: str | int = "one",
    repeat: bool = False,
    placement: int = 0,
    mutex_group: str | None = None,
    required_group: bool = False,
    console_managed: bool = False,
    metavar: str | None = None,
    sensitive: bool = False,
) -> FieldSpec:
    return FieldSpec(
        key=key,
        label=label or key.replace("_", " ").title(),
        help=help,
        flags=tuple(flags),
        kind=kind,
        required=required,
        positional=positional,
        choices=tuple(choices),
        default=default,
        nargs=nargs,
        repeat=repeat,
        placement=placement,
        mutex_group=mutex_group,
        required_group=required_group,
        console_managed=console_managed,
        metavar=metavar,
        sensitive=sensitive,
    )


def _workbench_template(*tokens: str) -> tuple[str, ...]:
    return ("{python}", "{root}/tools/workbench.py", *tokens, "{options:0}")


def _public_commands(root: Path) -> list[CommandSpec]:
    doctor_fields = (
        _f("workspace", "Workspace or subproject to inspect.", positional=True, default="."),
        _f("capability", "Capability to assess.", flags=("--capability",), kind="choice", choices=("workspace-context", "worldgen-dev")),
        _f("profile", "Explicit pack profile.", flags=("--profile",), mutex_group="profile-source"),
        _f("profile_file", "Explicit pack profile file.", flags=("--profile-file",), kind="path", mutex_group="profile-source"),
        _f("runtime_template", "Runtime template override.", flags=("--runtime-template",), kind="path"),
        _f("strata_root", "Strata checkout override.", flags=("--strata-root",), kind="path"),
        _f("java_cmd", "Java command override.", flags=("--java-cmd",)),
        _f("gradle_cmd", "Gradle command override.", flags=("--gradle-cmd",)),
        _f("json", "Emit the complete JSON report.", flags=("--json",), kind="boolean"),
        _f("output", "Write the report atomically to a fresh path.", flags=("--output",), kind="path"),
        _f("strict", "Return attention as exit 1.", flags=("--strict",), kind="boolean"),
    )
    commands = [
        CommandSpec(
            "environment.status",
            "shell",
            "Inspect Workbench environment",
            "Report execution provenance, Pixi identities, source environment footprint, and non-mutating repair and cleanup guidance.",
            "Workbench Shell local execution observation; release and profile owners remain authoritative",
            "read-only",
            "none",
            _workbench_template("environment", "status"),
            (
                _f(
                    "json",
                    "Emit the complete read-only environment status record.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            "packaging/pixi/README.md",
            limitations=(
                "Status does not install, repair, clean, or delete an environment.",
                "Java remains separate and profile-owned and is not inspected by this command.",
                "Installer receipts are observed provenance, not a second release approval path.",
            ),
        ),
        CommandSpec(
            "new.cleanroom-mod-preview", "new", "Preview Cleanroom mod project",
            "Compile one profile-owned 19-file Cleanroom mod plan for an absent, empty, or clean unborn Git target without changing it.",
            "Cleanroom platform construction owner over Blueprints fresh-project V2",
            "writes-output", "none", _workbench_template("new", "cleanroom-mod", "preview"),
            (
                _f("target", "Absent, empty, or clean unborn Git destination.", positional=True, kind="path", required=True),
                _f("output_mode", "Preview instructions or authorize later direct apply.", flags=("--output-mode",), kind="choice", choices=("instructions", "direct-apply"), default="instructions"),
                _f("sequence", "Owner request sequence.", flags=("--sequence",), kind="integer", default=0),
                _f("output", "Fresh reviewed plan output.", flags=("--output",), kind="path"),
                _f("json", "Emit the exact owner result.", flags=("--json",), kind="boolean"),
            ), "docs/product/CLEANROOM-PLATFORM.md", availability="experimental",
            limitations=("Preview observes the target and source lock but does not create either.",),
        ),
        CommandSpec(
            "new.cleanroom-mod-apply", "new", "Apply Cleanroom mod project",
            "Apply one exact direct-apply plan through Blueprints bootstrap, transaction, and recovery custody.",
            "Cleanroom platform construction owner over Blueprints fresh-project V2",
            "mutating", "plan-then-apply", _workbench_template("new", "cleanroom-mod", "apply"),
            (
                _f("plan", "Exact reviewed construction plan.", positional=True, kind="path", required=True),
                _f("state_root", "External private construction state root.", flags=("--state-root",), kind="path"),
                _f("consent_plan_id", "Exact reviewed plan identity.", flags=("--consent-plan-id",), required=True),
                _f("json", "Emit the exact owner result.", flags=("--json",), kind="boolean"),
            ), "docs/product/CLEANROOM-PLATFORM.md", availability="experimental",
        ),
        CommandSpec(
            "new.cleanroom-mod-recover", "new", "Recover Cleanroom mod project",
            "Recover an interrupted profile-owned construction using its exact plan and retained Blueprints journals.",
            "Cleanroom platform construction owner over Blueprints fresh-project V2",
            "mutating", "inert-only", _workbench_template("new", "cleanroom-mod", "recover"),
            (
                _f("plan", "Exact reviewed construction plan.", positional=True, kind="path", required=True),
                _f("state_root", "External private construction state root.", flags=("--state-root",), kind="path"),
                _f("json", "Emit the exact owner result.", flags=("--json",), kind="boolean"),
            ), "docs/product/CLEANROOM-PLATFORM.md", availability="experimental",
        ),
        CommandSpec(
            "workspace.open",
            "shell",
            "Open workspace Home",
            "Recognize one workspace and project a short ordered list of executable developer jobs and honestly blocked gaps.",
            "Workbench Shell projection over Project Intelligence and applicable profile owners",
            "read-only",
            "none",
            _workbench_template("open"),
            (
                _f(
                    "workspace",
                    "Workspace or path inside it.",
                    positional=True,
                    kind="path",
                    default=".",
                ),
                _f(
                    "json",
                    "Emit the complete workspace Home projection.",
                    flags=("--json",),
                    kind="boolean",
                ),
                _f(
                    "session",
                    "Exact Work Session selector to compose into Home.",
                    flags=("--session",),
                ),
                _f(
                    "state_root",
                    "Explicit private product-spine state root.",
                    flags=("--state-root",),
                    kind="path",
                ),
            ),
            "docs/guides/getting-started.md",
            availability="experimental",
            limitations=(
                "Home is read-only and does not execute a recommended job.",
                "Home exposes only owner-validated actions for the exact current context.",
            ),
        ),
        CommandSpec(
            "workspace.adopt",
            "shell",
            "Adopt workspace Home",
            "Publish one ignored-local Home binding and durable Work Session, or retain an exact retry after an interrupted publication.",
            "Workbench Shell navigation state over Project Intelligence and exact owner ports",
            "writes-output",
            "inert-only",
            _workbench_template("adopt"),
            (
                _f(
                    "workspace",
                    "Exact workspace root to adopt.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "state_root",
                    "Exact private product-spine state root receiving the binding.",
                    flags=("--state-root",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "json",
                    "Emit the complete adopted Home projection.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            "docs/guides/getting-started.md",
            availability="experimental",
            limitations=(
                "Adoption writes only private ignored-local navigation state and never authorizes an owner action or release claim.",
            ),
        ),
        CommandSpec(
            "project.qualify",
            "shell",
            "Qualify an existing pack",
            "Check one existing checkout against an explicit pack profile and, after exact-plan consent, save only a private external profile binding.",
            "Project Intelligence inspection composed by Workbench Shell over explicit pack and platform profile authority",
            "writes-output",
            "inert-only",
            _workbench_template("project", "qualify"),
            (
                _f(
                    "workspace",
                    "Existing pack checkout to inspect.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "profile",
                    "Explicit supported pack profile; selection is never inferred.",
                    flags=("--profile",),
                    kind="choice",
                    choices=profile_choices("qualification"),
                    required=True,
                ),
                _f(
                    "status",
                    "Read current conformance and saved binding state.",
                    flags=("--status",),
                    kind="boolean",
                    mutex_group="qualification-phase",
                ),
                _f(
                    "plan",
                    "Emit the exact reviewed qualification plan without saving it.",
                    flags=("--plan",),
                    kind="boolean",
                    mutex_group="qualification-phase",
                ),
                _f(
                    "apply",
                    "Exact plan ID consented by the developer.",
                    flags=("--apply",),
                    mutex_group="qualification-phase",
                ),
                _f(
                    "state_root",
                    "External private Workbench state root for the binding.",
                    flags=("--state-root",),
                    kind="path",
                ),
                _f(
                    "json",
                    "Emit the complete machine-readable contract.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            "modules/workbench-shell/contracts/workbench-project-qualification-v1.md",
            availability="experimental",
            limitations=(
                "Qualification never adds or changes a file in the selected checkout.",
                "It records pack-family fit only; build, runtime, publication, support, and release qualification remain separate.",
                "The current vertical slice supports only the explicitly selected Supersymmetry profile.",
            ),
        ),
        CommandSpec(
            "diagnose.latest",
            "shell",
            "Diagnose retained work",
            "Resolve the newest retained live-console owner through exact owner custody, preferring Work Session lineage when present, and project observed failures, unknowns, raw ranges, and typed next experiments.",
            "Workbench Shell projection over Work Session or D01 live-console owner custody",
            "read-only",
            "none",
            _workbench_template("diagnose"),
            (
                _f("target", "Exact Work Session selector, or live:SESSION_ID for a retained D01 stage owner.", positional=True, default="latest"),
                _f("state_root", "Explicit private product-spine state root.", flags=("--state-root",), kind="path"),
                _f("json", "Emit the complete diagnosis record.", flags=("--json",), kind="boolean"),
            ),
            "docs/product/DEVELOPER-EXPERIENCE.md",
            availability="experimental",
            limitations=(
                "Generic diagnosis reports observations and unknowns; it does not invent a causal root.",
                "A direct D01 stage target is accepted only through its exact verified live-console owner record.",
            ),
        ),
        CommandSpec(
            "sentinel.mixin-diagnose",
            "sentinel",
            "Check Cleanroom Mixin archives",
            "Explain profile-owned Cleanroom Mixin findings for exact JAR or ZIP bytes in plain English without loading archive code.",
            "Sentinel presentation over Cleanroom profile policy and Project Intelligence facts",
            "read-only",
            "none",
            _workbench_template("diagnose", "mixins"),
            (
                _f("artifacts", "One or more exact JAR/ZIP files.", positional=True, kind="path", required=True, nargs="one_or_more"),
                _f("policy", "Exact Cleanroom Mixin policy JSON; defaults to the policy shipped with Workbench.", flags=("--policy",), kind="path"),
                _f("json", "Emit the complete profile-owned Mixin Doctor report.", flags=("--json",), kind="boolean"),
                _f("strict", "Return exit 1 when the owner report requires review or rejects an archive.", flags=("--strict",), kind="boolean"),
            ),
            "modules/sentinel/contracts/sentinel-mixin-diagnosis-v1.md",
            availability="experimental",
            limitations=(
                "The flow presents the existing profile decision and never adds a finding or changes a disposition.",
                "The owner report is a static archive scan, not runtime application, assembled-game compatibility, or release evidence.",
            ),
        ),
        CommandSpec(
            "relay.locate",
            "relay",
            "Locate a retained runtime identity",
            "Carry one exact identity from an explicit Atlas, Crucible, or Explorer input to an owner-backed source or evidence location.",
            "Relay transport over Exact Runtime Explorer and its owning Atlas, Crucible, and source records",
            "read-only",
            "none",
            _workbench_template("relay", "locate"),
            (
                _f("identity", "Exact typed Explorer identity, such as machine:example:press.", positional=True, required=True),
                _f("identity_kind", "Exact identity kind when the positional value has no Explorer shorthand.", flags=("--identity-kind",)),
                _f("explorer_result", "Complete retained Exact Runtime Explorer V1 JSON result.", flags=("--explorer-result",), kind="path", mutex_group="relay-input", required_group=True),
                _f("receipt", "Exact Atlas or Crucible receipt supported by Explorer.", flags=("--receipt",), kind="path", mutex_group="relay-input", required_group=True),
                _f("runtime_db", "Exact immutable Atlas runtime-graph query database.", flags=("--runtime-db",), kind="path", mutex_group="relay-input", required_group=True),
                _f("profile", "Exact profile filter for an Atlas runtime database.", flags=("--profile",)),
                _f("side", "Exact physical-side filter for an Atlas runtime database.", flags=("--side",)),
                _f("json", "Emit the complete Relay result and preserved owner records.", flags=("--json",), kind="boolean"),
            ),
            "modules/relay/contracts/relay-location-v1.md",
            availability="experimental",
            limitations=(
                "Relay reports unresolved unless the identity is unique, runtime-backed, owner-bound, untruncated, and has a retained locator.",
                "Relay transports owner records verbatim and creates no Atlas fact, runtime claim, profile decision, or source-byte equivalence claim.",
            ),
        ),
        CommandSpec(
            "diagnose.capsule-create",
            "shell",
            "Create reviewed reproduction capsule",
            "Create a deterministic bounded capsule from one retained diagnosis after explicit privacy review and selection of a typed replay action.",
            "Workbench Shell capsule custody over retained owner projections",
            "writes-output",
            "inert-only",
            _workbench_template("diagnose", "reproduce", "create"),
            (
                _f("target", "Exact Work Session selector, or live:SESSION_ID for a retained D01 stage owner.", positional=True, default="latest"),
                _f("state_root", "Explicit private product-spine state root.", flags=("--state-root",), kind="path"),
                _f("output", "Fresh .wb-repro output.", flags=("--output",), kind="path", required=True),
                _f("action_id", "Typed replay action ID.", flags=("--action-id",), required=True),
                _f("arguments_json", "Reviewed replay arguments as a JSON object.", flags=("--arguments-json",), default="{}", sensitive=True),
                _f("mutation", "Replay mutation boundary.", flags=("--mutation",), kind="choice", choices=("read-only", "isolated-target-only"), required=True),
                _f("approve_privacy", "Confirm the displayed default privacy exclusions.", flags=("--approve-privacy",), kind="boolean", required=True),
                _f("json", "Emit the complete capsule result.", flags=("--json",), kind="boolean"),
            ),
            "docs/product/DEVELOPER-EXPERIENCE.md",
            availability="experimental",
            limitations=(
                "Capsules contain reviewed projections and descriptors, never protected runtime binaries by default.",
                "An action descriptor does not authorize replay; a separately installed typed executor must own it.",
            ),
        ),
        *(
            CommandSpec(
                f"diagnose.capsule-{action}",
                "shell",
                f"{action.title()} reproduction capsule",
                "Validate exact deterministic capsule membership and identities without hydration, execution, or network access.",
                "Workbench Shell read-only capsule verifier",
                "read-only",
                "none",
                _workbench_template("diagnose", "reproduce", action),
                (
                    _f("capsule", "Exact .wb-repro capsule.", positional=True, kind="path", required=True),
                    _f("json", "Emit the complete inspection result.", flags=("--json",), kind="boolean"),
                ),
                "docs/product/DEVELOPER-EXPERIENCE.md",
                availability="experimental",
            )
            for action in ("inspect", "verify")
        ),
        CommandSpec(
            "diagnose.capsule-run",
            "shell",
            "Replay reproduction capsule",
            "Replay one verified typed capsule action through the installed Cleanroom Dev Loop owner; other action IDs remain unavailable.",
            "Workbench Shell dispatch to the installed Cleanroom Dev Loop owner",
            "mutating",
            "inert-only",
            _workbench_template("diagnose", "reproduce", "run"),
            (
                _f("capsule", "Exact .wb-repro capsule.", positional=True, kind="path", required=True),
                _f("state_root", "Fresh private replay state root.", flags=("--state-root",), kind="path"),
                _f("gradle_cmd", "Exact installed Gradle executable for dev.fixture-run.", flags=("--gradle-cmd",), kind="path"),
                _f("java_home", "Exact installed Java 25 home for dev.fixture-run.", flags=("--java-home",), kind="path"),
                _f("json", "Emit the complete replay result.", flags=("--json",), kind="boolean"),
            ),
            "docs/product/DEVELOPER-EXPERIENCE.md",
            availability="experimental",
            limitations=(
                "Only the closed dev.fixture-run action currently has an installed executor; arbitrary archive commands are never executed.",
                "Replay always creates a fresh isolated target and requires independently supplied toolchain custody.",
            ),
        ),
        CommandSpec(
            "dev.run",
            "dev",
            "Run a Supersymmetry constituent mod",
            "Build and retain one exact candidate when needed, then run that same candidate through a fresh selected Cleanroom client, dedicated-server, or both-side attempt without manual phase handoffs.",
            "Workbench Shell composition over the project-owned Gradle build, canonical Packwiz materializers, and the existing client and dedicated-server runtime owners",
            "mutating",
            "inert-only",
            _workbench_template("dev", "run"),
            (
                _f("run", "Reopen one exact retained SUSY candidate for a fresh physical attempt without rebuilding it.", flags=("--run",), mutex_group="developer-input", required_group=True),
                _f("project", "Constituent mod checkout for a fresh run; the CLI defaults to the current directory.", flags=("--project",), kind="path"),
                _f("pack", "Exact Supersymmetry Packwiz checkout for a fresh run.", flags=("--pack",), kind="path", mutex_group="developer-input", required_group=True),
                _f("pack_mod", "Exact mods/*.pw.toml basename when fresh-run identity matching is ambiguous.", flags=("--pack-mod",)),
                _f("java_home", "Explicit fresh-build JDK; otherwise use a matching managed or Gradle JDK.", flags=("--java-home",), kind="path"),
                _f("timeout", "Fresh managed-build timeout in seconds; the CLI defaults to 1200.", flags=("--timeout",), kind="integer"),
                _f("side", "Runtime side: auto derives the applicable side set from the retained exact Packwiz metadata; otherwise choose client, server, or both explicitly.", flags=("--side",), kind="choice", choices=("auto", "client", "server", "both"), default="auto"),
                _f("launcher", "Client launcher family; the CLI defaults to Prism.", flags=("--launcher",), kind="choice", choices=("prism", "multimc")),
                _f("launcher_executable", "Explicit Prism or MultiMC executable for a client run.", flags=("--launcher-executable",), kind="path"),
                _f("launcher_root", "Explicit initialized launcher data root for a client run.", flags=("--launcher-root",), kind="path"),
                _f("launcher_profile", "Existing client launcher profile; its name is redacted from retained evidence.", flags=("--launcher-profile",), mutex_group="launch-identity", sensitive=True),
                _f("offline_name", "Offline player name when no launcher profile is selected; the CLI defaults to Workbench.", flags=("--offline-name",), mutex_group="launch-identity"),
                _f("launcher_java", "Explicit client launch JDK executable.", flags=("--launcher-java",), kind="path"),
                _f("launcher_java_state", "Managed client launch-JDK state root.", flags=("--launcher-java-state",), kind="path"),
                _f("server_template", "Existing measured, world-free dedicated-server template; omit only when a retained client stage can seed managed materialization.", flags=("--server-template",), kind="path", mutex_group="server-template-source"),
                _f("accept_minecraft_eula", "Accept the Minecraft EULA for this invocation's automatic managed server materialization.", flags=("--accept-minecraft-eula",), kind="boolean", mutex_group="server-template-source"),
                _f("server_java", "Explicit native full JDK executable for a server run.", flags=("--server-java",), kind="path"),
                _f("runtime_experiment", "Explicit allowlisted experiment; each selected owner applies it only to its fresh disposable projection.", flags=("--runtime-experiment",), kind="choice", choices=experiment_choices(server=True), repeat=True),
                _f("memory", "Maximum memory in MiB for each selected side; the CLI defaults to 8192.", flags=("--memory",), kind="integer"),
                _f("launch_timeout", "Readiness timeout in seconds for each selected side; the CLI defaults to 600.", flags=("--launch-timeout",)),
                _f("shutdown_timeout", "Graceful shutdown timeout in seconds for a selected dedicated server; the CLI defaults to 180.", flags=("--shutdown-timeout",)),
                _f("json", "Emit the complete retained unified-run result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "A fresh invocation builds once; --run reopens the exact retained candidate and never rebuilds it. Each command creates fresh selected-side smokes rather than reusing a prior attempt. A failed historical V1 client stage cannot be promoted in place.",
                "Each selected physical side keeps its existing readiness, exact loaded-byte, cleanup, and immutable-input requirements.",
                "Both-side success joins independent client and server observations; it is not a connected multiplayer test, baseline comparison, gameplay-correctness result, support admission, or release qualification.",
                "Server execution requires either an explicit measured template or explicit Minecraft EULA acceptance for managed materialization.",
                "A fresh server-only build has no retained client seed for the current managed server materializer and therefore requires an explicit measured template; a fresh both-side run can use EULA-authorized managed materialization.",
                "Minecraft EULA acceptance and redacted launcher-profile selection are per invocation; retained next actions report themselves unavailable when either must be supplied again.",
                "Compatibility experiments remain explicit, profile-bound inputs and are never applied to canonical pack or source bytes.",
            ),
        ),
        CommandSpec(
            "dev.show",
            "dev",
            "Preview a Supersymmetry mod build",
            "Inspect one constituent mod checkout, select its exact Supersymmetry Packwiz entry, and show the source-bound build and replacement plan without writing to either checkout.",
            "Workbench Shell observation over project-declared Gradle metadata and an explicitly selected Supersymmetry Packwiz manifest",
            "read-only",
            "none",
            _workbench_template("dev", "show"),
            (
                _f(
                    "project",
                    "Constituent mod checkout; defaults to the current directory.",
                    flags=("--project",),
                    kind="path",
                    default=".",
                ),
                _f(
                    "pack",
                    "Exact Supersymmetry Packwiz checkout used for replacement matching.",
                    flags=("--pack",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "pack_mod",
                    "Exact mods/*.pw.toml basename when automatic identity matching is ambiguous.",
                    flags=("--pack-mod",),
                ),
                _f(
                    "java_home",
                    "Explicit build JDK; otherwise use a matching managed or Gradle JDK.",
                    flags=("--java-home",),
                    kind="path",
                ),
                _f(
                    "json",
                    "Emit the complete source-bound plan.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This is a lower-level inspection route; use dev.run for the ordinary retained developer loop.",
                "This action only plans; it does not build a JAR or launch Minecraft.",
                "The source checkout may use legacy Forge tooling, but the later disposable execution target is Cleanroom.",
                "An exact Supersymmetry pack entry and compatible build JDK remain required.",
            ),
        ),
        CommandSpec(
            "dev.build",
            "dev",
            "Build a Supersymmetry constituent mod",
            "Snapshot one exact constituent checkout into managed custody, run its observed Gradle adapter, verify the distributable artifact, and prepare the exact Packwiz replacement overlay.",
            "Workbench Shell managed execution over the project-owned Gradle build and explicitly selected Supersymmetry Packwiz entry",
            "mutating",
            "inert-only",
            _workbench_template("dev", "build"),
            (
                _f("project", "Constituent mod checkout; defaults to the current directory.", flags=("--project",), kind="path", default="."),
                _f("pack", "Exact Supersymmetry Packwiz checkout used for replacement matching.", flags=("--pack",), kind="path", required=True),
                _f("pack_mod", "Exact mods/*.pw.toml basename when automatic identity matching is ambiguous.", flags=("--pack-mod",)),
                _f("java_home", "Explicit build JDK; otherwise use a matching managed or Gradle JDK.", flags=("--java-home",), kind="path"),
                _f("timeout", "Managed build timeout in seconds.", flags=("--timeout",), kind="integer", default=1200),
                _f("json", "Emit the complete retained build result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This is a lower-level build-only route; use dev.run for the ordinary retained developer loop.",
                "Builds run only in ignored Workbench custody and may download project dependencies through the project wrapper.",
                "Gradle assemble produces the candidate; project-specific check, test, and lint lifecycles remain separately visible work.",
                "A passed build verifies an exact artifact and overlay but does not prove it loads in Minecraft.",
            ),
        ),
        CommandSpec(
            "dev.stage",
            "dev",
            "Stage a constituent mod in Supersymmetry",
            "Build and verify the candidate, materialize or reuse the exact disposable Supersymmetry client, replace only the pinned baseline artifact, and prove every unrelated payload file stayed unchanged.",
            "Workbench Shell composition over project-owned Gradle output and the canonical Cleanroom Packwiz materializer",
            "mutating",
            "inert-only",
            _workbench_template("dev", "stage"),
            (
                _f("project", "Constituent mod checkout; defaults to the current directory.", flags=("--project",), kind="path", default="."),
                _f("pack", "Exact Supersymmetry Packwiz checkout used for replacement matching.", flags=("--pack",), kind="path", required=True),
                _f("pack_mod", "Exact mods/*.pw.toml basename when automatic identity matching is ambiguous.", flags=("--pack-mod",)),
                _f("java_home", "Explicit build JDK; otherwise use a matching managed or Gradle JDK.", flags=("--java-home",), kind="path"),
                _f("timeout", "Managed build timeout in seconds.", flags=("--timeout",), kind="integer", default=1200),
                _f("json", "Emit the complete retained build and client-stage result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This is a lower-level client-staging route; use dev.run for the ordinary retained developer loop.",
                "The current staging slice composes a disposable client; dedicated-server parity is not implemented.",
                "The staged candidate has not been launched and loaded-byte identity has not been observed.",
                "The canonical Packwiz materialization and both developer checkouts remain unchanged.",
            ),
        ),
        CommandSpec(
            "dev.launch",
            "dev",
            "Launch a staged Supersymmetry candidate",
            "Reopen one exact retained client stage, project it into a fresh launcher instance, launch Cleanroom, and require nonce-bound in-game source-JAR proof for the candidate before bounded shutdown and cleanup.",
            "Workbench Shell custody over the retained SUSY stage, Prism/MultiMC process, Forge loader source, and runtime evidence",
            "mutating",
            "inert-only",
            _workbench_template("dev", "launch"),
            (
                _f("run", "Exact retained staged SUSY run ID.", flags=("--run",), required=True),
                _f("launcher", "Launcher family.", flags=("--launcher",), kind="choice", choices=("prism", "multimc"), default="prism"),
                _f("launcher_executable", "Explicit Prism or MultiMC executable.", flags=("--launcher-executable",), kind="path"),
                _f("launcher_root", "Explicit initialized launcher data root.", flags=("--launcher-root",), kind="path"),
                _f("launcher_profile", "Existing launcher profile; its name is redacted from retained evidence.", flags=("--launcher-profile",), mutex_group="launch-identity", sensitive=True),
                _f("offline_name", "Offline player name when no launcher profile is selected.", flags=("--offline-name",), default="Workbench", mutex_group="launch-identity"),
                _f("launcher_java", "Explicit launch JDK executable.", flags=("--launcher-java",), kind="path"),
                _f("launcher_java_state", "Managed launch-JDK state root.", flags=("--launcher-java-state",), kind="path"),
                _f("runtime_experiment", "Explicit allowlisted experiment applied only to the fresh disposable projection.", flags=("--runtime-experiment",), kind="choice", choices=experiment_choices(server=False), repeat=True),
                _f("memory", "Client maximum memory in MiB.", flags=("--memory",), kind="integer", default=8192),
                _f("launch_timeout", "Client readiness timeout in seconds.", flags=("--launch-timeout",), default=600.0),
                _f("json", "Emit the complete retained launch result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This is a lower-level client-only route retained for compatibility and focused debugging; use dev.run for the ordinary retained developer loop.",
                "The launch operates only on an exact retained client stage selected by run ID; it never rebuilds or rewrites that stage.",
                "This first process-custody slice requires Windows-hosted Prism/MultiMC and a matching full JDK with javac.",
                "Candidate-loaded success requires the in-game Forge source path, size, and SHA-256 to match the projected staged JAR.",
                "Each profile-owned Recurrent Complex experiment is pack-version-bound, patches only the fresh disposable projection, and is retained explicitly in launch evidence; it never changes the staged or canonical client.",
                "Dedicated-server parity and debugger attachment remain outside this prototype.",
                "Pack compatibility overlays are not applied implicitly.",
            ),
        ),
        CommandSpec(
            "dev.launch-server",
            "dev",
            "Smoke a retained candidate on a SUSY dedicated server",
            "Reopen one passed constituent run, materialize or verify a world-free SUSY server template, replace the exact Packwiz baseline JAR, launch Cleanroom directly, prove Forge loaded the candidate bytes, and stop cleanly.",
            "Workbench Shell custody over a retained SUSY build, profile-bound server materialization or explicit template override, direct Java process group, Forge source proof, and fresh server lifecycle evidence",
            "mutating",
            "inert-only",
            _workbench_template("dev", "launch-server"),
            (
                _f("run", "Exact retained passing SUSY run ID; automatic materialization requires a staged client run, while an explicit template also accepts a build-only run.", flags=("--run",), required=True),
                _f("server_template", "Existing measured, world-free SUSY dedicated-server template override; omit to materialize from the retained pack.", flags=("--server-template",), kind="path", mutex_group="server-template-source", required_group=True),
                _f("accept_minecraft_eula", "Accept the Minecraft EULA for automatic managed server materialization; invalid with a template override.", flags=("--accept-minecraft-eula",), kind="boolean", mutex_group="server-template-source", required_group=True),
                _f("server_java", "Explicit native full JDK executable; otherwise use managed Cleanroom Java.", flags=("--server-java",), kind="path"),
                _f("runtime_experiment", "Explicit allowlisted experiment applied only to the disposable server projection; the shutdown bridge requires an override template containing its declared inputs.", flags=("--runtime-experiment",), kind="choice", choices=experiment_choices(server=True), repeat=True),
                _f("memory", "Dedicated-server maximum memory in MiB.", flags=("--memory",), kind="integer", default=8192),
                _f("launch_timeout", "Dedicated-server readiness timeout in seconds.", flags=("--launch-timeout",), default=600.0),
                _f("shutdown_timeout", "Graceful server shutdown timeout in seconds.", flags=("--shutdown-timeout",), default=180.0),
                _f("json", "Emit the complete retained server-smoke result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This is a lower-level server-only route retained for compatibility and focused debugging; use dev.run for the ordinary retained developer loop.",
                "Automatic materialization requires explicit Minecraft EULA acceptance and native Linux/WSL Java; an existing measured template remains available as an override.",
                "Automatic materialization requires a passed client-stage run because the verified canonical client supplies API-excluded pack bytes.",
                "A managed template is derived from the retained exact pack and Cleanroom profile under ignored custody; it is not promoted to canonical distribution or support authority.",
                "Server success requires full server-side Packwiz inventory, exact candidate source path, size, and SHA-256 proof, Minecraft Done, the FTB Library pack-ready marker, a non-empty GroovyScript server log without the upstream failure sentinel, acknowledged stop/save lifecycle, zero exit, and an empty owned process group.",
                "Fresh FATAL, ERROR, and WARN diagnostics are retained and surfaced separately; candidate attribution requires a baseline control.",
                "The result is an independent dedicated-server smoke, not a client/server parity or gameplay-correctness decision.",
                "Pack compatibility experiments are applied only when selected explicitly and remain bound to the disposable projection.",
            ),
        ),
        CommandSpec(
            "dev.check",
            "dev",
            "Compare a retained candidate with its SUSY server baseline",
            "Run fresh baseline and candidate projections from one materialized or explicitly supplied server template and report whether the candidate introduced an observed server-health regression.",
            "Workbench Shell retained comparison over one exact server-template identity, matched dedicated-server launches, normalized diagnostics, and exact candidate identity",
            "mutating",
            "inert-only",
            _workbench_template("dev", "check"),
            (
                _f("run", "Exact retained passing SUSY run ID containing the candidate; automatic materialization requires a staged client run, while an explicit template also accepts a build-only run.", flags=("--run",), required=True),
                _f("side", "Comparison side; V1 supports dedicated server only.", flags=("--side",), kind="choice", choices=("server",), required=True),
                _f("server_template", "Existing measured, world-free SUSY dedicated-server template override used for both attempts; omit to materialize from the retained pack.", flags=("--server-template",), kind="path", mutex_group="server-template-source", required_group=True),
                _f("accept_minecraft_eula", "Accept the Minecraft EULA for automatic managed server materialization; invalid with a template override.", flags=("--accept-minecraft-eula",), kind="boolean", mutex_group="server-template-source", required_group=True),
                _f("server_java", "Explicit native full JDK executable; otherwise use managed Cleanroom Java.", flags=("--server-java",), kind="path"),
                _f("runtime_experiment", "Explicit allowlisted experiment applied identically to both disposable server projections; the shutdown bridge requires an override template containing its declared inputs.", flags=("--runtime-experiment",), kind="choice", choices=experiment_choices(server=True), repeat=True),
                _f("memory", "Maximum memory in MiB for each dedicated-server attempt.", flags=("--memory",), kind="integer", default=8192),
                _f("launch_timeout", "Readiness timeout in seconds for each dedicated-server attempt.", flags=("--launch-timeout",), default=600.0),
                _f("shutdown_timeout", "Graceful shutdown timeout in seconds for each dedicated-server attempt.", flags=("--shutdown-timeout",), default=180.0),
                _f("json", "Emit the complete retained server-comparison result.", flags=("--json",), kind="boolean"),
            ),
            "docs/architecture/MANAGED-RUN-PROFILES.md",
            availability="experimental",
            limitations=(
                "This remains the lower-level matched server baseline/candidate comparison; dev.run does not replace its attribution role.",
                "V1 compares dedicated-server startup health only; client comparison and connected client/server parity remain outside this slice.",
                "A no-observed-regression verdict is bounded to matched startup attempts and normalized retained diagnostics; it is not proof of gameplay correctness or release readiness.",
                "Incomparable attempts remain failed and do not suppress baseline or candidate evidence.",
                "Automatic materialization requires explicit Minecraft EULA acceptance; an existing measured template remains available as an override.",
                "Automatic materialization requires a passed client-stage run because the verified canonical client supplies API-excluded pack bytes.",
                "The exact managed or overridden template and selected compatibility experiments are retained inputs, not support authority.",
            ),
        ),
        CommandSpec(
            "explorer.search",
            "explorer",
            "Search the assembled game",
            "Search project declarations, exact artifacts, Atlas runtime nodes, module receipts, retained output, and contextual Manuals in one authority-labeled result.",
            "Workbench Shell projection over Project Intelligence, Atlas, Crucible, and Manuals",
            "read-only",
            "none",
            _workbench_template("explore"),
            (
                _f("query", "Optional identity, stack frame, coordinate, source locator, or omnibox text; filters alone also form a query.", positional=True, nargs="one_or_more"),
                _f("project", "Workspace or source subdirectory to inventory.", flags=("--project",), kind="path"),
                _f("no_project", "Omit the static project surface.", flags=("--no-project",), kind="boolean"),
                _f("runtime_db", "Explicit immutable Atlas runtime-graph query database.", flags=("--runtime-db",), kind="path"),
                _f("artifact", "Exact JAR/ZIP; repeatable.", flags=("--artifact",), kind="path", repeat=True),
                _f("receipt", "Exact module receipt JSON; repeatable.", flags=("--receipt",), kind="path", repeat=True),
                _f("session", "Retained console session or event JSONL; repeatable.", flags=("--session",), kind="path", repeat=True),
                _f("log", "Raw build/game log; repeatable.", flags=("--log",), kind="path", repeat=True),
                _f("no_manuals", "Omit contextual Manuals.", flags=("--no-manuals",), kind="boolean"),
                _f("kind", "Entity kind filter; repeatable.", flags=("--kind",), repeat=True),
                _f("owner", "Owner filter; repeatable.", flags=("--owner",), repeat=True),
                _f("state", "Evidence-state filter; repeatable.", flags=("--state",), repeat=True),
                _f("profile", "Runtime profile filter; repeatable.", flags=("--profile",), repeat=True),
                _f("side", "Physical-side filter; repeatable.", flags=("--side",), repeat=True),
                _f("source", "Source authority/kind filter; repeatable.", flags=("--source",), repeat=True),
                _f("limit", "Maximum returned entities (1-100).", flags=("--limit",), kind="integer", default=20),
                _f("json", "Emit the complete V1 result envelope.", flags=("--json",), kind="boolean"),
                _f("details", "Show facets, relationships, and limitations.", flags=("--details",), kind="boolean"),
                _f("sources", "Show exact source coverage.", flags=("--sources",), kind="boolean"),
                _f("require_observed", "Return exit 1 without a validated observed facet.", flags=("--require-observed",), kind="boolean"),
            ),
            "docs/architecture/EXACT-RUNTIME-EXPLORER.md",
            availability="experimental",
            limitations=("The nested console wizard runs one bounded query; use `workbench explore` directly for the persistent omnibox.",),
        ),
        CommandSpec(
            "doctor.inspect", "doctor", "Inspect workspace", "Resolve exact workspace, Cleanroom, pack, toolchain, and bounded runtime readiness without provisioning.", "Project Intelligence facts composed by Workbench Shell", "writes-output", "none", _workbench_template("doctor"), doctor_fields, "docs/architecture/WORKSPACE-DOCTOR.md", availability="experimental",
        ),
        CommandSpec(
            "cleanroom.fixture-build",
            "cleanroom",
            "Build the frozen Cleanroom mod fixture",
            (
                "Revalidate the exact generic-mod owner lock and Java 25 toolchain, "
                "then replace this process with one Cleanroom/Unimined Gradle clean "
                "check under ignored Workbench storage."
            ),
            "Cleanroom platform profile fixture lock; Crucible retains process custody",
            "mutating",
            "inert-only",
            (
                "{python}",
                "{root}/profiles/platforms/cleanroom/tools/run_generic_mod_fixture_build.py",
                "{options:0}",
            ),
            (
                _f(
                    "gradle_cmd",
                    "Absolute non-symlink Gradle executable selected by the developer.",
                    flags=("--gradle-cmd",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "java_home",
                    "Absolute Java 25 home selected by the developer.",
                    flags=("--java-home",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "expected_input_digest",
                    "Exact fixture and toolchain input binding retained by Home.",
                    flags=("--expected-input-digest",),
                    required=True,
                ),
                _f(
                    "state_root",
                    "External state root for projections and Gradle caches.",
                    flags=("--state-root",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "check_only",
                    "Validate the fixture and toolchain without starting Gradle.",
                    flags=("--check-only",),
                    kind="boolean",
                ),
            ),
            "profiles/platforms/cleanroom/README.md",
            availability="experimental",
            limitations=(
                "This action is exact to the frozen generic-mod-daily-loop fixture and does not authorize arbitrary projects.",
                "The developer supplies existing Gradle and Java 25 installations; Workbench does not download toolchains.",
                "Build, cache, and dependency state remains under ignored .workbench storage.",
            ),
        ),
        CommandSpec(
            "runs.managed", "runs", "Managed development run", "Preview or execute a pack-owned fast, debug, worldgen, performance, or authority-bounded proof recipe.", "Crucible execution from a Doctor-bound pack recipe", "mutating", "append-show", _workbench_template("run"),
            (
                _f("recipe", "Pack-owned recipe name.", positional=True, required=True),
                _f("profile", "Explicit pack profile.", flags=("--profile",), required=True),
                _f("side", "Runtime side.", flags=("--side",), default="dedicated-server"),
                _f("runtime_template", "Runtime template override.", flags=("--runtime-template",), kind="path"),
                _f("strata_root", "Strata checkout override.", flags=("--strata-root",), kind="path"),
                _f("java_cmd", "Java command override.", flags=("--java-cmd",)),
                _f("gradle_cmd", "Gradle command override.", flags=("--gradle-cmd",)),
                _f("seed", "World seed.", flags=("--seed",), kind="integer"),
                _f("region", "minChunkX,minChunkZ,widthChunks,heightChunks.", flags=("--region",)),
                _f("label", "Fresh retained iteration label.", flags=("--label",)),
                _f("heap", "JVM heap override.", flags=("--heap",)),
                _f("show", "Show the inert concise plan.", flags=("--show",), kind="boolean", mutex_group="preview", console_managed=True),
                _f("json", "Show the inert JSON plan.", flags=("--json",), kind="boolean", mutex_group="preview"),
            ), "docs/architecture/MANAGED-RUN-PROFILES.md", availability="experimental",
        ),
    ]
    commands.extend(_runtime_commands())
    commands.append(_worldgen_command())
    commands.extend(_cockpit_commands())
    commands.extend(_qualifier_commands())
    commands.extend(_subsurface_commands())
    commands.append(_groovy_program_command())
    commands.append(_groovy_language_service_command())
    commands.append(_groovy_managed_session_command())
    commands.append(_recipe_invalidations_command())
    commands.extend(_semantic_projection_commands())
    return commands


def _preview_fields(*, include_apply: bool = False) -> tuple[FieldSpec, ...]:
    fields = [
        _f("show", "Show the inert operation plan.", flags=("--show",), kind="boolean", mutex_group="preview", console_managed=True),
        _f("json", "Emit the inert operation plan as JSON.", flags=("--json",), kind="boolean", mutex_group="preview"),
    ]
    if include_apply:
        fields.append(_f("apply", "Execute the freshly revalidated plan.", flags=("--apply",), kind="boolean", mutex_group="preview", console_managed=True))
    return tuple(fields)


def _golden_journey_commands() -> list[CommandSpec]:
    document_dev = "docs/product/CLEANROOM-PLATFORM.md"
    document_change = "docs/architecture/FEATURE-STUDIO.md"
    state_json = (
        _f("state_root", "Explicit private journey state root.", flags=("--state-root",), kind="path"),
        _f("json", "Emit the complete owner record.", flags=("--json",), kind="boolean"),
    )
    commands = [
        CommandSpec(
            "dev.fixture-plan", "dev", "Plan generic Cleanroom fixture loop",
            "Resolve the exact profile-owned fixture, Gradle, Java 25, independent side targets, commands, and mutations without creating run state.",
            "Cleanroom platform fixture owner composed by Workbench Shell",
            "writes-output", "none", _workbench_template("dev", "fixture", "plan"),
            (
                _f("gradle_cmd", "Exact Gradle executable.", flags=("--gradle-cmd",), kind="path", required=True),
                _f("java_home", "Exact Java 25 home.", flags=("--java-home",), kind="path", required=True),
                _f("state_root", "Private journey state root named in the plan.", flags=("--state-root",), kind="path"),
                _f("side", "Independent physical side selection.", flags=("--side",), kind="choice", choices=("client", "server", "both"), default="both"),
                _f("debug", "Request debugger custody; currently fails unavailable.", flags=("--debug",), kind="boolean"),
                _f("output", "Fresh reviewed plan output.", flags=("--output",), kind="path"),
                _f("json", "Emit the complete plan.", flags=("--json",), kind="boolean"),
            ), document_dev, availability="experimental",
            limitations=("Planning is read-only unless an explicit fresh --output is selected.",),
        ),
        CommandSpec(
            "dev.fixture-run", "dev", "Run generic Cleanroom fixture loop",
            "Revalidate and execute one exact reviewed fixture plan, retaining build, artifact, client/server marker, stop, and cleanup records.",
            "Cleanroom platform fixture owner plus Crucible live-console custody",
            "mutating", "inert-only", _workbench_template("dev", "fixture", "run"),
            (
                _f("plan", "Exact reviewed fixture plan; otherwise supply the exact tools.", flags=("--plan",), kind="path"),
                _f("gradle_cmd", "Exact Gradle executable for a direct run.", flags=("--gradle-cmd",), kind="path"),
                _f("java_home", "Exact Java 25 home for a direct run.", flags=("--java-home",), kind="path"),
                _f("state_root", "Private journey state root.", flags=("--state-root",), kind="path"),
                _f("side", "Independent physical side selection.", flags=("--side",), kind="choice", choices=("client", "server", "both"), default="both"),
                _f("debug", "Request debugger custody; currently fails unavailable.", flags=("--debug",), kind="boolean"),
                _f("json", "Emit the complete run result.", flags=("--json",), kind="boolean"),
            ), document_dev, availability="experimental",
            limitations=(
                "This exact fixture exercises the development classpath; its current marker does not yet prove runtime-loaded JAR digest or debugger interaction.",
                "It is a construction slice, not D01 L6 evidence.",
            ),
        ),
        CommandSpec(
            "dev.fixture-recover", "dev", "Recover generic Cleanroom fixture loop",
            "Revalidate retained live-console custody and report the exact retry boundary for one fixture receipt.",
            "Cleanroom dev-loop recovery over live-console owner records",
            "read-only", "none", _workbench_template("dev", "fixture", "recover"),
            (
                _f("receipt", "Exact retained fixture receipt.", positional=True, kind="path", required=True),
                _f("json", "Emit the complete recovery projection.", flags=("--json",), kind="boolean"),
            ), document_dev, availability="experimental",
        ),
        CommandSpec(
            "change.material-fluid-start", "change", "Start material-fluid recipe change",
            "Retain one profile-owned Supersymmetry material-fluid recipe intent and exact reversible Blueprint plan without editing source.",
            "Workbench Shell over Supersymmetry profile and Blueprints transaction owner",
            "writes-output", "inert-only", _workbench_template("change", "start", "material-fluid-recipe"),
            (
                _f("workspace", "Exact Supersymmetry checkout.", positional=True, kind="path", required=True),
                _f("state_root", "Private feature-change state root.", flags=("--state-root",), kind="path"),
                *tuple(_f(key, f"Typed {key.replace('_', ' ')}.", flags=("--" + key.replace("_", "-"),), required=True) for key in ("name", "color", "translation", "symbol", "recipe_script", "recipe_map", "input_fluid", "voltage_tier")),
                *tuple(_f(key, f"Typed {key.replace('_', ' ')}.", flags=("--" + key.replace("_", "-"),), kind="integer", required=True) for key in ("input_amount", "output_amount", "duration")),
                _f("json", "Emit the retained change identity.", flags=("--json",), kind="boolean"),
            ), document_change, availability="experimental",
            limitations=("Starting retains state but does not edit the target checkout.",),
        ),
    ]
    for action, risk, availability in (
        ("open", "read-only", "experimental"),
        ("test", "mutating", "experimental"),
        ("apply", "mutating", "experimental"),
        ("verify", "mutating", "experimental"),
        ("rollback", "mutating", "experimental"),
        ("recover", "mutating", "experimental"),
    ):
        fields: list[FieldSpec] = [
            _f("change_id", "Exact retained feature-change identity.", positional=True, required=True),
            *state_json,
        ]
        if action == "apply":
            fields.insert(
                2,
                _f("consent_plan_id", "Exact reviewed plan identity.", flags=("--consent-plan-id",), required=True),
            )
        if action in {"test", "verify", "rollback"}:
            fields.insert(
                2,
                _f(
                    "runtime_config",
                    "Profile-owned URI-only installed client and dedicated-server runtime configuration.",
                    flags=("--runtime-config",),
                    kind="path",
                    required=action in {"test", "verify"},
                ),
            )
        if action in {"test", "verify"}:
            limitations = (
                "Requires independently supplied local runtime paths through the exact Supersymmetry profile configuration contract.",
                "A passing pair is F01 journey evidence, not support admission or release qualification.",
            )
        elif action == "rollback":
            limitations = (
                "Without --runtime-config, exact before-bytes can be restored but the result remains runtime-unverified.",
            )
        else:
            limitations = ()
        commands.append(
            CommandSpec(
                f"change.material-fluid-{action}",
                "change",
                f"{action.title()} material-fluid recipe change",
                f"{action.title()} one exact retained material-fluid recipe workspace through its owner port.",
                "Workbench Shell orchestration over retained Blueprints/profile/runtime owner records",
                risk,
                "inert-only" if risk == "mutating" else "none",
                _workbench_template("change", action, "material-fluid-recipe"),
                tuple(fields),
                document_change,
                availability=availability,
                limitations=limitations,
            )
        )
    return commands


def _groovy_program_command() -> CommandSpec:
    return CommandSpec(
        "pack-program.groovy-dev",
        "pack-program",
        "Inspect Groovy pack program",
        "Bind, inventory, compare, and source-link one exact staged GroovyScript program with profile-owned identity and reload guidance.",
        "Workbench orchestration over static observation plus explicit pack/platform profiles; runtime evidence retains its owner",
        "writes-output",
        "none",
        _workbench_template("groovy", "dev"),
        (
            _f("profile", "Explicit named pack-program adapter.", flags=("--profile",), mutex_group="profile-source", required_group=True),
            _f("profile_file", "Explicit pack-program profile JSON.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
            _f("source", "Pack root or Groovy root to inspect.", flags=("--source",), kind="path", default="."),
            _f("baseline", "Exact baseline pack or Groovy root.", flags=("--baseline",), kind="path"),
            _f("side", "Exact preprocessor/runtime side.", flags=("--side",), kind="choice", choices=("dedicated-server", "integrated-server", "client"), default="dedicated-server"),
            _f("packmode", "Effective packmode override.", flags=("--packmode",)),
            _f("debug_state", "Effective GroovyScript debug state.", flags=("--debug-state",), kind="choice", choices=("auto", "on", "off"), default="auto"),
            _f("mod", "Installed mod ID; repeatable.", flags=("--mod",), repeat=True),
            _f("changed", "Changed path relative to the Groovy root; repeatable.", flags=("--changed",), kind="path", repeat=True),
            _f("groovy_log", "Exact GroovyScript runtime log observation.", flags=("--groovy-log",), kind="path"),
            _f("runtime_diagnosis", "Workbench runtime diagnosis to correlate.", flags=("--runtime-diagnosis",), kind="path"),
            _f("json", "Emit the complete source-linked V1 report.", flags=("--json",), kind="boolean"),
            _f(
                "recipe_review",
                "Use the decision-first Recipe Review presentation selected by the public review command.",
                flags=("--recipe-review",),
                kind="boolean",
            ),
            _f(
                "verbose",
                "Include full program, lifecycle, and provenance detail.",
                flags=("--verbose",),
                kind="boolean",
            ),
            _f("output", "Write the complete JSON report to a fresh path.", flags=("--output",), kind="path"),
            _f("strict", "Return attention as exit 1.", flags=("--strict",), kind="boolean"),
        ),
        "docs/architecture/PACK-PROGRAM-STUDIO.md",
        availability="experimental",
        limitations=(
            "This action is comment-aware static analysis; use pack-program.groovy-check for canonicalization diagnostics, while observed registry effects remain unavailable.",
        ),
    )


def _groovy_language_service_command() -> CommandSpec:
    return CommandSpec(
        "pack-program.groovy-check",
        "pack-program",
        "Check Groovy with exact runtime compiler",
        "Send exact in-memory source through GroovyScript's embedded language server, prove diagnostics with a canary cycle, and bind the result to a local runtime inventory.",
        "GroovyScript embedded compiler observation plus Workbench exact input custody; endpoint identity remains unavailable",
        "writes-output",
        "none",
        _workbench_template("groovy", "check"),
        (
            _f("profile", "Explicit named pack-program adapter.", flags=("--profile",), mutex_group="profile-source", required_group=True),
            _f("profile_file", "Explicit pack-program profile JSON.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
            _f("language_profile", "Explicit language-service profile override.", flags=("--language-profile",), kind="path"),
            _f("source", "Pack root or Groovy candidate root.", flags=("--source",), kind="path", default="."),
            _f("runtime_root", "Exact client runtime root to inventory.", flags=("--runtime-root",), kind="path", required=True),
            _f("files", "Groovy path relative to source; repeatable.", flags=("--file",), repeat=True, mutex_group="selection", required_group=True),
            _f("all_files", "Check every configured, non-excluded script.", flags=("--all",), kind="boolean", mutex_group="selection", required_group=True),
            _f("packmode", "Effective packmode override.", flags=("--packmode",)),
            _f("debug_state", "Effective GroovyScript debug state.", flags=("--debug-state",), kind="choice", choices=("auto", "on", "off"), default="auto"),
            _f("mod", "Installed mod ID for preprocessor evaluation; repeatable.", flags=("--mod",), repeat=True),
            _f("host", "Language-server host.", flags=("--host",)),
            _f("port", "Language-server port.", flags=("--port",), kind="integer"),
            _f("server_workspace_uri", "File URI visible to the server for path mapping.", flags=("--server-workspace-uri",)),
            _f("allow_remote", "Allow exact source disclosure to a non-loopback endpoint.", flags=("--allow-remote",), kind="boolean"),
            _f("connect_timeout", "TCP and initialize timeout seconds.", flags=("--connect-timeout",), default=5.0),
            _f("diagnostic_timeout", "Per-step diagnostic timeout seconds.", flags=("--diagnostic-timeout",), default=20.0),
            _f("java", "Exact Java executable to hash and probe.", flags=("--java",), kind="path"),
            _f("runtime_receipt", "Runtime launch receipt retained as endpoint context.", flags=("--runtime-receipt",), kind="path"),
            _f("json", "Emit the complete V1 compiler result.", flags=("--json",), kind="boolean"),
            _f("output", "Write the complete result to a fresh path.", flags=("--output",), kind="path"),
            _f("strict", "Return diagnostics or inconclusive state as exit 1.", flags=("--strict",), kind="boolean"),
        ),
        "docs/architecture/PACK-PROGRAM-STUDIO.md",
        availability="experimental",
        limitations=(
            "GroovyScript 1.4.3 requires a running physical client and cannot authenticate its TCP endpoint to the supplied runtime inventory.",
        ),
    )


def _groovy_managed_session_command() -> CommandSpec:
    return CommandSpec(
        "pack-program.groovy-session",
        "pack-program",
        "Start managed Groovy language session",
        "Launch an exact disposable Prism client, prove GroovyScript readiness, publish one shared terminal/IntelliJ/VS Code endpoint, and restore every checked overlay on stop.",
        "Workbench process and input custody over the upstream GroovyScript service; compiler semantics remain upstream",
        "mutating",
        "inert-only",
        _workbench_template("groovy", "session"),
        (
            _f("profile", "Explicit named pack-program adapter.", flags=("--profile",), mutex_group="profile-source", required_group=True),
            _f("profile_file", "Explicit pack-program profile JSON.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
            _f("language_profile", "Explicit language-service profile override.", flags=("--language-profile",), kind="path"),
            _f("session_profile", "Explicit managed-session profile override.", flags=("--session-profile",), kind="path"),
            _f("source", "Pack root or Groovy workspace root.", flags=("--source",), kind="path", default="."),
            _f("runtime_root", "Receipt-bound disposable client .minecraft root.", flags=("--runtime-root",), kind="path", required=True),
            _f("launch_receipt", "Completed Workbench runtime-launch V3 receipt.", flags=("--launch-receipt",), kind="path", required=True),
            _f("session_storage", "Retained language-session storage root.", flags=("--session-storage",), kind="path"),
            _f("port", "Explicit free loopback port; otherwise reserve one randomly.", flags=("--port",), kind="integer"),
            _f("packmode", "Effective packmode override.", flags=("--packmode",)),
            _f("debug_state", "Effective GroovyScript debug state.", flags=("--debug-state",), kind="choice", choices=("auto", "on", "off"), default="auto"),
            _f("mod", "Installed mod ID for preprocessor evaluation; repeatable.", flags=("--mod",), repeat=True),
            _f("readiness_timeout", "Physical-client and exact LSP readiness timeout seconds.", flags=("--readiness-timeout",)),
            _f("session_timeout", "Maximum ready-session lifetime seconds.", flags=("--session-timeout",)),
            _f("connect_timeout", "Per-attempt TCP timeout seconds.", flags=("--connect-timeout",), default=5.0),
            _f("diagnostic_timeout", "Readiness canary protocol timeout seconds.", flags=("--diagnostic-timeout",), default=20.0),
            _f("json_events", "Emit lifecycle JSONL with the ready IDE descriptor.", flags=("--json-events",), kind="boolean", mutex_group="render"),
            _f("json", "Emit the complete final session receipt.", flags=("--json",), kind="boolean", mutex_group="render"),
            _f("output", "Copy the final receipt to a fresh path.", flags=("--output",), kind="path"),
        ),
        "docs/architecture/PACK-PROGRAM-STUDIO.md",
        availability="experimental",
        limitations=(
            "The action accepts only a completed Workbench V3 receipt for a disposable Prism projection and gives the single-active-client upstream endpoint to one consumer at a time.",
        ),
    )


def _recipe_invalidations_command() -> CommandSpec:
    return CommandSpec(
        "pack-program.recipe-invalidations",
        "pack-program",
        "Diagnose recipe-registration signals",
        "Reduce one exact Supersymmetry V3 runtime receipt, or compare two explicitly selected client cold starts, into separate Groovy conflict and GT startup registration signal groups without claiming stable recipe identity.",
        "Supersymmetry profile-owned observation and composition, selected by its runtime diagnostic catalog and orchestrated by Workbench Shell",
        "read-only",
        "none",
        (
            "{python}",
            "{root}/tools/workbench.py",
            "runtime-diagnose",
            "{options:0}",
            "--recipe-invalidations",
            "{options:1}",
        ),
        (
            _f(
                "workspace",
                "Compatible Supersymmetry Packwiz workspace.",
                positional=True,
                kind="path",
                required=True,
                placement=0,
            ),
            _f(
                "receipt",
                "Exact candidate or single-run Workbench runtime-launch V3 receipt.",
                flags=("--receipt",),
                kind="path",
                required=True,
                placement=1,
            ),
            _f(
                "baseline_receipt",
                "Optional explicit baseline runtime-launch V3 receipt; never inferred or stored as last-green.",
                flags=("--baseline-receipt",),
                kind="path",
                placement=1,
            ),
            _f(
                "json",
                "Emit the complete profile-owned diagnostic or comparison.",
                flags=("--json",),
                kind="boolean",
                placement=1,
            ),
        ),
        "profiles/packs/supersymmetry/atlas/recipe-invalidation-diagnostic-v2.md",
        availability="available",
        limitations=(
            "This is a preview feature for the explicit Supersymmetry profile on provisional Cleanroom support, not a general GT or release-support claim.",
            "Groovy conflict counts and GT registration owner-method counts remain separate; the report has no stable recipe identity or cross-channel deduplication, and the same method count can mask different attempts.",
            "Observed owner frames and localized furnace descriptions are bounded attribution or context hints, not causal proof or stable identities.",
            "Standalone V3 receipts do not bind source revision or launch-time profile bytes, so observed deltas are not attributed to a source change.",
            "Fewer or absent signals do not prove a fix, an unchanged effective registry, recipe execution or lookup behavior, progression, JEI visibility, save safety, or release readiness.",
        ),
    )


def _semantic_projection_commands() -> list[CommandSpec]:
    document = "modules/atlas/contracts/workbench-semantic-projection-v1.md"
    return [
        CommandSpec(
            "atlas.semantic-check",
            "atlas",
            "Check semantic acceptance gate",
            "Replay the first profile-owned historical semantic regressions against the layered Atlas projection.",
            "Atlas derivation over Pack Program Studio declarations and Crucible observations",
            "read-only",
            "none",
            _workbench_template("check"),
            (
                _f("profile", "Explicit pack-owned semantic adapter.", flags=("--profile",), required=True),
                _f("fixture", "Optional single profile-owned regression fixture.", flags=("--fixture",)),
                _f("include_next", "Also replay fixtures queued after the first gate.", flags=("--include-next",), kind="boolean"),
                _f("json", "Emit the complete versioned result.", flags=("--json",), kind="boolean"),
            ),
            document,
            availability="experimental",
        ),
        CommandSpec(
            "atlas.semantic-why",
            "atlas",
            "Explain a semantic result",
            "Trace one canonical identity through SOURCE, RUNTIME, PLAYABLE, diagnostics, and exact provenance.",
            "Atlas explanation with Pack Program Studio and Crucible evidence retained",
            "read-only",
            "none",
            _workbench_template("why"),
            (
                _f("query", "Semantic ID or unambiguous profile identity.", positional=True, required=True),
                _f("profile", "Explicit pack-owned semantic adapter.", flags=("--profile",), required=True),
                _f("fixture", "Profile-owned semantic regression fixture.", flags=("--fixture",), required=True),
                _f("json", "Emit the complete versioned result.", flags=("--json",), kind="boolean"),
            ),
            document,
            availability="experimental",
        ),
        CommandSpec(
            "atlas.semantic-impact",
            "atlas",
            "Show semantic impact",
            "Report the bounded diagnostic and layer blast radius of one identity or the entire selected projection.",
            "Atlas derived impact over retained layer evidence",
            "read-only",
            "none",
            _workbench_template("impact"),
            (
                _f("query", "Optional semantic ID or unambiguous profile identity.", positional=True),
                _f("profile", "Explicit pack-owned semantic adapter.", flags=("--profile",), required=True),
                _f("fixture", "Profile-owned semantic regression fixture.", flags=("--fixture",), required=True),
                _f("json", "Emit the complete versioned result.", flags=("--json",), kind="boolean"),
            ),
            document,
            availability="experimental",
        ),
    ]


def _runtime_commands() -> list[CommandSpec]:
    doc = "docs/architecture/DISPOSABLE-RUNTIME-WORLD-MANAGER.md"
    check_fields = (
        _f("checks", "Use Core retained-check storage across projects and profiles.", flags=("--checks",), kind="boolean", placement=1),
        _f("checks_root", "Explicit retained-check store, including an inactive custom state location.", flags=("--checks-root",), kind="path", placement=1),
    )
    commands = [
        CommandSpec("storage.list", "storage", "Inventory local storage", "List exact Workbench-managed and protected local resources.", "Crucible custody inventory", "read-only", "none", _workbench_template("storage", "{options:1}", "list"), (*check_fields, _f("json", "Emit the complete inventory as JSON.", flags=("--json",), kind="boolean"),), doc, availability="experimental"),
        CommandSpec("storage.inspect", "storage", "Inspect storage item", "Inspect one inventory resource by exact or unambiguous selector.", "Crucible custody inventory", "read-only", "none", _workbench_template("storage", "{options:1}", "inspect"), (*check_fields, _f("selector", "Exact resource selector.", positional=True, required=True), _f("json", "Emit the complete record as JSON.", flags=("--json",), kind="boolean")), doc, availability="experimental"),
        CommandSpec("storage.cleanup", "storage", "Recoverable cleanup", "Preview or move one exact managed resource into recoverable trash.", "Crucible custody manager", "mutating", "plan-then-apply", _workbench_template("storage", "{options:1}", "cleanup"), (*check_fields, _f("selector", "Exact resource selector.", positional=True, required=True), _f("allow_review", "Allow an explicitly reviewed resource in the plan.", flags=("--allow-review",), kind="boolean"), *_preview_fields(include_apply=True)), doc, availability="experimental"),
        CommandSpec("storage.restore", "storage", "Restore recoverable trash", "Preview or restore one exact recoverable trash transaction.", "Crucible custody manager", "mutating", "plan-then-apply", _workbench_template("storage", "{options:1}", "restore"), (*check_fields, _f("selector", "Exact trash selector.", positional=True, required=True), *_preview_fields(include_apply=True)), doc, availability="experimental"),
        CommandSpec("storage.purge", "storage", "Permanently purge trash", "Preview and, only with exact transaction confirmation, permanently purge one trash transaction.", "Crucible custody manager", "destructive", "confirm-and-show", _workbench_template("storage", "{options:1}", "purge"), (*check_fields, _f("selector", "Exact trash selector.", positional=True, required=True), _f("confirm", "Exact selected trash transaction ID.", flags=("--confirm",)), *_preview_fields()), doc, availability="experimental", limitations=("Permanent execution still requires the downstream exact --confirm value.",)),
        CommandSpec("runtime.create", "runtime", "Create disposable runtime", "Preview or independently clone an audited profile template into managed storage.", "Crucible custody manager", "mutating", "append-show", _workbench_template("runtime", "create"), (_f("profile", "Explicit pack profile.", flags=("--profile",), required=True), _f("runtime_template", "Runtime template override.", flags=("--runtime-template", "--template"), kind="path"), _f("label", "Fresh managed runtime label.", flags=("--label",), required=True), _f("seed", "World seed.", flags=("--seed",), kind="integer"), _f("level_name", "Reserved absent world name.", flags=("--level-name",), default="world"), _f("server_port", "Server port.", flags=("--server-port",), kind="integer"), *_preview_fields()), doc, availability="experimental"),
        CommandSpec("world.snapshot", "worlds", "Snapshot managed world", "Preview or copy one quiesced managed world into an immutable snapshot.", "Crucible custody manager", "mutating", "append-show", _workbench_template("world", "snapshot"), (_f("selector", "Managed runtime selector.", positional=True, required=True), _f("label", "Fresh snapshot label.", flags=("--label",), required=True), _f("world", "Named world when more than one exists.", flags=("--world",)), *_preview_fields()), doc, availability="experimental"),
        CommandSpec("world.restore", "worlds", "Restore into fresh world", "Preview or restore a compatible snapshot only into a fresh managed runtime path.", "Crucible custody manager", "mutating", "plan-then-apply", _workbench_template("world", "restore"), (_f("snapshot_selector", "Exact snapshot selector.", positional=True, required=True), _f("profile", "Explicit pack profile.", flags=("--profile",), required=True), _f("label", "Fresh runtime label.", flags=("--label",), required=True), _f("runtime_template", "Runtime template override.", flags=("--runtime-template", "--template"), kind="path"), _f("level_name", "Fresh world name.", flags=("--level-name",)), _f("server_port", "Server port.", flags=("--server-port",), kind="integer"), *_preview_fields(include_apply=True)), doc, availability="experimental"),
    ]
    return commands


def _worldgen_command() -> CommandSpec:
    return CommandSpec(
        "world-studio.iterate", "world-studio", "Worldgen development iteration", "Build World Studio, provision a fresh Cleanroom world, generate a bounded region, retain diagnostics, and hand checked output to Strata.", "Crucible controlled experiment; Atlas alone interprets admitted evidence", "mutating", "inert-only", _workbench_template("worldgen", "dev"),
        (
            _f("profile", "Explicit pack profile.", flags=("--profile",), mutex_group="profile-source", required_group=True),
            _f("profile_file", "Explicit pack profile file.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
            _f("runtime_template", "Runtime template override.", flags=("--runtime-template",), kind="path"),
            _f("strata_root", "Strata checkout override.", flags=("--strata-root",), kind="path"),
            _f("java_cmd", "Java command override.", flags=("--java-cmd",)),
            _f("gradle_cmd", "Gradle command override.", flags=("--gradle-cmd",)),
            _f("plan", "Frozen plan input.", flags=("--plan",), kind="path"),
            _f("artifact", "Exact production-remapped artifact override.", flags=("--artifact",), kind="path", mutex_group="artifact-source"),
            _f("observatory_artifact", "Exact Worldgen Observatory artifact for same-run Stage-3 proof.", flags=("--observatory-artifact",), kind="path"),
            _f("seed", "World seed.", flags=("--seed",), kind="integer"),
            _f("region", "Bounded chunk region.", flags=("--region",)),
            _f("mode", "Iteration mode.", flags=("--mode",), kind="choice", choices=("fast", "debug", "performance"), default="debug"),
            _f("label", "Fresh iteration label.", flags=("--label",)),
            _f("heap", "JVM heap override.", flags=("--heap",)),
            _f("diagnostic_sample_modulo", "Diagnostic sampling modulo.", flags=("--diagnostic-sample-modulo",), kind="integer"),
            _f("server_port", "Server port.", flags=("--server-port",), kind="integer"),
            _f("viewer_port", "Strata viewer port.", flags=("--viewer-port",), kind="integer"),
            _f("startup_timeout", "Server startup timeout seconds.", flags=("--startup-timeout",), kind="integer", default=300),
            _f("scan_timeout", "World scan timeout seconds.", flags=("--scan-timeout",), kind="integer", default=600),
            _f("stop_timeout", "Server stop timeout seconds.", flags=("--stop-timeout",), kind="integer", default=60),
            _f("compare", "Baseline log for same-seed semantic comparison.", flags=("--compare",), kind="path"),
            _f("skip_build", "Explicitly reuse the one existing remapped artifact.", flags=("--skip-build",), kind="boolean", mutex_group="artifact-source"),
            _f("no_open", "Validate viewer handoff without leaving it open.", flags=("--no-open",), kind="boolean"),
        ), "docs/architecture/WORLDGEN-ITERATION-RUNNER.md", availability="experimental",
        limitations=("The console presents live observations; it does not upgrade them to Observatory proof.",),
    )


def _cockpit_commands() -> list[CommandSpec]:
    """Expose paired Worldgen Cockpit acquisition, analysis, and review."""

    doc = "docs/architecture/WORLDGEN-COCKPIT.md"
    profile_fields = (
        _f("profile", "Explicit pack profile.", flags=("--profile",), kind="choice", choices=profile_choices("cockpit"), mutex_group="profile-source", required_group=True),
        _f("profile_file", "Exact cockpit pack-profile JSON.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
    )
    evidence_fields = (
        _f("baseline_observatory_bundle", "Baseline sealed Observatory bundle.", flags=("--baseline-observatory-bundle",), kind="path"),
        _f("candidate_observatory_bundle", "Candidate sealed Observatory bundle.", flags=("--candidate-observatory-bundle",), kind="path"),
        _f("comparison_scope_sha256", "Exact common Observatory comparison scope SHA-256.", flags=("--comparison-scope-sha256",)),
        _f("baseline_inventory", "Baseline exact GTCEu inventory.", flags=("--baseline-inventory",), kind="path"),
        _f("candidate_inventory", "Candidate exact GTCEu inventory.", flags=("--candidate-inventory",), kind="path"),
        _f("baseline_impact", "Baseline GTCEu impact inventory.", flags=("--baseline-impact",), kind="path"),
        _f("candidate_impact", "Candidate GTCEu impact inventory.", flags=("--candidate-impact",), kind="path"),
        _f("baseline_trace", "Baseline controlled GTCEu trace.", flags=("--baseline-trace",), kind="path"),
        _f("candidate_trace", "Candidate controlled GTCEu trace.", flags=("--candidate-trace",), kind="path"),
        _f("baseline_observer_off_jfr", "Baseline observer-off JFR summary.", flags=("--baseline-observer-off-jfr",), kind="path"),
        _f("candidate_observer_off_jfr", "Candidate observer-off JFR summary.", flags=("--candidate-observer-off-jfr",), kind="path"),
    )
    run_fields = (
        *profile_fields,
        _f("mode", "Cockpit evidence mode.", flags=("--mode",), kind="choice", choices=("fast", "debug", "performance"), default="fast"),
        _f("label", "Fresh paired experiment label.", flags=("--label",)),
        _f("seed", "Aligned world seed.", flags=("--seed",), kind="integer"),
        _f("region", "Aligned bounded chunk region.", flags=("--region",)),
        _f("order", "Execution order retained as a confound.", flags=("--order",), kind="choice", choices=("baseline-first", "candidate-first"), default="baseline-first"),
        _f("baseline_plan", "Baseline frozen Groovy plan.", flags=("--baseline-plan",), kind="path"),
        _f("candidate_plan", "Candidate frozen Groovy plan.", flags=("--candidate-plan",), kind="path"),
        _f("artifact", "One exact artifact for both sides.", flags=("--artifact",), kind="path", mutex_group="artifact-mode"),
        _f("baseline_artifact", "Baseline exact artifact; candidate artifact is also required.", flags=("--baseline-artifact",), kind="path"),
        _f("candidate_artifact", "Candidate exact artifact; baseline artifact is also required.", flags=("--candidate-artifact",), kind="path"),
        _f("runtime_template", "Disposable runtime template override.", flags=("--runtime-template",), kind="path"),
        _f("strata_root", "Strata checkout override.", flags=("--strata-root",), kind="path"),
        _f("java_cmd", "Cleanroom Java command override.", flags=("--java-cmd",)),
        _f("gradle_cmd", "Gradle command override.", flags=("--gradle-cmd",)),
        _f("heap", "JVM heap override.", flags=("--heap",)),
        _f("diagnostic_sample_modulo", "Diagnostic sample modulo.", flags=("--diagnostic-sample-modulo",), kind="integer"),
        _f("startup_timeout", "Server startup timeout seconds.", flags=("--startup-timeout",), kind="integer", default=300),
        _f("scan_timeout", "Strata scan timeout seconds.", flags=("--scan-timeout",), kind="integer", default=600),
        _f("stop_timeout", "Server stop timeout seconds.", flags=("--stop-timeout",), kind="integer", default=60),
        *evidence_fields,
        _f("open", "Open the retained local review after success.", flags=("--open",), kind="boolean"),
        _f("show", "Show the inert paired run plan.", flags=("--show",), kind="boolean", mutex_group="preview", console_managed=True),
        _f("json", "Emit the inert paired run plan as JSON.", flags=("--json",), kind="boolean", mutex_group="preview"),
    )
    compare_fields = (
        *profile_fields,
        _f("baseline_report", "Completed baseline iteration report.", flags=("--baseline-report",), kind="path", required=True),
        _f("candidate_report", "Completed candidate iteration report.", flags=("--candidate-report",), kind="path", required=True),
        *evidence_fields,
        _f("output", "Fresh content-addressed report output; also writes HTML.", flags=("--output",), kind="path"),
        _f("json", "Emit the complete report JSON.", flags=("--json",), kind="boolean"),
        _f("sources", "Show exact source paths and identities.", flags=("--sources",), kind="boolean"),
    )
    return [
        CommandSpec(
            "world-studio.cockpit-run",
            "world-studio",
            "Run fixed-seed Worldgen Cockpit",
            "Freeze and execute baseline/candidate in separate fresh worlds, then compare exact state, semantics, statistics, causal evidence, and performance according to mode.",
            "Crucible experiment custody + Strata final state + Atlas causal interpretation",
            "mutating",
            "append-show",
            _workbench_template("cockpit", "run"),
            run_fields,
            doc,
            availability="experimental",
            limitations=("Debug causal closure still requires paired admitted Observatory bundles.",),
        ),
        CommandSpec(
            "world-studio.cockpit-compare",
            "world-studio",
            "Compare completed worldgen runs",
            "Validate alignment and compose two completed iterations into one high-signal cockpit report and optional local review.",
            "Workbench composition preserving Crucible, Strata, Atlas, and profile authority",
            "writes-output",
            "inert-only",
            _workbench_template("cockpit", "compare"),
            compare_fields,
            doc,
            availability="experimental",
        ),
        CommandSpec(
            "world-studio.cockpit-show",
            "world-studio",
            "Show Worldgen Cockpit report",
            "Render a retained content-addressed cockpit report as a terminal decision surface.",
            "Workbench read-only presentation",
            "read-only",
            "none",
            _workbench_template("cockpit", "show"),
            (
                _f("report", "Retained cockpit report.", flags=("--report",), kind="path", required=True),
                _f("json", "Emit complete report JSON.", flags=("--json",), kind="boolean"),
                _f("sources", "Show exact source bindings.", flags=("--sources",), kind="boolean"),
            ),
            doc,
            availability="experimental",
        ),
        CommandSpec(
            "world-studio.cockpit-open",
            "world-studio",
            "Open Worldgen Cockpit review",
            "Open the retained self-contained aligned chunk review for one exact cockpit report.",
            "Workbench local presentation",
            "mutating",
            "inert-only",
            _workbench_template("cockpit", "open"),
            (_f("report", "Retained cockpit report.", flags=("--report",), kind="path", required=True),),
            doc,
            availability="experimental",
        ),
    ]


def _qualifier_commands() -> list[CommandSpec]:
    """Expose reproducibility qualification, evidence assessment, and risk scan."""

    doc = "docs/architecture/WORLDGEN-QUALIFIER.md"
    profile_fields = (
        _f("profile", "Explicit pack qualification profile.", flags=("--profile",), kind="choice", choices=profile_choices("qualification"), mutex_group="profile-source", required_group=True),
        _f("profile_file", "Exact qualification profile JSON.", flags=("--profile-file",), kind="path", mutex_group="profile-source", required_group=True),
    )
    qualification_fields = (
        *profile_fields,
        _f("suite", "Perturbation suite.", flags=("--suite",), kind="choice", choices=("smoke", "standard", "release"), default="smoke"),
        _f("intent", "Developer decision being qualified.", flags=("--intent",), kind="choice", choices=("development", "terrain", "caves", "ore", "decoration", "release"), default="development"),
    )
    return [
        CommandSpec(
            "world-studio.qualifier-run",
            "world-studio",
            "Run worldgen qualification",
            "Freeze one subject and execute pack-owned fresh-JVM, seed, heap, and order perturbations with fail-closed stage/domain acceptance.",
            "Workbench gate composition over Crucible experiments, Strata final state, Atlas causal evidence, and explicit pack policy",
            "mutating",
            "append-show",
            _workbench_template("qualify", "run"),
            (
                *qualification_fields,
                _f("label", "Fresh retained qualification label.", flags=("--label",)),
                _f("plan", "One byte-identical subject plan.", flags=("--plan",), kind="path"),
                _f("artifact", "One exact subject artifact.", flags=("--artifact",), kind="path"),
                _f("seeds", "Matrix seed; repeat to replace suite defaults.", flags=("--seed",), kind="integer", repeat=True),
                _f("regions", "Matrix chunk region; repeat to replace suite defaults.", flags=("--region",), repeat=True),
                _f("orders", "Pair execution order; repeat to replace suite defaults.", flags=("--order",), kind="choice", choices=("baseline-first", "candidate-first"), repeat=True),
                _f("heaps", "Heap shape; repeat to replace suite defaults.", flags=("--heap",), repeat=True),
                _f("pair_repetitions", "Independent pair repetitions per cell.", flags=("--pair-repetitions",), kind="integer"),
                _f("allow_inconclusive", "Execute despite known preflight acceptance gaps.", flags=("--allow-inconclusive",), kind="boolean"),
                _f("risk_jar", "Additional exact JAR risk surface.", flags=("--risk-jar",), kind="path", repeat=True),
                _f("runtime_template", "Disposable runtime template override.", flags=("--runtime-template",), kind="path"),
                _f("strata_root", "Strata checkout override.", flags=("--strata-root",), kind="path"),
                _f("java_cmd", "Cleanroom Java command override.", flags=("--java-cmd",)),
                _f("gradle_cmd", "Gradle command override.", flags=("--gradle-cmd",)),
                _f("diagnostic_sample_modulo", "Diagnostic sample modulo.", flags=("--diagnostic-sample-modulo",), kind="integer"),
                _f("startup_timeout", "Server startup timeout seconds.", flags=("--startup-timeout",), kind="integer", default=300),
                _f("scan_timeout", "Capture timeout seconds.", flags=("--scan-timeout",), kind="integer", default=600),
                _f("stop_timeout", "Server stop timeout seconds.", flags=("--stop-timeout",), kind="integer", default=60),
                _f("open", "Open the retained decision review.", flags=("--open",), kind="boolean"),
                _f("show", "Show the inert matrix plan.", flags=("--show",), kind="boolean", mutex_group="preview", console_managed=True),
                _f("json", "Emit the inert matrix plan as JSON.", flags=("--json",), kind="boolean", mutex_group="preview"),
            ),
            doc,
            availability="experimental",
            limitations=("Release intent fails closed while traversal-order, settled-tick, warm-cache, or required stage evidence is unsupported.",),
        ),
        CommandSpec(
            "world-studio.qualifier-assess",
            "world-studio",
            "Assess completed A/A controls",
            "Apply domain, evidence, perturbation, and static-risk gates to completed byte-identical Worldgen Cockpit controls.",
            "Workbench acceptance policy preserving upstream evidence authority",
            "writes-output",
            "inert-only",
            _workbench_template("qualify", "assess"),
            (
                *qualification_fields,
                _f("cockpit_report", "Completed A/A Cockpit report.", flags=("--cockpit-report",), kind="path", repeat=True, required=True),
                _f("risk_jar", "Additional exact JAR risk surface.", flags=("--risk-jar",), kind="path", repeat=True),
                _f("output", "Fresh retained qualification report.", flags=("--output",), kind="path"),
                _f("json", "Emit complete qualification JSON.", flags=("--json",), kind="boolean"),
                _f("sources", "Show exact source bindings.", flags=("--sources",), kind="boolean"),
            ),
            doc,
            availability="experimental",
        ),
        CommandSpec(
            "world-studio.qualifier-scan",
            "world-studio",
            "Scan generator edge-case risks",
            "Screen exact JAR class surfaces for unordered RNG selection, identity ordering, entropy, filesystem/reflection ordering, async execution, and GC-sensitive caches.",
            "Workbench bounded static risk screening; never causal proof",
            "writes-output",
            "inert-only",
            _workbench_template("qualify", "scan"),
            (
                *profile_fields,
                _f("jar", "Exact JAR to scan.", flags=("--jar",), kind="path", repeat=True, required=True),
                _f("output", "Fresh retained static-risk report.", flags=("--output",), kind="path"),
                _f("json", "Emit complete static-risk JSON.", flags=("--json",), kind="boolean"),
            ),
            doc,
            availability="experimental",
        ),
        CommandSpec(
            "world-studio.qualifier-show",
            "world-studio",
            "Show worldgen qualification",
            "Render a retained qualification as a terminal acceptance surface.",
            "Workbench read-only presentation",
            "read-only",
            "none",
            _workbench_template("qualify", "show"),
            (
                _f("report", "Retained qualification report.", flags=("--report",), kind="path", required=True),
                _f("json", "Emit complete report JSON.", flags=("--json",), kind="boolean"),
                _f("sources", "Show exact source bindings.", flags=("--sources",), kind="boolean"),
            ),
            doc,
            availability="experimental",
        ),
        CommandSpec(
            "world-studio.qualifier-open",
            "world-studio",
            "Open worldgen qualification",
            "Open the retained self-contained gate, matrix, and risk review.",
            "Workbench local presentation",
            "mutating",
            "inert-only",
            _workbench_template("qualify", "open"),
            (_f("report", "Retained qualification report.", flags=("--report",), kind="path", required=True),),
            doc,
            availability="experimental",
        ),
    ]


def _subsurface_commands() -> list[CommandSpec]:
    """Expose every Subsurface Studio V1 query as a first-class wizard."""

    doc = "docs/architecture/GTCEU-SUBSURFACE-STUDIO.md"
    template = (
        "{python}",
        "{root}/tools/workbench.py",
        "subsurface",
        "{options:0}",
    )
    common = (
        _f(
            "profile",
            "Explicit pack profile.",
            flags=("--profile",),
            kind="choice",
            choices=profile_choices("subsurface"),
            placement=0,
            mutex_group="profile-source",
            required_group=True,
        ),
        _f(
            "profile_file",
            "Explicit Subsurface Studio pack-profile JSON.",
            flags=("--profile-file",),
            kind="path",
            placement=0,
            mutex_group="profile-source",
            required_group=True,
        ),
        _f("inventory", "Exact Crucible GTCEu inventory V1.", flags=("--inventory",), kind="path", placement=0),
        _f("impact", "Exact Crucible GTCEu impact inventory V2.", flags=("--impact",), kind="path", placement=0, mutex_group="impact-source"),
        _f("without_impact", "Deliberately run with partial definition controls.", flags=("--without-impact",), kind="boolean", placement=0, mutex_group="impact-source"),
        _f("manifest", "Exact Strata V2 region manifest.", flags=("--manifest",), kind="path", placement=0),
        _f("trace", "Optional controlled GTCEu subsurface trace V1.", flags=("--trace",), kind="path", placement=0),
        _f("require_trace", "Require controlled deposit/position attribution.", flags=("--require-trace",), kind="boolean", placement=0),
        _f("json", "Emit the exact content-addressed result JSON.", flags=("--json",), kind="boolean", placement=0),
        _f("sources", "Show exact source bindings and result identity.", flags=("--sources",), kind="boolean", placement=0),
    )

    def command(
        action: str,
        title: str,
        summary: str,
        fields: Sequence[FieldSpec] = (),
    ) -> CommandSpec:
        return CommandSpec(
            f"world-studio.subsurface-{action}",
            "world-studio",
            title,
            summary,
            "Strata final state + Crucible declarations/traces + Supersymmetry profile semantics",
            "read-only",
            "none",
            (*template, action, "{options:1}"),
            (*common, *tuple(fields)),
            doc,
            availability="experimental",
            limitations=(
                "Static definition candidates are never presented as observed deposit attribution.",
            ),
        )

    commands = [
        command("summary", "Summarize GTCEu subsurface", "Summarize exact ore, lithology, final cave-space, exposure, and virtual-fluid state."),
        command("layers", "List subsurface layers", "List every V1 map layer with its evidence state and practical boundary."),
        command(
            "map",
            "Map GTCEu subsurface",
            "Render a deterministic chunk map for ore, lithology, cave-space, exposure, height, biome, or fluid yield.",
            (
                _f("layer", "Layer to render.", flags=("--layer",), kind="choice", choices=("ore-blocks", "ore-materials", "lithology", "subsurface-air", "ore-exposure", "surface-indicators", "height", "biome", "fluid-yield", "ore-attempts"), required=True, placement=1),
                _f("material", "Optional exact ore material token.", flags=("--material",), placement=1),
            ),
        ),
        command(
            "explain",
            "Explain one subsurface block",
            "Join exact final block, lithology, biome, cave proximity, eligible definitions, and matching controlled decisions.",
            (
                _f("position", "Exact world X,Y,Z.", flags=("--position",), kind="integer", nargs=3, required=True, placement=1),
                _f("material", "Optional material question at this coordinate.", flags=("--material",), placement=1),
                _f("cave_radius", "Nearest subsurface-air radius, 0..32.", flags=("--cave-radius",), kind="integer", default=8, placement=1),
            ),
        ),
        command(
            "section",
            "Render a vertical subsurface section",
            "Render an exact bounded ore, lithology, and final cave-space plane in the terminal.",
            (
                _f("x", "Fixed world X; horizontal axis is Z.", flags=("--x",), kind="integer", placement=1, mutex_group="section-plane", required_group=True),
                _f("z", "Fixed world Z; horizontal axis is X.", flags=("--z",), kind="integer", placement=1, mutex_group="section-plane", required_group=True),
                _f("min_y", "Minimum inclusive Y.", flags=("--min-y",), kind="integer", default=0, placement=1),
                _f("max_y", "Maximum inclusive Y.", flags=("--max-y",), kind="integer", default=255, placement=1),
                _f("min_axis", "Minimum inclusive horizontal coordinate.", flags=("--min-axis",), kind="integer", placement=1),
                _f("max_axis", "Maximum inclusive horizontal coordinate.", flags=("--max-axis",), kind="integer", placement=1),
                _f("material", "Highlight one ore material.", flags=("--material",), placement=1),
            ),
        ),
        command(
            "definitions",
            "Inspect GTCEu ore definitions",
            "Search exact version-bound ore controls while keeping selection unobserved.",
            (
                _f("material", "Exact ore material token.", flags=("--material",), placement=1),
                _f("query", "Case-insensitive path/name/material search.", flags=("--query",), placement=1),
            ),
        ),
        command("fluids", "Inspect GTCEu bedrock fluids", "Inspect exact virtual fluid cells separately from physical underground blocks."),
        command(
            "compare",
            "Compare aligned subsurface captures",
            "Compare definition, ore, lithology, cave-space, exposure, biome, height, and virtual-fluid changes for one aligned scope.",
            (
                _f("baseline_manifest", "Exact baseline Strata V2 manifest.", flags=("--baseline-manifest",), kind="path", required=True, placement=1),
                _f("baseline_inventory", "Exact baseline Crucible GTCEu inventory.", flags=("--baseline-inventory",), kind="path", placement=1),
                _f("baseline_impact", "Exact baseline impact inventory.", flags=("--baseline-impact",), kind="path", placement=1, mutex_group="baseline-impact-source"),
                _f("baseline_without_impact", "Deliberately omit baseline impact controls.", flags=("--baseline-without-impact",), kind="boolean", placement=1, mutex_group="baseline-impact-source"),
                _f("baseline_trace", "Optional baseline controlled trace.", flags=("--baseline-trace",), kind="path", placement=1),
                _f("material", "Compare one ore material token.", flags=("--material",), placement=1),
            ),
        ),
    ]
    commands.append(
        CommandSpec(
            "world-studio.gtceu-trace-assemble",
            "world-studio",
            "Assemble GTCEu subsurface trace",
            "Validate observed GTCEu selection/placement records into one fresh content-addressed controlled trace.",
            "Crucible controlled GTCEu observation",
            "writes-output",
            "inert-only",
            (
                "{python}",
                "{root}/modules/crucible/tools/assemble_gtceu_subsurface_trace.py",
                "{options:0}",
            ),
            (
                _f("input", "Closed observed trace-input JSON.", flags=("--input",), kind="path", required=True),
                _f("output", "Fresh trace output under ignored storage.", flags=("--output",), kind="path", required=True),
            ),
            "modules/crucible/contracts/gtceu-subsurface-trace-v1.md",
            availability="experimental",
        )
    )
    return commands


def _expert_commands() -> list[CommandSpec]:
    atlas_script = "{root}/modules/atlas/tools/query_worldgen_observatory.py"
    mixin_script = "{root}/tools/inspect_mixin_artifacts.py"
    doctor_script = "{root}/profiles/platforms/cleanroom/tools/run_mixin_doctor.py"
    common_atlas = (
        _f("bundle", "Sealed admitted Observatory bundle.", flags=("--bundle",), kind="path", required=True, placement=0),
        _f("output", "Fresh Atlas answer output path.", flags=("--output",), kind="path", required=True, placement=0),
    )
    commands = [
        CommandSpec("atlas.who-wrote-block", "atlas", "Who wrote this block?", "Ask Atlas for observed logical writes at one exact position in a sealed bundle.", "Atlas observed/derived knowledge", "writes-output", "inert-only", ("{python}", atlas_script, "{options:0}", "who-wrote-block", "{options:1}"), (*common_atlas, _f("dimension_id", "Dimension ID.", flags=("--dimension-id",), kind="integer", required=True, placement=1), _f("position", "Exact x,y,z position.", flags=("--position",), kind="integer", nargs=3, required=True, placement=1)), "docs/architecture/WORLDGEN-OBSERVATORY.md", availability="experimental"),
        CommandSpec("atlas.which-handler-changed-event", "atlas", "Which handler changed this event?", "Ask Atlas to interpret one exact observed Forge event-post span.", "Atlas observed/derived knowledge", "writes-output", "inert-only", ("{python}", atlas_script, "{options:0}", "which-handler-changed-event", "{options:1}"), (*common_atlas, _f("event_span_id", "Exact event span ID.", flags=("--event-span-id",), required=True, placement=1)), "docs/architecture/WORLDGEN-OBSERVATORY.md", availability="experimental"),
        CommandSpec("mixin.inspect-topology", "mixin", "Inspect Mixin topology", "Inspect exact archive bytes for Mixin component topology without loading archive code.", "Project Intelligence static observation", "writes-output", "none", ("{python}", mixin_script, "{options:0}"), (_f("artifacts", "One or more exact JAR/ZIP paths.", positional=True, kind="path", required=True, nargs="one_or_more"), _f("compact", "Emit compact canonical JSON.", flags=("--compact",), kind="boolean"), _f("output", "Atomic output path instead of stdout.", flags=("--output",), kind="path")), "docs/architecture/MIXIN-OBSERVABILITY.md", availability="experimental"),
        CommandSpec("mixin.cleanroom-doctor", "mixin", "Cleanroom Mixin Doctor", "Evaluate exact archive bytes with the bound experimental Cleanroom Mixin policy.", "Cleanroom profile policy over Project Intelligence facts", "writes-output", "none", ("{python}", doctor_script, "{options:0}"), (_f("artifacts", "One or more exact JAR/ZIP paths.", positional=True, kind="path", required=True, nargs="one_or_more"), _f("policy", "Exact Cleanroom Mixin policy JSON.", flags=("--policy",), kind="path"), _f("compact", "Emit compact canonical JSON.", flags=("--compact",), kind="boolean"), _f("output", "Atomic output path instead of stdout.", flags=("--output",), kind="path"), _f("fail_on", "Disposition threshold for exit 1.", flags=("--fail-on",), kind="choice", choices=("none", "reject", "review"), default="none")), "docs/architecture/MIXIN-OBSERVABILITY.md", availability="experimental"),
    ]
    commands.extend(_atlas_runtime_commands())
    commands.extend(_atlas_observatory_commands())
    commands.extend(_mixin_engineering_commands())
    commands.extend(_mixin_evidence_commands())
    commands.extend(_crucible_lab_commands())
    commands.extend(_world_studio_diagnostics_commands())
    commands.extend(_crucible_evidence_commands())
    commands.extend(_blueprints_commands())
    return commands


def _atlas_runtime_commands() -> list[CommandSpec]:
    runtime_script = "{root}/modules/atlas/src/workbench_atlas/runtime_graph.py"
    corpus_script = "{root}/modules/atlas/src/workbench_atlas/corpus_bridge.py"
    runtime_doc = "modules/atlas/contracts/unified-process-atlas-v1.md"

    def runtime(
        action: str,
        title: str,
        summary: str,
        fields: Sequence[FieldSpec] = (),
        *,
        risk: str = "read-only",
        availability: str = "experimental",
        limitations: Sequence[str] = (),
    ) -> CommandSpec:
        return CommandSpec(
            f"atlas.runtime-{action}",
            "atlas",
            title,
            summary,
            "Atlas runtime-graph authority",
            risk,
            "inert-only" if risk != "read-only" else "none",
            ("{python}", runtime_script, action, "{options:0}"),
            tuple(fields),
            runtime_doc,
            availability=availability,
            limitations=tuple(limitations),
        )

    domain = (
        _f("database", "Normalized Atlas SQLite database.", positional=True, kind="path", required=True),
        _f("key_kind", "Exact normalized node-key kind.", flags=("--key-kind",), required=True),
        _f("key_value", "Exact normalized node-key value.", flags=("--key-value",), required=True),
        _f("kind", "Expected runtime node kind.", flags=("--kind",), required=True),
        _f("scope", "Exact PROFILE:PHYSICAL_SIDE scope; repeatable.", flags=("--scope",), required=True, repeat=True),
    )
    commands = [
        runtime("machine-recipes", "Query machine recipes", "Query a normalized runtime database for one machine's recipes and slot alternatives.", (_f("database", "Normalized Atlas SQLite database.", positional=True, kind="path", required=True), _f("machine", "Exact machine identity.", positional=True, required=True), _f("limit", "Maximum results.", flags=("--limit",), kind="integer", default=100), _f("offset", "Result offset.", flags=("--offset",), kind="integer", default=0))),
        runtime("producers", "Query exact producers", "Query exact scope-bounded mechanical producer occurrences.", (*domain, _f("limit", "Maximum results.", flags=("--limit",), kind="integer", default=100), _f("offset", "Result offset.", flags=("--offset",), kind="integer", default=0))),
        runtime("consumers", "Query exact consumers", "Query exact scope-bounded mechanical consumer occurrences.", (*domain, _f("limit", "Maximum results.", flags=("--limit",), kind="integer", default=100), _f("offset", "Result offset.", flags=("--offset",), kind="integer", default=0), _f("predicate", "Consumer predicate; repeatable.", flags=("--predicate",), repeat=True))),
        runtime("process-chain", "Build process-chain route", "Build one bounded route DAG for an exact target without pretending truncation is completeness.", (*domain, _f("max_depth", "Maximum route depth.", flags=("--max-depth",), kind="integer", default=8), _f("max_routes", "Maximum route count.", flags=("--max-routes",), kind="integer", default=50), _f("max_alternatives_per_slot", "Maximum alternatives per slot.", flags=("--max-alternatives-per-slot",), kind="integer", default=25), _f("max_visited_nodes", "Maximum visited nodes.", flags=("--max-visited-nodes",), kind="integer", default=10000), _f("exclude_chanced_outputs", "Exclude explicit chanced outputs.", flags=("--exclude-chanced-outputs",), kind="boolean"), _f("exclude_procedural_rules", "Exclude procedural producer rules.", flags=("--exclude-procedural-rules",), kind="boolean"))),
    ]

    def corpus(
        action: str,
        title: str,
        summary: str,
        fields: Sequence[FieldSpec],
    ) -> CommandSpec:
        return CommandSpec(
            f"atlas.{action}", "atlas", title, summary,
            "Unified Process Atlas V1 composition", "read-only", "none",
            ("{python}", corpus_script, action, "{options:0}"), tuple(fields),
            runtime_doc, availability="experimental",
        )

    corpus_common = (
        _f("runtime_database", "Normalized runtime database.", flags=("--runtime-database",), kind="path", required=True),
        _f("catalog_root", "Curated knowledge catalog root.", flags=("--catalog-root",), kind="path", required=True),
        _f("questions", "Acceptance questions JSON.", flags=("--questions",), kind="path"),
        _f("links", "Curated links JSONL.", flags=("--links",), kind="path", required=True),
        _f("quest_nodes", "Quest nodes JSONL.", flags=("--quest-nodes",), kind="path", required=True),
        _f("quest_edges", "Quest edges JSONL.", flags=("--quest-edges",), kind="path", required=True),
    )
    commands.extend(
        [
            corpus("answer", "Answer an Atlas acceptance question", "Compose one exact evidence-indexed acceptance-question answer.", (*corpus_common, _f("question_id", "Exact question ID.", flags=("--question-id",), required=True), _f("snapshot_id", "Exact snapshot ID.", flags=("--snapshot-id",), required=True), _f("scope", "Exact PROFILE:PHYSICAL_SIDE; repeatable.", flags=("--scope",), required=True, repeat=True), _f("max_depth", "Route depth bound.", flags=("--max-depth",), kind="integer", default=8), _f("max_routes", "Route count bound.", flags=("--max-routes",), kind="integer", default=50), _f("max_alternatives_per_slot", "Alternative bound.", flags=("--max-alternatives-per-slot",), kind="integer", default=25), _f("max_visited_nodes", "Visited-node bound.", flags=("--max-visited-nodes",), kind="integer", default=10000), _f("recycling_max_depth", "Recycling depth bound.", flags=("--recycling-max-depth",), kind="integer", default=4), _f("recycling_max_operations", "Recycling operation bound.", flags=("--recycling-max-operations",), kind="integer", default=250), _f("recycling_max_consumers_per_target", "Recycling consumers per target.", flags=("--recycling-max-consumers-per-target",), kind="integer", default=25), _f("recycling_max_output_alternatives_per_slot", "Recycling output alternative bound.", flags=("--recycling-max-output-alternatives-per-slot",), kind="integer", default=25), _f("recycling_max_visited_targets", "Recycling visited-target bound.", flags=("--recycling-max-visited-targets",), kind="integer", default=10000), _f("construction_max_depth", "Construction depth bound.", flags=("--construction-max-depth",), kind="integer", default=8), _f("construction_max_routes", "Construction route bound.", flags=("--construction-max-routes",), kind="integer", default=250), _f("construction_max_alternatives_per_slot", "Construction alternatives bound.", flags=("--construction-max-alternatives-per-slot",), kind="integer", default=25), _f("construction_max_visited_nodes", "Construction visited-node bound.", flags=("--construction-max-visited-nodes",), kind="integer", default=10000), _f("construction_max_machine_roots", "Construction machine-root bound.", flags=("--construction-max-machine-roots",), kind="integer"), _f("exclude_chanced_outputs", "Exclude explicit chanced outputs.", flags=("--exclude-chanced-outputs",), kind="boolean"), _f("exclude_procedural_rules", "Exclude procedural producer rules.", flags=("--exclude-procedural-rules",), kind="boolean"))),
            corpus("query", "Run parameterized Atlas query", "Compose one explicit target-parameterized Atlas query.", (*corpus_common, _f("query_instance", "Exact query-instance JSON.", flags=("--query-instance",), kind="path", required=True))),
            corpus("continuation-start", "Start Atlas continuation", "Start one opt-in portable route or recycling continuation.", (_f("runtime_database", "Normalized runtime database.", flags=("--runtime-database",), kind="path", required=True), _f("query_instance", "Exact query-instance JSON.", flags=("--query-instance",), kind="path", required=True), _f("evidence", "Canonical evidence array.", flags=("--evidence",), kind="path", required=True), _f("kind", "Continuation kind.", flags=("--kind",), kind="choice", choices=("route", "recycling"), required=True), _f("work_items", "Bounded work item count.", flags=("--work-items",), kind="integer", required=True), _f("route_manifest", "Complete route manifest for recycling.", flags=("--route-manifest",), kind="path"), _f("questions", "Questions JSON.", flags=("--questions",), kind="path"), _f("capability_policy", "Capability policy JSON.", flags=("--capability-policy",), kind="path"))),
            corpus("continuation-resume", "Resume Atlas continuation", "Resume one exact portable continuation manifest.", (_f("runtime_database", "Normalized runtime database.", flags=("--runtime-database",), kind="path", required=True), _f("manifest", "Continuation manifest.", flags=("--manifest",), kind="path", required=True), _f("work_items", "Additional bounded work item count.", flags=("--work-items",), kind="integer", required=True))),
            corpus("continuation-compose", "Compose Atlas continuation", "Compose and validate one exact parent/delta pair.", (_f("parent", "Parent manifest.", flags=("--parent",), kind="path", required=True), _f("delta", "Delta manifest.", flags=("--delta",), kind="path", required=True))),
            corpus("continuation-invalidate", "Invalidate Atlas continuation", "Close an active lineage with explicit reasons.", (_f("parent", "Parent manifest.", flags=("--parent",), kind="path", required=True), _f("reason", "Explicit invalidation reason; repeatable.", flags=("--reason",), required=True, repeat=True))),
        ]
    )
    return commands


def _atlas_observatory_commands() -> list[CommandSpec]:
    script = "{root}/modules/atlas/tools/query_worldgen_observatory_exact.py"
    fields = (
        _f("primary_bundle", "Primary admitted Observatory bundle.", flags=("--primary-bundle",), kind="path", required=True),
        _f("comparison_bundle", "Comparison admitted Observatory bundle.", flags=("--comparison-bundle",), kind="path", required=True),
        _f("crash_bundle", "Crash admitted Observatory bundle.", flags=("--crash-bundle",), kind="path", required=True),
        _f("dimension_id", "Dimension ID.", flags=("--dimension-id",), kind="integer", required=True),
        _f("block_position", "Exact x,y,z block position.", flags=("--block-position",), kind="integer", nargs=3, required=True),
        _f("event_span_id", "Exact event-post span identity.", flags=("--event-span-id",)),
        _f("event_class", "Exact Forge event class.", flags=("--event-class",)),
        _f("event_bus_id", "Exact observed event-bus identity.", flags=("--event-bus-id",)),
        _f("event_chunk", "Event chunk x,z.", flags=("--event-chunk",), kind="integer", nargs=2),
        _f("event_occurrence", "Zero-based event occurrence.", flags=("--event-occurrence",), kind="integer"),
        _f("comparison_scope_sha256", "Exact comparison-scope digest.", flags=("--comparison-scope-sha256",)),
        _f("comparison_checkpoint_id", "Exact comparison checkpoint.", flags=("--comparison-checkpoint-id",)),
        _f("comparison_chunk", "Comparison chunk x,z.", flags=("--comparison-chunk",), kind="integer", nargs=2),
        _f("expect_divergence_status", "Expected bounded comparison result.", flags=("--expect-divergence-status",), kind="choice", choices=("equal", "diverged", "either")),
        _f("expected_writer_mod_id", "Expected observed writer mod ID.", flags=("--expected-writer-mod-id",)),
        _f("expected_handler_mod_id", "Expected observed handler mod ID.", flags=("--expected-handler-mod-id",)),
        _f("scratch_directory", "Fresh ignored scratch directory.", flags=("--scratch-directory",), kind="path"),
        _f("output", "Fresh exact-suite answer output.", flags=("--output",), kind="path", required=True),
    )
    return [
        CommandSpec(
            "atlas.worldgen-exact-suite",
            "atlas",
            "Run exact Observatory query suite",
            "Run the bound writer, handler, first-divergence, and crash queries over three admitted bundles.",
            "Atlas observed and derived Worldgen Observatory knowledge",
            "writes-output",
            "inert-only",
            ("{python}", script, "{options:0}"),
            fields,
            "docs/architecture/WORLDGEN-OBSERVATORY.md",
            availability="experimental",
        )
    ]


def _mixin_engineering_commands() -> list[CommandSpec]:
    doc = "docs/architecture/MIXIN-OBSERVABILITY.md"
    return [
        CommandSpec("mixin.ap-compatibility", "mixin", "Inspect packaged AP compatibility", "Publish an exact packaged Mixin AP compatibility conformance receipt.", "Project Intelligence artifact and policy conformance", "writes-output", "inert-only", ("{python}", "{root}/tools/inspect_mixin_ap_compatibility.py", "{options:0}"), (_f("artifact", "Exact artifact path.", positional=True, kind="path", required=True), _f("policy", "Exact AP compatibility policy.", flags=("--policy",), kind="path", required=True), _f("output", "Fresh receipt output.", flags=("--output",), kind="path", required=True), _f("compact", "Emit compact canonical JSON.", flags=("--compact",), kind="boolean"), _f("allow_nonconformant", "Return success while preserving nonconformance in the receipt.", flags=("--allow-nonconformant",), kind="boolean")), doc, availability="experimental"),
        CommandSpec("mixin.dependency-closure", "mixin", "Inspect Mixin dependency closure", "Publish a complete-or-explicitly-incomplete dependency and class-header closure receipt.", "Project Intelligence static closure", "writes-output", "inert-only", ("{python}", "{root}/tools/inspect_mixin_dependency_closure.py", "{options:0}"), (_f("spec", "Strict caller-owned closure input spec.", flags=("--spec",), kind="path", required=True), _f("output", "Fresh closure receipt output.", flags=("--output",), kind="path", required=True), _f("compact", "Emit compact canonical JSON.", flags=("--compact",), kind="boolean")), doc, availability="experimental"),
        CommandSpec("mixin.compiler-ap-receipt", "mixin", "Assemble compiler/AP custody receipt", "Bind caller-declared compiler and annotation-processor inputs and outputs without claiming reproducible execution.", "Project Intelligence invocation custody", "writes-output", "inert-only", ("{python}", "{root}/tools/assemble_mixin_compiler_ap_build_receipt.py", "{options:0}"), (_f("input_spec", "Compiler/AP invocation input spec.", positional=True, kind="path", required=True), _f("policy", "Exact compiler/AP policy.", flags=("--policy",), kind="path", required=True), _f("output", "Fresh custody receipt output.", flags=("--output",), kind="path", required=True), _f("compact", "Emit compact canonical JSON.", flags=("--compact",), kind="boolean"), _f("allow_partial", "Return success while preserving partial custody state.", flags=("--allow-partial",), kind="boolean")), doc, availability="experimental"),
    ]


def _mixin_evidence_commands() -> list[CommandSpec]:
    profile_tools = "{root}/profiles/platforms/cleanroom/tools"
    crucible_tools = "{root}/modules/crucible/tools"
    doc = "docs/architecture/MIXIN-OBSERVABILITY.md"

    def write_command(
        command_id: str,
        title: str,
        summary: str,
        script: str,
        fields: Sequence[FieldSpec],
        authority: str,
    ) -> CommandSpec:
        return CommandSpec(
            command_id,
            "mixin",
            title,
            summary,
            authority,
            "writes-output",
            "inert-only",
            ("{python}", script, "{options:0}"),
            tuple(fields),
            doc,
            availability="experimental",
        )

    side = ("client", "dedicated_server", "integrated_server")
    return [
        write_command(
            "mixin.import-runtime-service",
            "Import CleanMix runtime-service observations",
            "Import exact Cleanroom/CleanMix service reports without inferring provider uniqueness.",
            profile_tools + "/import_cleanmix_runtime_service.py",
            (
                _f("debug_log", "Exact debug.log when captured.", flags=("--debug-log",), kind="path"),
                _f("latest_log", "Exact latest.log when captured.", flags=("--latest-log",), kind="path"),
                _f("cleanmix_artifact", "Exact CleanMix artifact.", flags=("--cleanmix-artifact",), kind="path", required=True),
                _f("toolchain_lock", "Exact runtime toolchain lock.", flags=("--toolchain-lock",), kind="path", required=True),
                _f("component_topology_receipt", "Bound component topology receipt.", flags=("--component-topology-receipt",), kind="path", required=True),
                _f("output", "Fresh imported observation output.", flags=("--output",), kind="path", required=True),
                _f("session_id", "Exact session identity.", flags=("--session-id",), required=True),
                _f("launch_id", "Exact launch identity.", flags=("--launch-id",), required=True),
                _f("profile_id", "Exact profile identity.", flags=("--profile-id",), required=True),
                _f("side", "Physical launch side.", flags=("--side",), kind="choice", choices=side, required=True),
            ),
            "Cleanroom profile importer over exact runtime observations",
        ),
        write_command(
            "mixin.import-provider-enumeration",
            "Import Mixin service-provider enumeration",
            "Bind exact Java provider-enumeration probe evidence to its base observation set.",
            profile_tools + "/import_mixin_service_provider_enumeration.py",
            (
                _f("base_observation_set", "Base runtime-service observation set.", flags=("--base-observation-set",), kind="path", required=True),
                _f("probe_evidence", "Exact provider-enumeration probe evidence.", flags=("--probe-evidence",), kind="path", required=True),
                _f("toolchain_lock", "Exact runtime toolchain lock.", flags=("--toolchain-lock",), kind="path", required=True),
                _f("provider_artifact", "Provider artifact; repeatable.", flags=("--provider-artifact",), kind="path", repeat=True),
                _f("output", "Fresh imported observation output.", flags=("--output",), kind="path", required=True),
            ),
            "Cleanroom profile importer over exact provider enumeration",
        ),
        write_command(
            "mixin.import-defining-loader-trace",
            "Import defining-loader discovery trace",
            "Bind one raw defining-loader trace to its exact candidate, toolchain, artifacts, launch, and fixture result.",
            profile_tools + "/import_cleanmix_defining_loader_discovery_trace.py",
            tuple(
                _f(key, help_text, flags=(flag,), kind="path", required=True)
                for key, flag, help_text in (
                    ("raw_trace", "--raw-trace", "Exact raw defining-loader trace."),
                    ("candidate_lock", "--candidate-lock", "Exact Cleanroom candidate lock."),
                    ("toolchain_lock", "--toolchain-lock", "Exact runtime toolchain lock."),
                    ("observer_agent", "--observer-agent", "Exact observer agent artifact."),
                    ("mixin_engine_artifact", "--mixin-engine-artifact", "Exact Mixin engine artifact."),
                    ("selected_provider_artifact", "--selected-provider-artifact", "Exact selected provider artifact."),
                    ("launch_log", "--launch-log", "Exact retained launch log."),
                    ("fixture_result", "--fixture-result", "Exact fixture result."),
                )
            )
            + (
                _f("launch_id", "Exact launch identity.", flags=("--launch-id",), required=True),
                _f("output", "Fresh imported trace output.", flags=("--output",), kind="path", required=True),
            ),
            "Cleanroom profile importer over defining-loader evidence",
        ),
        write_command(
            "mixin.import-cleanmix-audit",
            "Import CleanMix audit observations",
            "Import exact CleanMix audit events with explicit launch state and component custody.",
            profile_tools + "/import_cleanmix_audit.py",
            (
                _f("audit", "Exact CleanMix audit stream.", flags=("--audit",), kind="path", required=True),
                _f("output", "Fresh imported audit output.", flags=("--output",), kind="path", required=True),
                _f("session_id", "Exact session identity.", flags=("--session-id",), required=True),
                _f("launch_id", "Exact launch identity.", flags=("--launch-id",), required=True),
                _f("profile_id", "Exact profile identity.", flags=("--profile-id",), required=True),
                _f("side", "Physical launch side.", flags=("--side",), kind="choice", choices=side, required=True),
                _f("component_receipt_sha256", "Bound component receipt SHA-256.", flags=("--component-receipt-sha256",), required=True),
                _f("launch_state", "Observed launch terminal state.", flags=("--launch-state",), kind="choice", choices=("complete", "crashed", "incomplete"), required=True),
                _f("owner_bindings", "Optional exact owner-binding input.", flags=("--owner-bindings",), kind="path"),
            ),
            "Cleanroom profile importer over exact CleanMix audit events",
        ),
        write_command(
            "mixin.assemble-runtime-service",
            "Assemble Mixin runtime-service receipt",
            "Build the fail-closed Crucible runtime-service receipt from one closed input spec.",
            crucible_tools + "/assemble_mixin_runtime_service_receipt.py",
            (
                _f("input", "Closed runtime-service input spec.", flags=("--input",), kind="path", required=True),
                _f("output", "Fresh receipt output.", flags=("--output",), kind="path", required=True),
            ),
            "Crucible Mixin runtime evidence",
        ),
        write_command(
            "mixin.assemble-transformation-ledger",
            "Assemble Mixin transformation ledger",
            "Build a fail-closed transformation ledger without inferring missing applications.",
            crucible_tools + "/assemble_mixin_transformation_ledger.py",
            (
                _f("input", "Closed transformation-ledger input spec.", flags=("--input",), kind="path", required=True),
                _f("output", "Fresh ledger output.", flags=("--output",), kind="path", required=True),
            ),
            "Crucible Mixin transformation evidence",
        ),
    ]


def _crucible_lab_commands() -> list[CommandSpec]:
    strata = "{root}/modules/crucible/tools/run_strata_observation.py"
    return [
        CommandSpec("world-studio.strata-observe", "world-studio", "Run checked Strata observation", "Capture or reuse an exact dense scan, validate its manifest, smoke-render, and publish a checked viewer handoff.", "Crucible controlled observation and external-tool custody", "mutating", "inert-only", ("{python}", strata, "{options:0}"), (_f("strata_root", "Exact Strata checkout.", flags=("--strata-root",), kind="path"), _f("runtime", "Managed runtime under .workbench.", flags=("--runtime",), kind="path", required=True), _f("server_jar", "Exact server JAR override.", flags=("--server-jar",), kind="path"), _f("scan", "Existing exact dense scan to reuse.", flags=("--scan",), kind="path"), _f("java_cmd", "Runtime Java command.", flags=("--java-cmd",), default="java"), _f("observer_java_home", "Observer Java home.", flags=("--observer-java-home",)), _f("observer_compiler_java_home", "Observer compiler Java home.", flags=("--observer-compiler-java-home",)), _f("dimension", "Dimension ID.", flags=("--dimension",), kind="integer", default=0), _f("min_chunk_x", "Minimum chunk X.", flags=("--min-chunk-x",), kind="integer", default=-2), _f("min_chunk_z", "Minimum chunk Z.", flags=("--min-chunk-z",), kind="integer", default=-2), _f("chunk_size_x", "Chunk width.", flags=("--chunk-size-x",), kind="integer", default=4), _f("chunk_size_z", "Chunk height.", flags=("--chunk-size-z",), kind="integer", default=4), _f("halo_chunks", "Halo chunks.", flags=("--halo-chunks",), kind="integer", default=1), _f("tile_size", "Shard tile size.", flags=("--tile-size",), kind="integer", default=2), _f("sample_profile", "Observation sample profile.", flags=("--sample-profile",), kind="choice", choices=("custom", "micro-region"), default="custom"), _f("manifest_version", "Strata manifest version.", flags=("--manifest-version",), kind="choice", choices=("1", "2")), _f("heap", "Runtime heap.", flags=("--heap",), default="2048M"), _f("startup_timeout", "Startup timeout seconds.", flags=("--startup-timeout",), kind="integer", default=300), _f("scan_timeout", "Scan timeout seconds.", flags=("--scan-timeout",), kind="integer", default=420), _f("stop_timeout", "Stop timeout seconds.", flags=("--stop-timeout",), kind="integer", default=60), _f("label", "Fresh observation label.", flags=("--label",)), _f("jvm_arg", "Additional JVM argument; repeatable.", flags=("--jvm-arg",), repeat=True), _f("no_render_smoke", "Skip the render smoke explicitly.", flags=("--no-render-smoke",), kind="boolean"), _f("viewer_port", "Viewer port.", flags=("--viewer-port",), kind="integer", default=5173), _f("output_root", "Explicit output root under managed storage.", flags=("--output-root",), kind="path")), "docs/architecture/WORLDGEN-ITERATION-RUNNER.md", availability="experimental"),
        CommandSpec("world-studio.strata-check", "world-studio", "Check Strata viewer handoff", "Validate and print one exact viewer handoff without starting a server.", "Crucible checked external-tool handoff", "read-only", "none", ("{python}", "{root}/modules/crucible/tools/serve_strata_observation.py", "{options:0}", "--check"), (_f("handoff", "Exact viewer-handoff JSON.", positional=True, kind="path", required=True),), "docs/architecture/WORLDGEN-ITERATION-RUNNER.md", availability="experimental"),
        CommandSpec("world-studio.strata-serve", "world-studio", "Serve Strata viewer", "Validate and start the exact checked local viewer server until explicitly stopped.", "Crucible checked external-tool handoff", "mutating", "inert-only", ("{python}", "{root}/modules/crucible/tools/serve_strata_observation.py", "{options:0}"), (_f("handoff", "Exact viewer-handoff JSON.", positional=True, kind="path", required=True),), "docs/architecture/WORLDGEN-ITERATION-RUNNER.md", availability="experimental"),
        CommandSpec("world-studio.gtceu-inventory", "world-studio", "Inventory GTCEu worldgen", "Build an exact definition inventory with optional Strata correlation.", "Crucible GTCEu adapter observation", "writes-output", "inert-only", ("{python}", "{root}/modules/crucible/tools/inventory_gtceu_worldgen.py", "{options:0}"), (_f("jar", "Exact GTCEu JAR.", flags=("--jar",), kind="path", required=True), _f("config_root", "Exact GregTech config root.", flags=("--config-root",), kind="path", required=True), _f("strataview", "Optional exact Strata package.", flags=("--strataview",), kind="path"), _f("out", "Fresh output under .workbench.", flags=("--out",), kind="path", required=True)), "modules/crucible/README.md", availability="experimental"),
        CommandSpec("world-studio.gtceu-impact", "world-studio", "Inventory GTCEu impact", "Build the exact GTCEu configuration, runtime, Mixin, and Groovy impact inventory.", "Crucible GTCEu adapter observation", "writes-output", "inert-only", ("{python}", "{root}/modules/crucible/tools/inventory_gtceu_worldgen_impact.py", "{options:0}"), (_f("jar", "Exact GTCEu JAR.", flags=("--jar",), kind="path", required=True), _f("config_root", "Exact GregTech config root.", flags=("--config-root",), kind="path", required=True), _f("runtime_root", "Optional exact runtime root.", flags=("--runtime-root",), kind="path"), _f("strataview", "Optional exact Strata package.", flags=("--strataview",), kind="path"), _f("out", "Fresh output under .workbench.", flags=("--out",), kind="path", required=True)), "modules/crucible/README.md", availability="experimental"),
        CommandSpec("world-studio.gtceu-overlay", "world-studio", "Materialize GTCEu overlay", "Materialize an explicit overlay plan only into a fresh ignored GregTech config destination.", "Crucible GTCEu adapter construction fixture", "mutating", "inert-only", ("{python}", "{root}/modules/crucible/tools/materialize_gtceu_worldgen_overlay.py", "{options:0}"), (_f("jar", "Exact GTCEu JAR.", flags=("--jar",), kind="path", required=True), _f("config_root", "Source GregTech config root.", flags=("--config-root",), kind="path", required=True), _f("inventory", "Exact admitted inventory.", flags=("--inventory",), kind="path", required=True), _f("plan", "Explicit overlay plan.", flags=("--plan",), kind="path", required=True), _f("out_config_root", "Fresh ignored config/gregtech destination.", flags=("--out-config-root",), kind="path", required=True)), "modules/crucible/README.md", availability="experimental"),
    ]


def _world_studio_diagnostics_commands() -> list[CommandSpec]:
    tools = (
        "{root}/profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
        "worldgen-prototype-fixture/tools"
    )
    doc = "docs/architecture/WORLDGEN-ITERATION-RUNNER.md"
    return [
        CommandSpec(
            "world-studio.summarize-log", "world-studio",
            "Summarize World Studio log",
            "Summarize development-only WORLDGEN_PROTOTYPE records from one exact server log.",
            "Cleanroom candidate development diagnostics", "read-only", "none",
            ("{python}", tools + "/summarize_world_studio_log.py", "{options:0}"),
            (
                _f("log", "Exact latest.log or retained copy.", positional=True, kind="path", required=True),
                _f("compact", "Emit compact JSON.", flags=("--compact",), kind="boolean"),
            ), doc, availability="experimental",
        ),
        CommandSpec(
            "world-studio.summarize-jfr", "world-studio",
            "Summarize World Studio JFR",
            "Summarize World Studio custom events from one exact Java Flight Recording.",
            "Cleanroom candidate development diagnostics", "read-only", "none",
            ("{python}", tools + "/summarize_world_studio_jfr.py", "{options:0}"),
            (
                _f("recording", "Exact Java Flight Recording.", positional=True, kind="path", required=True),
                _f("jfr_bin", "jfr executable from the recording JDK.", flags=("--jfr-bin",), kind="path"),
                _f("compact", "Emit compact JSON.", flags=("--compact",), kind="boolean"),
            ), doc, availability="experimental",
        ),
        CommandSpec(
            "world-studio.compare-logs", "world-studio",
            "Compare fixed-seed World Studio logs",
            "Compare two exact fixed-seed logs while excluding only declared telemetry.",
            "Cleanroom candidate development diagnostics", "read-only", "none",
            ("{python}", tools + "/compare_world_studio_logs.py", "{options:0}"),
            (
                _f("baseline", "Exact baseline log.", positional=True, kind="path", required=True),
                _f("candidate", "Exact candidate log.", positional=True, kind="path", required=True),
                _f("compact", "Emit compact JSON.", flags=("--compact",), kind="boolean"),
            ), doc, availability="experimental",
        ),
    ]


def _crucible_evidence_commands() -> list[CommandSpec]:
    tools = "{root}/modules/crucible/tools"
    doc = "docs/architecture/WORLDGEN-OBSERVATORY.md"

    def path_field(
        key: str, flag: str, help_text: str, *, required: bool = True
    ) -> FieldSpec:
        return _f(
            key, help_text, flags=(flag,), kind="path", required=required
        )

    def command(
        action: str,
        title: str,
        summary: str,
        script: str,
        fields: Sequence[FieldSpec],
        *,
        prefix: Sequence[str] = (),
        risk: str = "writes-output",
        limitations: Sequence[str] = (),
    ) -> CommandSpec:
        return CommandSpec(
            f"crucible.{action}", "crucible", title, summary,
            "Crucible exact runtime and evidence custody", risk, "inert-only",
            ("{python}", tools + "/" + script, *prefix, "{options:0}"),
            tuple(fields), doc, availability="experimental",
            limitations=tuple(limitations),
        )

    commands = [
        command(
            "manifest-installed-mod-set", "Assemble installed-mod manifest",
            "Hash every installed mod in one already-staged exact server.",
            "assemble_exact_runtime_manifests.py",
            (
                path_field("server", "--server", "Exact staged server directory."),
                _f("candidate_lock_sha256", "Bound candidate-lock SHA-256.", flags=("--candidate-lock-sha256",), required=True),
                _f("runtime_class_source_sha256", "Bound runtime-class-source SHA-256.", flags=("--runtime-class-source-sha256",), required=True),
                path_field("output", "--output", "Fresh installed-mod-set manifest."),
            ), prefix=("installed-mod-set",),
        ),
        command(
            "manifest-configuration-set", "Assemble configuration manifest",
            "Hash exact configuration and bind its route and observer posture.",
            "assemble_exact_runtime_manifests.py",
            (
                path_field("server", "--server", "Exact staged server directory."),
                _f("case_id", "Exact runtime case ID.", flags=("--case-id",), required=True),
                _f("route_order", "Declared route order.", flags=("--route-order",), kind="choice", choices=("forward", "reverse"), required=True),
                _f("observer", "Declared observer posture.", flags=("--observer",), kind="choice", choices=("enabled", "disabled"), required=True),
                _f("probe_capture_id", "Optional exact probe capture ID.", flags=("--probe-capture-id",)),
                path_field("output", "--output", "Fresh configuration-set manifest."),
            ), prefix=("configuration-set",),
        ),
        command(
            "audit-runtime-session", "Audit exact runtime session",
            "Verify one captured Cleanroom session against closed caller-pinned inputs.",
            "audit_exact_runtime_session.py",
            (
                _f("case_id", "Exact runtime case ID.", flags=("--case-id",), required=True),
                _f("outcome", "Expected captured outcome.", flags=("--outcome",), kind="choice", choices=("completed", "crash"), required=True),
                path_field("case_directory", "--case-directory", "Exact captured case directory."),
                path_field("candidate_lock", "--candidate-lock", "Exact candidate lock."),
                _f("expected_candidate_lock_sha256", "Expected candidate-lock SHA-256.", flags=("--expected-candidate-lock-sha256",), required=True),
                path_field("fixture_artifact", "--fixture-artifact", "Exact fixture artifact."),
                _f("expected_fixture_artifact_sha256", "Expected fixture SHA-256.", flags=("--expected-fixture-artifact-sha256",), required=True),
                path_field("installed_mod_set_manifest", "--installed-mod-set-manifest", "Exact installed-mod-set manifest."),
                _f("expected_installed_mod_set_sha256", "Expected installed-mod-set SHA-256.", flags=("--expected-installed-mod-set-sha256",), required=True),
                path_field("configuration_set_manifest", "--configuration-set-manifest", "Exact configuration-set manifest."),
                _f("expected_configuration_set_sha256", "Expected configuration-set SHA-256.", flags=("--expected-configuration-set-sha256",), required=True),
                _f("expected_launch_log_sha256", "Expected launch-log SHA-256.", flags=("--expected-launch-log-sha256",), required=True),
                _f("expected_raw_sha256", "Expected raw probe SHA-256.", flags=("--expected-raw-sha256",)),
                _f("expected_result_sha256", "Expected result SHA-256.", flags=("--expected-result-sha256",)),
                _f("expected_runtime_inventory_sha256", "Expected runtime-inventory SHA-256.", flags=("--expected-runtime-inventory-sha256",)),
                _f("expected_semantic_result_sha256", "Expected semantic-result SHA-256.", flags=("--expected-semantic-result-sha256",)),
                path_field("output", "--output", "Fresh exact-session audit receipt."),
            ),
        ),
        command(
            "evaluate-hook-health", "Evaluate Cleanroom hook health",
            "Evaluate repeatable exact cases against one schema, probe plan, and candidate.",
            "evaluate_cleanroom_hook_health.py",
            (
                path_field("schema", "--schema", "Exact hook schema."),
                _f("schema_sha256", "Expected hook-schema SHA-256.", flags=("--schema-sha256",), required=True),
                _f("schema_id", "Exact hook-schema identity.", flags=("--schema-id",), required=True),
                path_field("probe_plan", "--probe-plan", "Exact probe plan."),
                _f("probe_plan_sha256", "Expected probe-plan SHA-256.", flags=("--probe-plan-sha256",), required=True),
                _f("probe_plan_id", "Exact probe-plan identity.", flags=("--probe-plan-id",), required=True),
                path_field("candidate_lock", "--candidate-lock", "Exact candidate lock."),
                _f("candidate_lock_sha256", "Expected candidate-lock SHA-256.", flags=("--candidate-lock-sha256",), required=True),
                _f("candidate_id", "Exact candidate identity.", flags=("--candidate-id",), required=True),
                _f("case", "CASE_ID ROLE RAW_NDJSON RAW_SHA256 SESSION_AUDIT SESSION_AUDIT_SHA256; repeatable.", flags=("--case",), nargs=6, repeat=True, required=True),
                path_field("output", "--output", "Fresh hook-health receipt."),
            ),
        ),
        command(
            "normalize-worldgen-case", "Normalize exact worldgen case",
            "Normalize one pinned Cleanroom worldgen case from a closed worker spec.",
            "run_cleanroom_worldgen_case.py",
            (path_field("spec", "--spec", "Absolute closed worker-spec JSON."),),
            limitations=("The caller-owned spec selects three output paths; review it before execution.",),
        ),
        command(
            "evaluate-worldgen-matrix", "Evaluate exact worldgen matrix",
            "Evaluate A/A, reverse-order, observer-off, and crash projections.",
            "evaluate_cleanroom_worldgen_matrix.py",
            tuple(
                path_field(key, flag, description)
                for key, flag, description in (
                    ("aa_1_projection", "--aa-1-projection", "First A/A projection."),
                    ("aa_1_result", "--aa-1-result", "First A/A fixture result."),
                    ("aa_2_projection", "--aa-2-projection", "Second A/A projection."),
                    ("aa_2_result", "--aa-2-result", "Second A/A fixture result."),
                    ("order_reverse_projection", "--order-reverse-projection", "Reverse-order projection."),
                    ("order_reverse_result", "--order-reverse-result", "Reverse-order fixture result."),
                    ("observer_off_result", "--observer-off-result", "Observer-off fixture result."),
                    ("crash_projection", "--crash-projection", "Crash projection."),
                    ("output", "--output", "Fresh matrix-evaluation output."),
                )
            ),
        ),
        command(
            "assemble-observatory-proof", "Assemble Observatory proof",
            "Assemble an exact-runtime proof from one closed content-addressed spec.",
            "assemble_worldgen_observatory_proof.py",
            (path_field("spec", "--spec", "Absolute closed proof spec."),),
            limitations=("The caller-owned proof spec selects the proof output path.",),
        ),
        command(
            "external-diagnostic-health", "Assemble diagnostic health receipt",
            "Evaluate one exact launch log using only a profile-owned literal policy.",
            "assemble_external_diagnostic_health_receipt.py",
            (
                path_field("session_audit", "--session-audit", "Exact session audit."),
                path_field("launch_log", "--launch-log", "Exact launch log."),
                path_field("policy", "--policy", "Exact profile-owned diagnostic policy."),
                path_field("output", "--output", "Fresh health receipt."),
            ),
        ),
        command(
            "foundation-bytecode-manifest", "Fingerprint Foundation bytecode",
            "Derive a comparison-only semantic manifest without replacing custody hashes.",
            "fingerprint_foundation_semantic_bytecode.py",
            (
                _f("class_dump", "Exact numbered class dump.", positional=True, kind="path", required=True),
                path_field("output", "--output", "Fresh semantic manifest."),
            ), prefix=("manifest",),
        ),
        command(
            "foundation-bytecode-compare", "Compare Foundation bytecode",
            "Compare two or more labeled semantic bytecode manifests.",
            "fingerprint_foundation_semantic_bytecode.py",
            (
                _f("manifest", "LABEL=PATH semantic manifest; repeatable.", flags=("--manifest",), required=True, repeat=True),
                path_field("output", "--output", "Fresh comparison output."),
            ), prefix=("compare",),
        ),
        command(
            "hook-catalog-write", "Rebuild worldgen hook catalog",
            "Compile and replace the selected Cleanroom hook-catalog output.",
            "generate_worldgen_hook_catalog.py",
            (
                path_field("binding", "--binding", "Exact hook binding input.", required=False),
                path_field("spec", "--spec", "Exact hook specification.", required=False),
                path_field("output", "--output", "Catalog output path.", required=False),
                path_field("source_root", "--source-root", "Optional exact Cleanroom sources.", required=False),
            ), risk="mutating",
            limitations=("Without --output the owner targets its checked-in catalog.",),
        ),
        command(
            "synthetic-observatory-fixtures", "Run synthetic Observatory fixtures",
            "Run deterministic contract fixtures without launching Cleanroom.",
            "run_worldgen_observatory_contract_fixtures.py",
            (path_field("output_root", "--output-root", "Ignored publication root.", required=False),),
            limitations=("Synthetic contract evidence is not runtime observation.",),
        ),
    ]
    commands.append(
        CommandSpec(
            "crucible.hook-catalog-check", "crucible",
            "Check worldgen hook catalog",
            "Require the selected compiled hook catalog to be byte-identical.",
            "Crucible exact hook-contract compiler", "read-only", "none",
            ("{python}", tools + "/generate_worldgen_hook_catalog.py", "{options:0}", "--check"),
            (
                path_field("binding", "--binding", "Exact hook binding input.", required=False),
                path_field("spec", "--spec", "Exact hook specification.", required=False),
                path_field("output", "--output", "Compiled catalog to check.", required=False),
                path_field("source_root", "--source-root", "Optional exact Cleanroom sources.", required=False),
            ), doc, availability="experimental",
        )
    )
    return commands


def _blueprints_commands() -> list[CommandSpec]:
    template = ("{python}", "{root}/modules/blueprints/src/workbench_blueprints/cli.py", "{options:0}")
    workspace = _f("workspace", "Protected .workbench/blueprints session directory.", flags=("--workspace",), kind="path", required=True, placement=0)
    doc = "modules/blueprints/contracts/core-cli-interface-v1.md"
    base = {
        "suite_id": "blueprints",
        "authority": "Blueprints construction lifecycle",
        "documentation": doc,
        "availability": "experimental",
    }

    def command(action: str, title: str, summary: str, fields: Iterable[FieldSpec] = (), risk: str = "mutating") -> CommandSpec:
        return CommandSpec(
            f"blueprints.{action}", base["suite_id"], title, summary, base["authority"], risk, "inert-only",
            (*template, action, "{options:1}"),
            (workspace, *tuple(fields)), doc, availability=base["availability"],
            limitations=("No stable feature standard is currently admitted; lifecycle results preserve that state.",),
        )

    commands = [
        command("init", "Initialize Blueprint session", "Capture a target and initialize the canonical Blueprint lifecycle.", (
            _f("target_repository", "Target repository.", flags=("--target-repository",), kind="path", required=True, placement=1),
            _f("repository_id", "Exact target repository identity.", flags=("--repository-id",), required=True, placement=1),
            _f("intake", "Complete intake JSON.", flags=("--intake",), kind="path", placement=1, mutex_group="intake"),
            _f("feature_family", "Inline intake feature family.", flags=("--feature-family",), placement=1, mutex_group="intake"),
            _f("sequence", "Intake sequence.", flags=("--sequence",), kind="integer", default=0, placement=1),
            _f("operation", "Requested operation.", flags=("--operation",), kind="choice", choices=("create", "update", "reconcile"), placement=1),
            _f("target_key", "Target key.", flags=("--target-key",), placement=1),
            _f("desired_outcome", "Desired outcome.", flags=("--desired-outcome",), placement=1),
            _f("parameter", "Repeatable NAME=JSON parameter.", flags=("--parameter",), repeat=True, placement=1),
            _f("variant", "Repeatable requested variant.", flags=("--variant",), repeat=True, placement=1),
            _f("output_mode", "Release form.", flags=("--output-mode",), kind="choice", choices=("instructions", "patch-bundle", "direct-apply"), placement=1),
            _f("accept_compliant_revision", "Consent to compliant revision selection.", flags=("--accept-compliant-revision",), kind="boolean", placement=1),
            _f("allow_direct_apply", "Consent boundary enabling a later direct apply.", flags=("--allow-direct-apply",), kind="boolean", placement=1),
            _f("registry_root", "Explicit profile-owned Blueprint registry root.", flags=("--registry-root",), kind="path", required=True, placement=1),
            _f("asset_root", "Blueprint asset root.", flags=("--asset-root",), kind="path", placement=1),
            _f("ledger", "Explicit profile-owned Blueprint ledger path.", flags=("--ledger",), kind="path", required=True, placement=1),
        )),
        command("plan", "Plan Blueprint", "Select a standard or labeled experimental pattern and synthesize a reviewable plan.", (_f("planning_evidence", "Planning evidence JSON.", flags=("--planning-evidence",), kind="path", required=True, placement=1), _f("choices", "Explicit choices JSON.", flags=("--choices",), kind="path", placement=1))),
        command("simulate", "Simulate Blueprint", "Execute the lifecycle's isolated validation step.", (_f("environment_lock", "Exact environment lock JSON.", flags=("--environment-lock",), kind="path", required=True, placement=1), _f("dependency_source", "Local locked dependency files.", flags=("--dependency-source",), kind="path", placement=1))),
        command("generate", "Generate Blueprint release", "Release the passing candidate in its selected output form."),
        command("apply", "Apply Blueprint release", "Apply a separately consented direct release to the captured target.", risk="mutating"),
        command("verify", "Verify Blueprint target", "Verify the applied target using the canonical lifecycle."),
        command("history", "Record Blueprint history", "Record and return the session's local lifecycle history."),
        command("export-proof", "Export Blueprint proof", "Export a portable proof only when the lifecycle admits one.", risk="writes-output"),
    ]
    standards = "{root}/modules/blueprints/src/workbench_blueprints/standards.py"
    standard_fields = (
        _f("registry_root", "Explicit profile-owned Blueprint registry root.", flags=("--registry-root",), kind="path", required=True),
        _f("asset_root", "Blueprint asset root.", flags=("--asset-root",), kind="path"),
    )
    commands.extend(
        [
            CommandSpec("blueprints.standard-compile", "blueprints", "Compile one Blueprint standard", "Compile and print one exact implementation standard without updating the registry.", "Blueprints standard compiler", "read-only", "none", ("{python}", standards, "compile", "{options:0}"), (_f("path", "Exact standard source.", positional=True, kind="path", required=True), *standard_fields), doc, availability="experimental"),
            CommandSpec("blueprints.standard-check", "blueprints", "Check Blueprint registry", "Validate the admitted standard registry and its assets without changing it.", "Blueprints standard compiler", "read-only", "none", ("{python}", standards, "check", "{options:0}"), standard_fields, doc, availability="experimental"),
            CommandSpec("blueprints.standard-build", "blueprints", "Build Blueprint registry", "Atomically rebuild the admitted standard registry from exact checked-in sources.", "Blueprints standard compiler", "mutating", "inert-only", ("{python}", standards, "build", "{options:0}"), standard_fields, doc, availability="experimental"),
        ]
    )
    return commands


def _manual_commands(root: Path) -> list[CommandSpec]:
    guide_root = root / "modules/manuals/guides"
    commands: list[CommandSpec] = [
        CommandSpec("manuals.overview", "manuals", "Manuals overview", "Read the practical teaching contract and current authority boundary.", "Manuals teaching; never executable approval", "read-only", "none", document="modules/manuals/README.md"),
    ]
    for path in sorted(
        guide_root.rglob("*.md"),
        key=lambda candidate: (
            candidate.relative_to(root).as_posix().encode("utf-8")
        ),
    ):
        relative = path.relative_to(root).as_posix()
        slug = relative.removeprefix("modules/manuals/guides/").removesuffix("/README.md").removesuffix(".md").replace("/", ".")
        if slug.endswith(".README"):
            slug = slug[:-7]
        command_id = "manuals." + re.sub(r"[^a-z0-9.-]+", "-", slug.casefold())
        title = path.stem.replace("-", " ").title() if path.name != "README.md" else path.parent.name.replace("-", " ").title()
        commands.append(CommandSpec(command_id, "manuals", title, "Open this checked-in experimental implementation guide in the terminal.", "Manuals teaching; never executable approval", "read-only", "none", document=relative, availability="experimental"))
    return commands


def _shell_commands() -> list[CommandSpec]:
    material_common = (
        _f("workspace", "Supersymmetry Packwiz workspace.", positional=True, kind="path", required=True),
        _f("name", "Developer-facing material name.", flags=("--name",), required=True),
        _f("color", "Lowercase 0xrrggbb material color.", flags=("--color",), required=True),
        _f("translation", "English label; defaults to the developer name.", flags=("--translation",)),
        _f("symbol", "Optional exact Groovy static-field symbol.", flags=("--symbol",)),
        _f("launcher", "Disposable client launcher family.", flags=("--launcher",), kind="choice", choices=("prism", "multimc"), default="prism"),
    )
    return [
        CommandSpec(
            "shell.live-console",
            "shell",
            "Live console guide",
            "Read the console lifecycle, retention, search, cancellation, and authority contract; session list/replay and file ingestion are built into this surface.",
            "Workbench Shell presentation and orchestration",
            "read-only",
            "none",
            document="docs/architecture/HIGH-SIGNAL-LIVE-CONSOLE.md",
            availability="experimental",
        ),
        CommandSpec(
            "shell.material-fluid-plan",
            "shell",
            "Plan material-backed fluid trial",
            "Show the exact three profile-owned source edits and fresh disposable cold-start boundary without changing state.",
            "Workbench Shell orchestration over Atlas facts, a profile-owned Blueprint, and Crucible runtime custody",
            "read-only",
            "none",
            _workbench_template("material-fluid", "plan"),
            (*material_common, _f("json", "Emit the complete canonical plan.", flags=("--json",), kind="boolean")),
            "modules/workbench-shell/README.md",
            availability="experimental",
            limitations=(
                "The plan does not claim Groovy compilation or material, fluid, or localization registration.",
            ),
        ),
        CommandSpec(
            "shell.material-fluid-run",
            "shell",
            "Run reviewed material-backed fluid trial",
            "Revalidate one exact plan, stage it under ignored state, and cold-start a fresh disposable client through final observation.",
            "Workbench Shell orchestration over Atlas facts, a profile-owned Blueprint, and Crucible runtime custody",
            "mutating",
            "append-show",
            _workbench_template("material-fluid", "run"),
            (
                *material_common,
                _f("plan_id", "Exact ID returned by the reviewed plan command.", flags=("--plan-id",), required=True),
                _f("launcher_executable", "Prism Launcher or MultiMC executable.", flags=("--launcher-executable",), kind="path", required=True),
                _f("launcher_root", "Launcher data root receiving a fresh instance.", flags=("--launcher-root",), kind="path", required=True),
                _f("launcher_java", "Exact launcher-host Temurin candidate.", flags=("--launcher-java",), kind="path"),
                _f("launcher_java_state", "Managed launcher-host Java store.", flags=("--launcher-java-state",), kind="path"),
                _f("launcher_profile", "Configured launcher profile; retained values are redacted.", flags=("--launcher-profile",), sensitive=True),
                _f("packwiz", "Packwiz executable override.", flags=("--packwiz",), kind="path"),
                _f("seed", "Read-only hash-matched Packwiz seed root; repeatable.", flags=("--seed",), kind="path", repeat=True),
                _f("memory_mib", "Maximum client heap in MiB.", flags=("--memory-mib",), kind="integer", default=8192),
                _f("offline_name", "Offline player name when no launcher profile is supplied.", flags=("--offline-name",), default="Workbench"),
                _f("launch_timeout", "Client-loaded timeout seconds.", flags=("--launch-timeout",), default=600.0),
                _f("attach_timeout", "Projected-process attachment timeout seconds.", flags=("--attach-timeout",), default=120.0),
                _f("session_timeout", "Maximum observed client session seconds.", flags=("--session-timeout",), default=21600.0),
                _f("show", "Revalidate and show the inert plan.", flags=("--show",), kind="boolean", console_managed=True),
            ),
            "modules/workbench-shell/README.md",
            availability="experimental",
            limitations=(
                "Execution mutates only ignored staging and a new Workbench-owned launcher projection.",
                "One exact profile-authorized Recurrent Complex bridge is shown in the plan and applied only to the disposable projection; arbitrary patches are rejected.",
                "FML load remains separate from Groovy, material, fluid, and localization assertions.",
            ),
        ),
    ]


def _feature_studio_commands() -> list[CommandSpec]:
    common = (
        _f("workspace", "Supersymmetry Packwiz workspace.", positional=True, kind="path", required=True),
        _f("name", "Developer-facing material name.", flags=("--name",), required=True),
        _f("color", "Lowercase 0xrrggbb material color.", flags=("--color",), required=True),
        _f("translation", "English label; defaults to the developer name.", flags=("--translation",)),
        _f("symbol", "Optional exact Groovy static-field symbol.", flags=("--symbol",)),
        _f("launcher", "Disposable client launcher family.", flags=("--launcher",), kind="choice", choices=("prism", "multimc"), default="prism"),
    )
    document = "docs/architecture/FEATURE-STUDIO.md"
    authority = (
        "Workbench Shell composition over Blueprints construction, Atlas semantics, "
        "profile authority, and Crucible custody"
    )

    def read_only(
        action: str,
        title: str,
        summary: str,
        *,
        fields: tuple[FieldSpec, ...] = common,
    ) -> CommandSpec:
        return CommandSpec(
            f"feature-studio.{action}",
            "feature-studio",
            title,
            summary,
            authority,
            "read-only",
            "none",
            _workbench_template("studio", action),
            (*fields, _f("json", "Emit the closed Feature Studio result.", flags=("--json",), kind="boolean")),
            document,
            availability="experimental",
        )

    verify_fields = (
        *common,
        _f("plan_id", "Exact Feature Studio flow plan ID reviewed before execution.", flags=("--plan-id",), required=True),
        _f("launcher_executable", "Prism Launcher or MultiMC executable.", flags=("--launcher-executable",), kind="path", required=True),
        _f("launcher_root", "Launcher data root receiving a fresh instance.", flags=("--launcher-root",), kind="path", required=True),
        _f("launcher_java", "Exact launcher-host Temurin candidate.", flags=("--launcher-java",), kind="path"),
        _f("launcher_java_state", "Managed launcher-host Java store.", flags=("--launcher-java-state",), kind="path"),
        _f("launcher_profile", "Configured launcher profile; retained values are redacted.", flags=("--launcher-profile",), sensitive=True),
        _f("packwiz", "Packwiz executable override.", flags=("--packwiz",), kind="path"),
        _f("seed", "Read-only hash-matched Packwiz seed root; repeatable.", flags=("--seed",), kind="path", repeat=True),
        _f("memory_mib", "Maximum client heap in MiB.", flags=("--memory-mib",), kind="integer", default=8192),
        _f("offline_name", "Offline player name when no launcher profile is supplied.", flags=("--offline-name",), default="Workbench"),
        _f("launch_timeout", "Client-loaded timeout seconds.", flags=("--launch-timeout",), default=600.0),
        _f("attach_timeout", "Projected-process attachment timeout seconds.", flags=("--attach-timeout",), default=120.0),
        _f("session_timeout", "Maximum observed client session seconds.", flags=("--session-timeout",), default=21600.0),
        _f("state_root", "Ignored Workbench state root override.", flags=("--state-root",), kind="path"),
        _f("show", "Revalidate and show the inert plan.", flags=("--show",), kind="boolean", console_managed=True),
        _f("json", "Emit the closed Feature Studio result.", flags=("--json",), kind="boolean"),
    )
    return [
        read_only(
            "inspect",
            "Inspect feature workspace",
            "Open one exact Materials & Recipes workspace from source planning inputs or a retained owner receipt.",
            fields=(
                _f("workspace", "Supersymmetry Packwiz workspace when inspecting a new plan.", positional=True, kind="path"),
                _f("receipt", "Exact retained material-flow V2 receipt selected by identity.", flags=("--receipt",), kind="path"),
                _f("name", "Developer-facing material name for source planning.", flags=("--name",)),
                _f("color", "Lowercase 0xrrggbb material color for source planning.", flags=("--color",)),
                _f("translation", "English label; defaults to the developer name.", flags=("--translation",)),
                _f("symbol", "Optional exact Groovy static-field symbol.", flags=("--symbol",)),
                _f("launcher", "Disposable client launcher family.", flags=("--launcher",), kind="choice", choices=("prism", "multimc"), default="prism"),
            ),
        ),
        read_only("plan", "Plan feature", "Produce the exact Blueprint and profile-bound material-backed-fluid plan without writing state."),
        CommandSpec(
            "feature-studio.verify",
            "feature-studio",
            "Verify reviewed feature",
            "Stage and cold-start one exact reviewed feature in a fresh disposable projection, retaining independent assertions.",
            authority,
            "mutating",
            "append-show",
            _workbench_template("studio", "verify"),
            verify_fields,
            document,
            availability="experimental",
            limitations=(
                "Use Feature Studio Service V3 when durable jobs, cancellation, or reattachment are required.",
                "Execution mutates only ignored state and a fresh Workbench-owned launcher projection.",
            ),
        ),
        read_only("explain", "Explain feature state", "Present owner-backed evidence states, limitations, documentation, and the next safe action."),
        CommandSpec(
            "feature-studio.export",
            "feature-studio",
            "Export reviewed feature patch",
            "Write one exact reviewed three-file patch and content-addressed receipt without modifying source.",
            authority,
            "writes-output",
            "append-show",
            _workbench_template("studio", "export"),
            (
                *common,
                _f("plan_id", "Exact Feature Studio flow plan ID reviewed before export.", flags=("--plan-id",), required=True),
                _f("output", "New output directory for feature.patch and receipt.json.", flags=("--output",), kind="path", required=True),
                _f("show", "Revalidate and show the inert plan.", flags=("--show",), kind="boolean", console_managed=True),
                _f("json", "Emit the closed Feature Studio result.", flags=("--json",), kind="boolean"),
            ),
            document,
            availability="experimental",
            limitations=(
                "The export never applies changes and must not overlap the developer workspace.",
            ),
        ),
    ]


def _process_studio_commands() -> list[CommandSpec]:
    return [
        CommandSpec(
            "process-studio.recipes-compare",
            "process-studio",
            "Compare runtime recipes",
            "Compare finite recipes and bounded resource-flow exposure across two explicit Atlas runtime graphs.",
            "Atlas runtime recipe authority presented through the Process Studio workflow",
            "read-only",
            "none",
            _workbench_template("process", "recipes", "compare"),
            (
                _f(
                    "baseline",
                    "Earlier verified categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "candidate",
                    "Later verified categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f("max_recipes", "Maximum finite recipes scanned per graph.", flags=("--max-recipes",), kind="integer", default=100000),
                _f("max_recipe_deltas", "Maximum exact signature delta details.", flags=("--max-recipe-deltas",), kind="integer", default=500),
                _f("max_resources", "Maximum resource-flow delta details.", flags=("--max-resources",), kind="integer", default=500),
                _f("max_depth", "Maximum bounded propagation depth.", flags=("--max-depth",), kind="integer", default=4),
                _f("max_nodes", "Maximum graph nodes inspected across both sides.", flags=("--max-nodes",), kind="integer", default=2000),
                _f("json", "Emit the complete unchanged Atlas comparison record.", flags=("--json",), kind="boolean"),
            ),
            "modules/process-studio/README.md",
            availability="experimental",
            limitations=(
                "Atlas owns graph verification and every observed or derived recipe claim; Process Studio does not create another graph.",
                "Capture protocol and context must be comparable or Atlas emits no semantic delta.",
                "The result does not prove causality, balance, execution, global reachability, or release readiness.",
            ),
        ),
        CommandSpec(
            "process-studio.effects-compare",
            "process-studio",
            "Compare bounded synthetic effects",
            "Produce a bounded observed-effect comparison from explicit synthetic fixture snapshots, with optional matching against a declared change envelope.",
            "Process Studio synthetic-fixture comparison; live Atlas and profile adapter integration is unavailable",
            "writes-output",
            "none",
            _workbench_template("process", "effects", "compare"),
            (
                _f(
                    "baseline",
                    "Explicit baseline fixture snapshot.",
                    flags=("--baseline",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "candidate",
                    "Explicit candidate fixture snapshot.",
                    flags=("--candidate",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "envelope",
                    "Optional declared change envelope for comparison-state classification.",
                    flags=("--envelope",),
                    kind="path",
                ),
                _f(
                    "fixture_adapters",
                    "Explicitly select the synthetic fixture adapters.",
                    flags=("--fixture-adapters",),
                    kind="boolean",
                    required=True,
                ),
                _f(
                    "output",
                    "Optional explicit path for the comparison report.",
                    flags=("--output",),
                    kind="path",
                ),
                _f(
                    "json",
                    "Emit the complete bounded observed-effect comparison as JSON.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            "modules/workbench-shell/README.md",
            availability="experimental",
            limitations=(
                "The operation does not mutate source, runtime, or snapshot inputs; --output only writes the explicit report path.",
                "Workbench Shell forwards the request and presents the Process Studio comparison state without reinterpretation.",
                "The comparison does not establish causality, playability, construction approval, or save compatibility.",
                "Without --envelope, the output is comparison-only and does not evaluate declared bounds.",
                "The required --fixture-adapters flag selects synthetic built-ins only; live Atlas/profile adapter integration is unavailable and no runtime authority is claimed.",
                "Envelope matching does not prove prior authorship or trusted custody; this incomplete slice has no temporal-admission authority.",
            ),
        ),
    ]


def _impact_studio_commands() -> list[CommandSpec]:
    """Expose the three bounded Impact Studio compositions."""

    document = "docs/product/IMPACT-STUDIOS.md"
    json_output = _f(
        "json",
        "Emit the complete owner-preserving machine-readable result.",
        flags=("--json",),
        kind="boolean",
    )
    return [
        CommandSpec(
            "machine-studio.inspect",
            "machine-studio",
            "Inspect one machine",
            "Inspect one exact Atlas machine, its registered forms, recipe maps, bounded recipes, and source navigation while keeping formed-world proof separate.",
            "Machine Studio composition over Atlas knowledge and Exact Runtime Explorer navigation",
            "read-only",
            "none",
            _workbench_template("machine", "inspect"),
            (
                _f(
                    "identity",
                    "Exact machine identity selected by the declared Atlas key kind.",
                    positional=True,
                    required=True,
                ),
                _f(
                    "runtime_db",
                    "Explicit immutable Atlas runtime-graph query database.",
                    flags=("--runtime-db",),
                    kind="path",
                    required=True,
                ),
                _f(
                    "key_kind",
                    "Exact Atlas identity-key kind; use runtime-node-id for a canonical node ID.",
                    flags=("--key-kind",),
                    default="registry-name",
                ),
                _f(
                    "profile",
                    "Optional exact Atlas profile used to disambiguate the identity.",
                    flags=("--profile",),
                ),
                _f(
                    "side",
                    "Optional exact Atlas physical side used to disambiguate the identity.",
                    flags=("--side",),
                ),
                _f(
                    "recipe_limit",
                    "Maximum lookup-active recipe details from 1 through 1000.",
                    flags=("--recipe-limit",),
                    kind="integer",
                    default=100,
                ),
                _f(
                    "relationship_limit",
                    "Maximum exact relationship details from 1 through 1000.",
                    flags=("--relationship-limit",),
                    kind="integer",
                    default=250,
                ),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                "Atlas owns every machine, recipe, and relationship record; Machine Studio only groups exact rows for review.",
                "Registered forms and structure-shaped relationships do not prove that a machine formed or operated in a world.",
                "Recipe rows are lookup-active registry observations, not observed executions or playability claims.",
            ),
        ),
        CommandSpec(
            "asset-studio.check",
            "asset-studio",
            "Check local asset closure",
            "Check blockstate, model, texture, model-parent, and explicitly requested localization references in one selected workspace without changing it.",
            "Asset Studio read-only local filesystem closure",
            "read-only",
            "none",
            _workbench_template("assets", "check"),
            (
                _f(
                    "workspace",
                    "Explicit assets directory or workspace containing resources/.../assets.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "identity",
                    "Optional exact namespace:path block or item identity.",
                    flags=("--identity",),
                ),
                _f(
                    "translation_key",
                    "Exact localization key required by the caller; repeatable.",
                    flags=("--translation-key",),
                    repeat=True,
                ),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                "Local source closure does not prove runtime registration, model selection, rendering success, or visibility.",
                "Missing Minecraft-namespace dependencies are reported as external/not supplied rather than invented as local defects.",
                "Conventional localization keys are candidates only; exact requirements must be supplied with --translation-key.",
            ),
        ),
        CommandSpec(
            "evolution-studio.recipes",
            "evolution-studio",
            "Compare recipe evidence",
            "Compare two exact capture-compatible runtime recipe graphs through the existing Atlas comparison without changing its record or claims.",
            "Evolution Studio route over Atlas runtime recipe comparison authority",
            "read-only",
            "none",
            _workbench_template("evolution", "recipes"),
            (
                _f(
                    "before_path",
                    "Verified baseline categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "after_path",
                    "Verified candidate categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f("max_recipes", "Maximum finite recipes scanned per graph.", flags=("--max-recipes",), kind="integer", default=100000),
                _f("max_recipe_deltas", "Maximum exact signature delta details.", flags=("--max-recipe-deltas",), kind="integer", default=500),
                _f("max_resources", "Maximum resource-flow delta details.", flags=("--max-resources",), kind="integer", default=500),
                _f("max_depth", "Maximum bounded propagation depth.", flags=("--max-depth",), kind="integer", default=4),
                _f("max_nodes", "Maximum graph nodes inspected across both sides.", flags=("--max-nodes",), kind="integer", default=2000),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                "The command delegates to Atlas and emits its existing V1 JSON or human rendering without an Evolution-owned reinterpretation.",
                "Scope, adapter profile, and capture protocol must match exactly or Atlas emits no semantic delta.",
                "Added and removed immutable signatures are not inferred to be changed pairs, migrations, or save-compatible transitions.",
            ),
        ),
    ]


def _developer_feature_commands() -> list[CommandSpec]:
    """Expose the Shell-owned reversible developer-feature lifecycle to clients."""

    families = ("material-fluid-recipe", "recipe-change", "quest-for-process")
    collection_choices = ("plans", "receipts", "rollbacks", "recoveries", "runs")
    document = "README.md"
    authority = (
        "Workbench Shell orchestration over profile-owned Supersymmetry Blueprints "
        "and retained transaction custody"
    )
    boundary = (
        "These experimental local flows do not authorize tested-profile support, "
        "stable-standard admission, release, or publication.",
    )
    state_root = _f(
        "state_root",
        "Retained developer-feature state root; defaults to stable per-user state.",
        flags=("--state-root",),
        kind="path",
    )
    json_output = _f(
        "json",
        "Emit the complete owner-defined machine-readable result.",
        flags=("--json",),
        kind="boolean",
    )
    plan_json_output = _f(
        "json",
        "Emit the complete owner-defined plan, including exact operation bytes.",
        flags=("--json",),
        kind="boolean",
        mutex_group="plan-output",
    )
    compact_plan_output = _f(
        "compact_json",
        "Emit a validated plan, retention, and review summary without Base64 operation bytes.",
        flags=("--compact-json",),
        kind="boolean",
        mutex_group="plan-output",
    )
    workspace = _f(
        "workspace",
        "Compatible Supersymmetry Packwiz workspace.",
        positional=True,
        kind="path",
        required=True,
    )
    family = _f(
        "family",
        "Exact developer-feature family.",
        positional=True,
        kind="choice",
        choices=families,
        required=True,
    )
    plan_record = _f(
        "plan",
        "Retained plan ID or exact record path.",
        positional=True,
        required=True,
    )

    material_plan_fields = (
        workspace,
        _f("name", "Developer-facing material name.", flags=("--name",), required=True),
        _f("color", "Lowercase 0xrrggbb material color.", flags=("--color",), required=True),
        _f("recipe_script", "Existing recipe-owner script selected by the profile catalog.", flags=("--recipe-script",), required=True),
        _f("recipe_map", "Existing recipe map alias selected by the profile catalog.", flags=("--recipe-map",), required=True),
        _f("input_fluid", "Existing input-fluid registry identity.", flags=("--input-fluid",), required=True),
        _f("input_amount", "Input fluid amount in mB.", flags=("--input-amount",), kind="integer", default=1000),
        _f("output_amount", "Output fluid amount in mB.", flags=("--output-amount",), kind="integer", default=1000),
        _f("duration", "Recipe duration in ticks.", flags=("--duration",), kind="integer", default=200),
        _f("voltage_tier", "Existing voltage-tier symbol.", flags=("--voltage-tier",), default="LV"),
        _f("translation", "English label; defaults to the developer name.", flags=("--translation",)),
        _f("symbol", "Optional exact Groovy static-field symbol.", flags=("--symbol",)),
        state_root,
        _f("show_diff", "Show the reviewed unified diff in human output.", flags=("--show-diff",), kind="boolean"),
        plan_json_output,
        compact_plan_output,
    )
    recipe_plan_fields = (
        workspace,
        _f("mutation", "Supported recipe mutation.", flags=("--mutation",), kind="choice", choices=("add",), default="add"),
        _f("recipe_script", "Existing recipe-owner script selected by the profile catalog.", flags=("--recipe-script",), required=True),
        _f("recipe_map", "Existing recipe map alias selected by the profile catalog.", flags=("--recipe-map",), required=True),
        _f("item_input", "One item-input object; repeatable.", flags=("--item-input",), kind="json", repeat=True),
        _f("fluid_input", "One fluid-input object; repeatable.", flags=("--fluid-input",), kind="json", repeat=True),
        _f("item_output", "One item-output object; repeatable.", flags=("--item-output",), kind="json", repeat=True),
        _f("fluid_output", "One fluid-output object; repeatable.", flags=("--fluid-output",), kind="json", repeat=True),
        _f("duration", "Recipe duration in ticks.", flags=("--duration",), kind="integer", required=True),
        _f("voltage_tier", "Existing voltage-tier symbol.", flags=("--voltage-tier",), required=True),
        state_root,
        _f("show_diff", "Show the reviewed unified diff in human output.", flags=("--show-diff",), kind="boolean"),
        plan_json_output,
        compact_plan_output,
    )
    quest_plan_fields = (
        workspace,
        _f("quest_id", "Existing BetterQuesting quest ID.", flags=("--quest-id",), kind="integer", required=True),
        _f("add_prerequisite_id", "Existing quest ID to add as a prerequisite.", flags=("--add-prerequisite-id",), kind="integer"),
        _f("requirement_type", "BetterQuesting prerequisite edge type.", flags=("--requirement-type",), kind="choice", choices=("NORMAL", "IMPLICIT", "HIDDEN"), default="IMPLICIT"),
        _f("title", "Replacement text for the quest's existing title key.", flags=("--title",)),
        _f("description", "Replacement text for the quest's existing description key.", flags=("--description",)),
        state_root,
        _f("show_diff", "Show the reviewed unified diff in human output.", flags=("--show-diff",), kind="boolean"),
        plan_json_output,
        compact_plan_output,
    )

    def plan(
        family_name: str,
        title: str,
        summary: str,
        fields: tuple[FieldSpec, ...],
        *limitations: str,
    ) -> CommandSpec:
        return CommandSpec(
            f"developer-features.plan-{family_name}",
            "developer-features",
            title,
            summary,
            authority,
            "writes-output",
            "inert-only",
            _workbench_template("feature", "plan", family_name),
            fields,
            document,
            availability="experimental",
            limitations=(*boundary, *limitations),
        )

    lifecycle_fields = (family, plan_record, state_root, json_output)
    return [
        CommandSpec(
            "developer-features.examples",
            "developer-features",
            "Browse packaged Supersymmetry examples",
            "List one bounded set of revision-bound Blueprint examples or inspect an exact family/example record.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "examples"),
            (
                _f("selector", "Optional family or exact packaged example key.", positional=True),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Examples are non-identity-bearing research records; their historical revisions may predate the current Workbench profile contract.",
            ),
        ),
        CommandSpec(
            "developer-features.records",
            "developer-features",
            "Discover retained feature records",
            "List owner-validated plans and transaction records retained in one explicit state root.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "records"),
            (
                _f(
                    "family",
                    "Optional exact developer-feature family filter.",
                    positional=True,
                    kind="choice",
                    choices=families,
                ),
                _f(
                    "collection",
                    "Optional retained-record collection filter; requires a family positional value first.",
                    positional=True,
                    kind="choice",
                    choices=collection_choices,
                ),
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Discovery covers only the selected state root; each record is reopened through its owner-validated presentation before action.",
            ),
        ),
        CommandSpec(
            "developer-features.options",
            "developer-features",
            "Inspect feature construction options",
            "List current profile-owned recipe owners or bounded localized quest owners for a compatible workspace.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "options"),
            (
                family,
                workspace,
                _f("query", "Case-insensitive quest ID/title, recipe-map, registry-name, or owner-path search.", flags=("--query",)),
                _f("limit", "Maximum returned matches per option collection from 1 to 200.", flags=("--limit",), kind="integer", default=50),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Quest options describe source owners and localization only; they do not establish BetterQuesting runtime state.",
            ),
        ),
        CommandSpec(
            "developer-features.plan-example",
            "developer-features",
            "Plan packaged Blueprint example",
            "Translate one exact allowlisted example through its current family owner and retain only the resulting ordinary plan.",
            authority,
            "writes-output",
            "inert-only",
            _workbench_template("feature", "plan", "example"),
            (
                _f(
                    "example_key",
                    "Exact allowlisted packaged Blueprint example key.",
                    positional=True,
                    kind="choice",
                    choices=example_choices(),
                    required=True,
                ),
                workspace,
                state_root,
                _f("show_diff", "Show the owner-validated unified diff in human output.", flags=("--show-diff",), kind="boolean"),
                plan_json_output,
                compact_plan_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "The packaged record supplies no construction authority; current owner validation may report it as not applicable without retaining a plan.",
                "Application still requires the family apply command and consent to the exact retained plan ID.",
            ),
        ),
        plan(
            "material-fluid-recipe",
            "Plan material, fluid, and recipe",
            "Build and retain one exact four-file Supersymmetry material-backed-fluid recipe plan.",
            material_plan_fields,
            "Construction adds one new material, fluid, localization entry, and machine recipe; replacement and removal are unavailable.",
            "Runtime registration remains unobserved until the reviewed plan is run in a disposable client.",
        ),
        plan(
            "recipe-change",
            "Plan machine recipe addition",
            "Build and retain one exact additive machine-recipe plan for an existing owner script.",
            recipe_plan_fields,
            "V1 supports additive machine recipes only; replacement and removal are unavailable.",
            "Client and dedicated-server runtime observation remain required after source application.",
        ),
        plan(
            "quest-for-process",
            "Plan quest process edit",
            "Build and retain one exact existing-quest prerequisite and/or localization edit.",
            quest_plan_fields,
            "V1 edits an existing quest and existing localization keys; quest creation and ID allocation are unavailable.",
            "The owner requires at least one prerequisite, title, or description change and rejects cycle-forming edges.",
            "BetterQuesting runtime/world-state observation remains required after source application.",
        ),
        CommandSpec(
            "developer-features.check",
            "developer-features",
            "Check retained feature plan",
            "Regenerate one retained plan against its exact workspace and report staleness without writing source.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "check"),
            lifecycle_fields,
            document,
            availability="experimental",
            limitations=boundary,
        ),
        CommandSpec(
            "developer-features.compare-recipe-runtime",
            "developer-features",
            "Compare recipe runtime projections",
            "Cold-start one unchanged baseline and one exact reviewed recipe candidate in separate disposable clients.",
            authority,
            "mutating",
            "inert-only",
            _workbench_template("feature", "compare-runtime"),
            (
                _f("family", "Runtime-comparable feature family.", positional=True, kind="choice", choices=("recipe-change",), required=True),
                plan_record,
                _f("consent", "Exact reviewed plan ID authorizing both disposable sides.", flags=("--consent",), required=True),
                _f("order", "One-pair execution order retained as a confound.", flags=("--order",), kind="choice", choices=("baseline-first", "candidate-first"), default="baseline-first"),
                _f("launcher", "Disposable client launcher family.", flags=("--launcher",), kind="choice", choices=("prism", "multimc"), default="prism"),
                _f("launcher_executable", "Prism Launcher or MultiMC executable.", flags=("--launcher-executable",), kind="path", required=True),
                _f("launcher_root", "Launcher data root receiving two fresh instances.", flags=("--launcher-root",), kind="path", required=True),
                _f("launcher_profile", "Configured launcher profile; retained values are redacted.", flags=("--launcher-profile",), sensitive=True),
                _f("launcher_java", "Exact launcher-host Temurin candidate.", flags=("--launcher-java",), kind="path"),
                _f("launcher_java_state", "Managed launcher-host Java store.", flags=("--launcher-java-state",), kind="path"),
                _f("packwiz_executable", "Packwiz executable override.", flags=("--packwiz-executable",), kind="path"),
                _f("seed", "Read-only hash-matched payload seed root; repeatable.", flags=("--seed",), kind="path", repeat=True),
                _f("memory_mib", "Maximum client heap in MiB.", flags=("--memory-mib",), kind="integer", default=8192),
                _f("offline_name", "Offline player name when no profile is supplied.", flags=("--offline-name",), default="Workbench"),
                _f("timeout", "Client-loaded timeout seconds for each side.", flags=("--timeout",), default=600.0),
                _f("attach_timeout", "Projected-process attachment timeout seconds.", flags=("--attach-timeout",), default=120.0),
                _f("session_timeout", "Maximum observed session seconds per side.", flags=("--session-timeout",), default=21600.0),
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Both sides mutate only ignored staging, runtime state, and fresh launcher projections; the developer checkout remains read-only.",
                "The current paired runner observes clients only; dedicated-server parity remains required.",
                "Exact registration and lookup deltas do not prove player reachability or downstream progression safety.",
            ),
        ),
        CommandSpec(
            "developer-features.apply",
            "developer-features",
            "Apply reviewed feature plan",
            "Apply one exact non-stale plan to its local workspace after explicit plan-ID consent.",
            authority,
            "mutating",
            "inert-only",
            _workbench_template("feature", "apply"),
            (
                family,
                plan_record,
                _f("consent", "Exact reviewed plan ID authorizing local application.", flags=("--consent",), required=True),
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Application changes only the exact owner-bound source files after staleness and transaction preflight.",
            ),
        ),
        CommandSpec(
            "developer-features.rollback",
            "developer-features",
            "Roll back applied feature plan",
            "Restore one locally applied plan from its exact successful application receipt.",
            authority,
            "mutating",
            "inert-only",
            _workbench_template("feature", "rollback"),
            (
                family,
                plan_record,
                _f("receipt", "Retained application receipt ID or exact record path.", positional=True, required=True),
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Rollback refuses source that no longer matches the exact successful application receipt.",
            ),
        ),
        CommandSpec(
            "developer-features.recover",
            "developer-features",
            "Recover interrupted feature transaction",
            "Finish or restore one exact transaction interrupted by process death.",
            authority,
            "mutating",
            "inert-only",
            _workbench_template("feature", "recover"),
            lifecycle_fields,
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Recovery is meaningful only when the exact workspace transaction journal still exists.",
            ),
        ),
        CommandSpec(
            "developer-features.present",
            "developer-features",
            "Present retained feature record",
            "Project one retained owner record into the Shell-owned IDE presentation contract.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "present"),
            (
                family,
                _f("collection", "Retained record collection.", positional=True, kind="choice", choices=collection_choices, required=True),
                _f("record", "Retained record ID or exact record path.", positional=True, required=True),
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "The presentation summarizes owner records for IDE display and does not become a second construction or transaction authority.",
            ),
        ),
        CommandSpec(
            "developer-features.transaction",
            "developer-features",
            "Inspect current feature transaction",
            "Show compact retained lineage, reviewed diffs, and point-in-time workspace byte state for one retained plan.",
            authority,
            "read-only",
            "none",
            _workbench_template("feature", "transaction"),
            (
                family,
                plan_record,
                state_root,
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *boundary,
                "Current state is a point-in-time read-only observation; every mutating owner command revalidates under its transaction lock.",
                "Retained records remain immutable historical identities even after a later rollback or recovery.",
            ),
        ),
    ]


def _recipe_capture_commands() -> list[CommandSpec]:
    """Compose the selected-environment capture lifecycle through its owners."""
    authority = "Workbench Shell orchestration over Core custody, the selected pack profile, Crucible admission and Atlas audit"
    document = "modules/workbench-shell/contracts/developer-recipe-capture-v1.md"
    common = (
        _f("state_root", "Private retained capture storage; defaults to per-user recipe-capture state.",
           flags=("--state-root",), kind="path"),
        _f("json", "Emit the complete owner-defined retained record.", flags=("--json",), kind="boolean"),
    )
    attempt = _f("attempt", "Exact retained recipe-capture attempt ID.", positional=True, required=True)
    boundaries = (
        "The selected profile currently supports the original pinned Forge/Java 8 tuple; another source branch does not qualify different binary versions.",
        "Preparation and capture admission are separate; qualification is specific to the observed runtime/JDK and broader recipe coverage remains incomplete.",
        "Missing captured producers or uses are structural candidates, with external acquisition and terminal uses unresolved.",
    )
    commands = [CommandSpec(
        "shell.recipe-capture-plan", "shell", "Plan a recipe capture",
        "Retain saved source and inspect Core-selected or explicitly overridden server and Java locations.",
        authority, "writes-output", "inert-only", _workbench_template("capture", "recipes", "plan"),
        (
            _f("workspace", "Saved pack checkout, including tracked and nonignored untracked files.",
               flags=("--workspace",), kind="path", required=True),
            _f("runtime", "Prepared dedicated-server environment override; otherwise Core's user fixture selection.",
               flags=("--runtime",), kind="path"),
            _f("java_home", "Compatible JDK override; otherwise Core's user fixture selection or matching Setup JDK.",
               flags=("--java-home",), kind="path"),
            _f("pack_profile", "Explicit installed recipe-capture profile.",
               flags=("--pack-profile",), required=True),
            _f("heap_mib", "Maximum game heap in MiB; the owner defaults to 16384.",
               flags=("--heap-mib",), kind="integer"),
            *common,
        ), document, availability="experimental", limitations=boundaries)]
    for fixture_action, title, summary, risk in (
        ("set", "Register recipe fixture locations", "Save workspace/profile runtime and JDK locations in Core user configuration.", "mutating"),
        ("inspect", "Inspect recipe fixture selection", "Resolve and verify configured or overridden runtime and JDK for this profile.", "read-only"),
    ):
        commands.append(CommandSpec(
            "shell.recipe-capture-fixtures-" + fixture_action, "shell", title, summary,
            authority, risk, "inert-only", _workbench_template("capture", "recipes", "fixtures", fixture_action),
            (_f("workspace", "Exact workspace for this user selection.", flags=("--workspace",), kind="path", required=True),
             _f("pack_profile", "Installed recipe-capture profile.", flags=("--pack-profile",), required=True),
             _f("runtime", "Prepared server location.", flags=("--runtime",), kind="path", required=fixture_action == "set"),
             _f("java_home", "Compatible JDK home.", flags=("--java-home",), kind="path", required=fixture_action == "set"),
             _f("json", "Emit the selected record.", flags=("--json",), kind="boolean")),
            document, availability="experimental", limitations=boundaries))
    for action, title, summary, risk in (
        ("prepare", "Prepare a recipe capture", "Copy the reviewed source, server and Java into custody and compile the observer.", "mutating"),
        ("run", "Run a prepared recipe capture", "Launch the isolated server, admit its sealed capture, and retain the complete Atlas audit.", "mutating"),
        ("show", "Reopen a recipe capture", "Verify and read retained capture status and audit evidence without launching the game.", "read-only"),
        ("cancel", "Cancel a recipe capture", "Request cancellation while retaining the attempt and available evidence.", "mutating"),
        ("export", "Export a completed recipe scan", "Verify a complete admitted capture and export its graph, audit and original observation records for another machine.", "writes-output"),
    ):
        fields = [attempt]
        if action in {"prepare", "run"}:
            fields.append(_f("confirm", "Exact reviewed plan ID." if action == "prepare" else "Exact prepared record ID.",
                             flags=("--confirm",), required=True))
        if action == "run":
            fields.append(_f("accept_eula", "Accept the Minecraft EULA for this isolated execution; required by the execution owner.",
                             flags=("--accept-eula",), kind="boolean"))
        if action == "export":
            fields.append(_f("output", "New completed-scan archive outside the retained attempt; existing files are never replaced.",
                             flags=("--output",), kind="path", required=True))
        commands.append(CommandSpec(
            "shell.recipe-capture-" + action, "shell", title, summary, authority, risk,
            "none" if action == "show" else "inert-only", _workbench_template("capture", "recipes", action),
            (*fields, *common), document, availability="experimental", limitations=boundaries))
    return commands


def _atlas_scan_commands() -> list[CommandSpec]:
    """Compose standalone Atlas completed-scan operations without evaluation."""
    document = "modules/atlas/contracts/atlas-completed-scan-v1.md"
    authority = "Atlas completed-scan reader over Core archive transport and Crucible evidence"
    limits = (
        "Imported scans retain their original capture scope and stored audit policy; findings are not recomputed.",
        "Content hashes establish integrity, not publisher authenticity or a match to the current target environment.",
        "The archive contains the full required data envelope, without a game, JDK, mods or original process custody.",
    )
    commands = []
    for action, title, summary in (
        ("import", "Import a completed Atlas scan", "Verify a selected scan archive and publish its original data in a new directory."),
        ("show", "Show a completed Atlas scan", "Verify provenance, coverage, stored audit totals and the local graph path."),
        ("audit", "Read a stored Atlas audit", "Read totals or filter a page of saved recipe findings without reevaluation."),
        ("export", "Export a completed Atlas scan", "Re-export an imported scan while preserving its original envelope identity."),
    ):
        fields = [_f("path", "Selected scan archive." if action == "import" else "Imported completed-scan directory.",
                     positional=True, required=True, kind="path")]
        if action == "import":
            fields.append(_f("destination", "New import directory; existing destinations are never replaced.",
                             flags=("--destination",), required=True, kind="path"))
        elif action == "export":
            fields.append(_f("output", "New archive; existing files are never replaced.",
                             flags=("--output",), required=True, kind="path"))
        elif action == "audit":
            fields.extend((
                _f("summary", "Return saved totals and matched count without recipe rows.", flags=("--summary",), kind="boolean"),
                _f("finding", "Select one stored finding category.", flags=("--finding",), kind="choice",
                   choices=("missing-producer-candidate", "no-output-use-candidate", "both-sides-candidate", "stranded-output-candidate", "structural-cycle")),
                _f("lookup_state", "Select the captured lookup state.", flags=("--lookup-state",), kind="choice", choices=("active", "inactive", "unknown")),
                _f("text", "Case-insensitive text in saved recipe/map IDs and related item/fluid names.", flags=("--text",)),
                _f("offset", "Zero-based offset in matching rows; the owner defaults to zero.", flags=("--offset",), kind="integer"),
                _f("limit", "Stored rows to return, 0..10000; an explicit selection defaults to 100.", flags=("--limit",), kind="integer"),
            ))
        fields.append(_f("json", "Emit the complete owner-defined result.", flags=("--json",), kind="boolean"))
        writing = action in {"import", "export"}
        commands.append(CommandSpec(
            "atlas.scans-" + action, "atlas", title, summary, authority,
            "writes-output" if writing else "read-only", "inert-only" if writing else "none",
            _workbench_template("atlas", "scans", action), tuple(fields), document,
            availability="experimental", limitations=limits))
    return commands


def _atlas_recipe_health_commands() -> list[CommandSpec]:
    """Expose recipe views, capture import and derived storage through Atlas."""

    document = "modules/atlas/README.md"
    authority = "Atlas recipe-health view over explicit source or verified categorical-graph evidence"
    path = _f(
        "path",
        "Explicit source checkout or retained categorical-graph manifest/root.",
        positional=True,
        kind="path",
        required=True,
    )
    json_output = _f(
        "json",
        "Emit the complete Atlas record.",
        flags=("--json",),
        kind="boolean",
    )
    common_limitations = (
        "Source-only evidence reports conservative text occurrences and cannot claim runtime collisions, producers, consumers, or ownership.",
        "The current view does not prove stoichiometric correctness or process-chain reachability.",
    )
    return [
        CommandSpec(
            "atlas.recipes-import-capture",
            "atlas",
            "Import retained recipe capture",
            "Project a retained capture into a new graph through its explicit pack adapter.",
            "Pack-owned recipe projection over Crucible-admitted retained capture",
            "mutating",
            "inert-only",
            _workbench_template("atlas", "recipes", "import-capture"),
            (
                _f("path", "Retained recipe capture directory.", positional=True, kind="path", required=True),
                _f("output", "New graph output directory.", flags=("--output",), kind="path", required=True),
                _f("input_manifest", "Exact input manifest bound by the capture.", flags=("--input-manifest",), kind="path", required=True),
                _f("pack_profile", "Explicit installed recipe-capture adapter profile.", flags=("--pack-profile",), required=True),
                _f("max_source_bytes", "Maximum admitted capture bytes; Atlas defaults to 1 GiB.", flags=("--max-source-bytes",), kind="integer"),
                json_output,
            ),
            "modules/atlas/contracts/atlas-recipe-capture-adapter-v1.md",
            availability="experimental",
            limitations=(
                "Import requires an admitted pack adapter and preserves the original capture context, native outcome and evidence gaps; it does not launch the game.",
                "Import writes a new graph; querying that graph does not require the adapter or original producer to remain installed.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-context",
            "atlas",
            "Inspect recipe evidence context",
            "Describe whether an explicit path supplies a verified V2 graph or a bounded source-only recipe view.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "context"),
            (path, json_output),
            document,
            availability="experimental",
            limitations=common_limitations,
        ),
        CommandSpec(
            "atlas.recipes-index",
            "atlas",
            "Rebuild recipe query index",
            "Validate one categorical graph and atomically rebuild only its disposable SQLite query storage under explicit byte and disk bounds.",
            "Atlas derived query-storage builder over immutable categorical graph evidence",
            "mutating",
            "inert-only",
            _workbench_template("atlas", "recipes", "index"),
            (
                path,
                _f(
                    "max_source_bytes",
                    "Maximum authoritative JSONL bytes read during rebuild.",
                    flags=("--max-source-bytes",),
                    kind="integer",
                    default=8589934592,
                ),
                _f(
                    "max_index_bytes",
                    "Maximum derived SQLite bytes; twice this value is preflighted as free staging disk.",
                    flags=("--max-index-bytes",),
                    kind="integer",
                    default=4294967296,
                ),
                _f(
                    "json",
                    "Emit the complete derived-index operation record.",
                    flags=("--json",),
                    kind="boolean",
                ),
            ),
            document,
            availability="experimental",
            limitations=(
                "The operation replaces only query-index.sqlite3 and the manifest query_index descriptor; graph identity and authoritative JSONL evidence remain unchanged.",
                "The bundle must be writable and have at least twice the declared maximum index size available for bounded staging and VACUUM.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-search",
            "atlas",
            "Search recipe evidence",
            "Find selectable recipes, resources, or source occurrences in one explicit evidence context.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "search"),
            (
                path,
                _f("query", "Literal recipe, resource, or source query.", positional=True, required=True),
                _f("limit", "Maximum result count from 1 through 10000.", flags=("--limit",), kind="integer", default=50),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *common_limitations,
                "Selection IDs are valid only within the exact evidence context that produced them.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-inspect",
            "atlas",
            "Inspect recipe selection",
            "Inspect one exact selection ID returned by Atlas recipe search.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "inspect"),
            (
                path,
                _f("selection_id", "Exact selection ID returned by recipe search.", positional=True, required=True),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *common_limitations,
                "Graph ownership paths are evidence labels, not workspace navigation authorization.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-audit-dead-ends",
            "atlas",
            "Audit captured recipes for dead ends",
            "Inspect every captured GT recipe for missing producer/use candidates, unresolved matching and circular dependencies.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "audit-dead-ends"),
            (
                _f("path", "Verified categorical V2 recipe graph root or manifest.", positional=True, kind="path", required=True),
                json_output,
                _f("csv", "Emit every recipe row as CSV; mutually exclusive with JSON.", flags=("--csv",), kind="boolean"),
            ),
            "modules/atlas/contracts/atlas-recipe-dead-ends-v1.md",
            availability="experimental",
            limitations=(
                "Audits the complete captured GT inventory without traversal or result limits; this is not whole-pack recipe-family coverage.",
                "No captured producer or use is a candidate, not proof of impossible acquisition or useless output. External acquisition and terminal uses remain unknown.",
                "Inactive/unknown recipes, incomplete matching, reusable inputs and chance outputs stay distinct. Cycle signals do not establish missing bootstrap supply.",
                "Reads an existing graph without launching Minecraft. Developer-local capture orchestration is separate work.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-routes",
            "atlas",
            "Trace captured recipe prerequisites",
            "Follow exact item or fluid producers, alternative recipes, reusable requirements and unresolved prerequisites.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "routes"),
            (
                _f("path", "Verified categorical V2 recipe graph root or manifest.", positional=True, kind="path", required=True),
                _f("selection_id", "Exact item or fluid selection ID returned by search in this graph.", positional=True, required=True),
                _f("max_depth", "Optional upstream recipe depth; zero retains the root. Omitted means no depth bound.", flags=("--max-depth",), kind="integer"),
                _f("max_resources", "Optional positive count of expanded resources.", flags=("--max-resources",), kind="integer"),
                _f("max_recipes", "Optional positive count of expanded recipe occurrences.", flags=("--max-recipes",), kind="integer"),
                json_output,
            ),
            "modules/atlas/contracts/atlas-captured-recipe-routes-v1.md",
            availability="experimental",
            limitations=(
                "Omitting bounds explores the complete admitted finite closure. Explicit bounds retain unexplored frontiers and never imply complete coverage.",
                "Cycles and missing observed producers do not prove deadlock or impossible acquisition; inventory, machine access and craftability remain unknown.",
                "The default display is a summary. Complete JSON may be very large; this command does not select recipes, balance quantities or order quests.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-impact",
            "atlas",
            "Analyze observed recipe impact",
            "Trace structural dead-path candidates if one exact observed finite recipe disappears.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "impact"),
            (
                path,
                _f(
                    "selection_id",
                    "Exact observed gt-recipe selection ID returned by recipe search.",
                    positional=True,
                    required=True,
                ),
                _f(
                    "exploration",
                    "Bounded V1 by default, or complete finite V2 exploration with evidence gaps retained.",
                    flags=("--exploration",),
                    kind="choice",
                    choices=("bounded", "complete-finite"),
                ),
                _f(
                    "max_depth",
                    "Bounded mode only: maximum propagation depth from 1 through 12; Atlas defaults to 4.",
                    flags=("--max-depth",),
                    kind="integer",
                ),
                _f(
                    "max_nodes",
                    "Bounded mode only: maximum observed nodes from 10 through 2000; Atlas defaults to 500.",
                    flags=("--max-nodes",),
                    kind="integer",
                ),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *common_limitations,
                "Impact models removal of the selected observed recipe; it does not model proposed replacement bytes.",
                "At-risk resources and recipes are structural candidates, never claims of broken or unreachable player progression.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-assess-plan",
            "atlas",
            "Assess proposed recipe plan",
            "Assess one retained, owner-validated recipe-change ADD plan against an explicit verified graph.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "assess-plan"),
            (
                path,
                _f(
                    "plan",
                    "Retained recipe-change plan ID or exact record path.",
                    positional=True,
                    required=True,
                ),
                _f(
                    "state_root",
                    "Optional retained developer-feature state root.",
                    flags=("--state-root",),
                    kind="path",
                ),
                _f(
                    "max_depth",
                    "Maximum observed dependency depth from 1 through 12.",
                    flags=("--max-depth",),
                    kind="integer",
                    default=4,
                ),
                _f(
                    "max_nodes",
                    "Maximum observed graph nodes to inspect from 10 through 2000.",
                    flags=("--max-nodes",),
                    kind="integer",
                    default=500,
                ),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *common_limitations,
                "The recipe-change owner validates the retained plan before Atlas receives a bounded generic proposal.",
                "This composition requires enabled Workbench Shell and the Supersymmetry plan owner; independent Atlas recipe queries do not.",
                "The proposed source bytes are not an observed runtime recipe; collisions, cycles, bypasses, and progression effects remain candidates or unknowns.",
            ),
        ),
        CommandSpec(
            "atlas.recipes-compare-runtime",
            "atlas",
            "Compare runtime recipe graphs",
            "Compare exact finite-recipe signatures and bounded progression exposure across two capture-compatible verified graphs.",
            authority,
            "read-only",
            "none",
            _workbench_template("atlas", "recipes", "compare-runtime"),
            (
                _f(
                    "before_path",
                    "Verified baseline categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f(
                    "after_path",
                    "Verified candidate categorical-graph root or manifest.",
                    positional=True,
                    kind="path",
                    required=True,
                ),
                _f("max_recipes", "Maximum finite recipes scanned per graph.", flags=("--max-recipes",), kind="integer", default=100000),
                _f("max_recipe_deltas", "Maximum exact signature delta details.", flags=("--max-recipe-deltas",), kind="integer", default=500),
                _f("max_resources", "Maximum resource-flow delta details.", flags=("--max-resources",), kind="integer", default=500),
                _f("max_depth", "Maximum bounded propagation depth.", flags=("--max-depth",), kind="integer", default=4),
                _f("max_nodes", "Maximum graph nodes inspected across both sides.", flags=("--max-nodes",), kind="integer", default=2000),
                json_output,
            ),
            document,
            availability="experimental",
            limitations=(
                *common_limitations,
                "Scope, adapter profile, and gt-recipes capture protocol must match exactly or Atlas emits no semantic delta.",
                "Added and removed immutable signatures are not paired into inferred changed recipes.",
                "Progression exposure is bounded observed structure, never proof that gameplay is broken, dead, or unreachable.",
            ),
        ),
    ]


def _atlas_observation_commands() -> list[CommandSpec]:
    """Present Atlas observation APIs without importing producer implementations."""

    document = "modules/atlas/contracts/atlas-observation-query-v1.md"
    authority = "Atlas exact observation queries over verified retained graph evidence"
    path = _f("path", "Explicit retained graph root or manifest; snapshot path for import.",
              positional=True, kind="path", required=True)
    selection = _f("selection_id", "Exact node ID returned by observation search.", positional=True, required=True)
    json_output = _f("json", "Emit the complete versioned record.", flags=("--json",), kind="boolean")
    paging = (
        _f("limit", "Records in this page, from 1 through 1000; more records remain accessible by cursor.", flags=("--limit",), kind="integer", default=50),
        _f("cursor", "Exact continuation bound to the graph and query.", flags=("--cursor",)),
    )
    definitions = (
        ("session", "Open observation query session", "Verify one graph once and answer serial JSONL requests until EOF, close or cancellation.", (path, json_output)),
        ("context", "Inspect observation context", "Inspect scope, declared coverage, limitations and index readiness.", (path, json_output)),
        ("search", "Search retained observations", "Find materials, registrations and recipe-family observations in one verified graph.",
         (path, _f("query", "Literal observation text.", positional=True, required=True),
          _f("kind", "Optional exact node kind.", flags=("--kind",)), *paging, json_output)),
        ("inspect", "Inspect one observation", "Read exact stored properties and available incoming/outgoing relationship kinds.", (path, selection, json_output)),
        ("crafting-exposure", "Inspect crafting reference exposure", "Find named recipes retaining a selected native value; stored references do not prove matching or gameplay impact.",
         (path, selection,
          _f("max_depth", "Optional positive traversal depth bound; omitted explores the finite closure.", flags=("--max-depth",), kind="integer"),
          _f("max_nodes", "Optional positive visited-node bound; omitted explores the finite closure.", flags=("--max-nodes",), kind="integer"), json_output)),
        ("relationships", "Follow observed relationships", "Read exact edges and selectable endpoints with their recorded relationship meanings.",
         (path, selection, _f("direction", "Relationship direction.", flags=("--direction",), kind="choice", choices=("incoming", "outgoing"), default="outgoing"),
          _f("relation", "Optional exact relation name.", flags=("--relation",)), *paging, json_output)),
        ("evidence", "Follow observation evidence", "Read retained references or explicitly reopen their originals through the selected owner adapter.",
         (path, selection, *paging,
          _f("snapshot", "Original retained source; requires pack-profile.", flags=("--snapshot",), kind="path"),
          _f("pack_profile", "Explicit evidence-reader profile; requires snapshot.", flags=("--pack-profile",)), json_output)),
        ("index", "Rebuild observation query index", "Validate authoritative streams and rebuild only disposable query storage.",
         (path, _f("max_source_bytes", "Maximum authoritative input bytes.", flags=("--max-source-bytes",), kind="integer", default=8589934592),
          _f("max_index_bytes", "Maximum index bytes, with twice this free staging disk required.", flags=("--max-index-bytes",), kind="integer", default=4294967296), json_output)),
        ("import-snapshot", "Import retained observations", "Admit an original snapshot through an explicitly selected profile into a new graph.",
         (path, _f("pack_profile", "Explicit observation-graph profile adapter.", flags=("--pack-profile",), required=True),
          _f("output", "New graph output directory.", flags=("--output",), kind="path", required=True),
          _f("side", "Exact retained side; unavailable sides are rejected.", flags=("--side",), kind="choice", choices=("single", "baseline", "candidate"), default="single"), json_output)),
    )
    return [CommandSpec(
        "atlas.observations-" + action, "atlas", title, description, authority,
        "mutating" if action in {"index", "import-snapshot"} else "read-only",
        "inert-only" if action in {"index", "import-snapshot"} else "none",
        _workbench_template("atlas", "observations", action), fields, document,
        availability="experimental", limitations=(
            "Stored observations and recorded relationships do not establish unobserved matching, source causation or gameplay viability.",
            "Original evidence resolution requires explicit source/profile selection and owner validation; references are not filesystem navigation authority.",
            "Import needs an admitted profile adapter; queries of existing graphs do not require the original producer or profile.",
        ),
    ) for action, title, description, fields in definitions]


def build_catalog(root: Path) -> Catalog:
    from workbench_api.profiles import profiles
    from workbench_registration_workbench_shell import module

    root = root.expanduser().resolve()
    profile_ids = frozenset(profile.id for profile in profiles())
    profile_requirements = tuple(sorted(module().capabilities, key=lambda capability: len(capability.command), reverse=True))
    suites = (
        SuiteSpec("shell", "Workbench Shell", "Command palette, retained sessions, ingestion, search, replay, and terminal presentation.", "Workbench Shell", "experimental"),
        SuiteSpec("new", "New Project", "Profile-owned fresh-project preview, consented construction, and retained recovery.", "Workbench Shell over Blueprints and explicit platform construction owners", "experimental"),
        SuiteSpec("dev", "Develop", "Build once and carry one exact constituent-mod candidate through applicable Supersymmetry client and dedicated-server runs.", "Workbench Shell + explicit Supersymmetry pack context", "experimental"),
        SuiteSpec("explorer", "Exact Runtime Explorer", "Cross-authority identity search with observed/static separation and source navigation.", "Workbench Shell projection over owning modules", "experimental"),
        SuiteSpec("pack-program", "Pack Program Studio", "GroovyScript lifecycle, dependency, effect, identity, comparison, reload, and exact compiler-diagnostic inspection.", "Workbench + explicit pack/platform profiles", "experimental"),
        SuiteSpec("doctor", "Workspace Doctor", "Exact project and capability context before mutation.", "Workbench Shell + Project Intelligence", "experimental"),
        SuiteSpec("cleanroom", "Cleanroom Fixture", "Exact profile-owned generic-mod build fixture.", "Cleanroom platform profile + Crucible", "experimental"),
        SuiteSpec("runs", "Managed Runs", "Pack-owned daily development recipes.", "Crucible", "experimental"),
        SuiteSpec("runtime", "Disposable Runtimes", "Fresh profile-bound runtime custody.", "Crucible", "experimental"),
        SuiteSpec("worlds", "Managed Worlds", "Snapshot and fresh-path restore.", "Crucible", "experimental"),
        SuiteSpec("storage", "Local Storage", "Inventory and recoverable cleanup.", "Crucible", "experimental"),
        SuiteSpec("world-studio", "World Studio", "Bounded iteration, subsurface inspection, and aligned baseline/candidate Worldgen Cockpit.", "Crucible + Strata + Atlas with profile authority", "experimental"),
        SuiteSpec("feature-studio", "Feature Studio", "Materials, fluids, registrations, and localization through owner-composed developer workflows.", "Workbench Shell composition over Blueprints, Atlas, profile authority, and Crucible", "experimental"),
        SuiteSpec("machine-studio", "Machine Studio", "Exact machine registration, recipe-map, structure-candidate, and source-navigation inspection with formed-world proof kept separate.", "Machine Studio composition over Atlas and Exact Runtime Explorer", "experimental"),
        SuiteSpec("process-studio", "Process Studio", "Forward Atlas's bounded finite-recipe and resource-flow comparison for two explicit, capture-compatible verified runtime graphs without changing its record or claims.", "Atlas owns graph verification and every observed or derived recipe-comparison claim; Process Studio owns only this workflow.", "experimental"),
        SuiteSpec("asset-studio", "Asset Studio", "Read-only local blockstate, model, texture, parent, and localization closure.", "Asset Studio", "experimental"),
        SuiteSpec("evolution-studio", "Evolution Studio", "Authority-preserving comparison across explicit runtime recipe evidence contexts.", "Evolution Studio route over Atlas", "experimental"),
        SuiteSpec("developer-features", "Developer Features", "Packaged examples and reversible, profile-owned developer construction transactions.", "Workbench Shell orchestration over Blueprints and explicit pack authority", "experimental"),
        SuiteSpec("change", "Feature Change Workspace", "Persistent intent, runtime matrix, explicit apply, verification, rollback, and recovery.", "Workbench Shell over Blueprints, explicit profile authority, and runtime owners", "experimental"),
        SuiteSpec("crucible", "Crucible Evidence Lab", "Exact runtime manifests, audits, matrices, health receipts, and proof assembly.", "Crucible", "experimental"),
        SuiteSpec("atlas", "Atlas", "Evidence-bounded runtime knowledge queries.", "Atlas", "experimental"),
        SuiteSpec("mixin", "Project Intelligence / Mixin", "Static topology, closure, compiler/AP custody, and exact Cleanroom policy evaluation.", "Project Intelligence + Cleanroom profile", "experimental"),
        SuiteSpec("blueprints", "Blueprints", "Canonical construction lifecycle.", "Blueprints", "experimental"),
        SuiteSpec("manuals", "Manuals", "Practical checked-in teaching beside tools.", "Manuals", "experimental"),
        SuiteSpec("sentinel", "Sentinel", "Plain-English, policy-backed findings without duplicating the underlying diagnostic authority.", "Sentinel presentation over declared owners", "experimental"),
        SuiteSpec("relay", "Relay", "Exact identity handoff from retained runtime evidence to owner-backed source or evidence locations.", "Relay transport over Atlas, Crucible, and Exact Runtime Explorer", "experimental"),
    )
    commands = [
        *_shell_commands(),
        *_feature_studio_commands(),
        *_process_studio_commands(),
        *_impact_studio_commands(),
        *_developer_feature_commands(),
        *_recipe_capture_commands(),
        *_atlas_scan_commands(),
        *_atlas_recipe_health_commands(),
        *_atlas_observation_commands(),
        *_public_commands(root),
        *_golden_journey_commands(),
        *_expert_commands(),
        *_manual_commands(root),
    ]
    return Catalog(root=root, suites=suites, commands=tuple(_admit_command(command, profile_ids, profile_requirements) for command in commands))


def _admit_command(command: CommandSpec, profile_ids: frozenset[str], requirements: tuple) -> CommandSpec:
    """Make missing profile choices explicit without disabling unrelated tools."""
    if command.argv_template[:2] == ("{python}", "{root}/tools/workbench.py"):
        tokens = command.argv_template[2:]
        selected = next((capability for capability in requirements if tokens[:len(capability.command)] == capability.command), None)
        missing = () if selected is None else tuple(sorted(set(selected.requires_profiles) - profile_ids))
        if selected and selected.requires_profiles:
            # Metadata cannot widen a concrete workflow's admitted owners.
            command = replace(command, fields=tuple(
                replace(field, choices=tuple(value for value in field.choices if value in selected.requires_profiles))
                if field.key == "profile" and field.kind == "choice" else field
                for field in command.fields
            ))
        if missing:
            command = replace(command, availability="unavailable", limitations=(*command.limitations, "Required profiles are disabled or unavailable: " + ", ".join(missing)))
    empty = tuple(field for field in command.fields if field.kind == "choice" and not field.choices)
    if not empty:
        return command
    required = tuple(field for field in empty if field.required or (
        field.required_group and not any(other.mutex_group == field.mutex_group and other not in empty for other in command.fields)
    ))
    return replace(
        command,
        fields=tuple(field for field in command.fields if field not in empty or field in required),
        availability="unavailable" if required else command.availability,
        limitations=(*command.limitations, "Unavailable profile-owned inputs: " + ", ".join(field.key for field in empty)),
    )


__all__ = [
    "Catalog",
    "CatalogError",
    "CommandSpec",
    "FORMAT_VERSION",
    "FieldSpec",
    "SuiteSpec",
    "build_catalog",
    "parse_assignments",
    "redact_argv",
]
