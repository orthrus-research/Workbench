"""Admission and dispatch of installed Workbench modules."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, replace
from importlib import import_module, metadata
from typing import Iterable, Sequence

from workbench_api import Capability, ExecutionContext, Module, ModuleError
from .dependencies import dependency_errors

RESERVED_COMMANDS = frozenset({"setup", "settings", "repair", "environment", "modules", "profiles", "version", "storage", "runtime", "world"})


@dataclass(frozen=True)
class InstalledModule:
    id: str
    distribution: str
    version: str
    state: str
    reason: str = ""
    module: Module | None = None

    def record(self) -> dict[str, object]:
        return {
            "id": self.id, "distribution": self.distribution,
            "version": self.version, "state": self.state, "reason": self.reason,
            "capabilities": [c.id for c in self.module.capabilities] if self.module else [],
        }


def discover(*, entries: Iterable[metadata.EntryPoint] | None = None, disabled: Iterable[str] = ()) -> tuple[InstalledModule, ...]:
    selected = metadata.entry_points(group="workbench.modules") if entries is None else entries
    disabled_ids = frozenset(disabled)
    result: list[InstalledModule] = []
    seen: set[str] = set()
    for entry in sorted(selected, key=lambda e: (e.name, e.value)):
        distribution = version = "unknown"
        try:
            if entry.dist is None:
                raise ModuleError("module has no installed distribution metadata")
            distribution = entry.dist.metadata["Name"]
            version = entry.dist.version
            if not distribution or not version:
                raise ModuleError("module distribution identity is incomplete")
            if entry.name in seen:
                raise ModuleError(f"duplicate installed module: {entry.name}")
            seen.add(entry.name)
            if entry.name in disabled_ids:
                result.append(InstalledModule(entry.name, distribution, version, "disabled"))
                continue
            failures = dependency_errors(getattr(entry.dist, "requires", None) or ())
            if failures:
                raise ModuleError("; ".join(failures))
            factory = entry.load()
            module = factory()
            if not isinstance(module, Module) or module.id != entry.name or module.version != version:
                raise ModuleError("module descriptor does not match its installed metadata")
            if any(c.command[0] in RESERVED_COMMANDS for c in module.capabilities):
                raise ModuleError("module shadows a Core command")
            result.append(InstalledModule(module.id, distribution, version, "available", module=module))
        except (Exception, SystemExit) as exc:
            result.append(InstalledModule(entry.name, distribution, version, "unavailable", f"{type(exc).__name__}: {exc}"))
    duplicates = {row.id for row in result if sum(r.id == row.id for r in result) > 1}
    commands: dict[tuple[str, ...], list[str]] = {}
    capability_ids: dict[str, list[str]] = {}
    for row in result:
        if row.module:
            for capability in row.module.capabilities:
                commands.setdefault(capability.command, []).append(row.id)
                capability_ids.setdefault(capability.id, []).append(row.id)
    conflicts = duplicates | {owner for owners in [*commands.values(), *capability_ids.values()] if len(owners) > 1 for owner in owners}
    # Missing, failed, disabled, cyclic, or conflicting dependencies cannot be
    # admitted merely because their distributions are present.
    admitted: set[str] = set()
    while True:
        next_ids = {r.id for r in result if r.module and r.id not in conflicts and set(r.module.requires) <= admitted}
        if next_ids <= admitted:
            break
        admitted |= next_ids
    return tuple(
        InstalledModule(r.id, r.distribution, r.version, "unavailable", "conflicting command or missing/cyclic dependency")
        if r.module and r.id not in admitted else r
        for r in result
    )


def dispatch(arguments: Sequence[str], context: ExecutionContext, modules: Sequence[InstalledModule]) -> int:
    matches: list[tuple[InstalledModule, Capability]] = []
    for row in modules:
        if row.state == "available" and row.module:
            matches.extend((row, c) for c in row.module.capabilities if tuple(arguments[:len(c.command)]) == c.command)
    if not matches:
        raise ModuleError("no installed module provides this command")
    owner, capability = max(matches, key=lambda pair: len(pair[1].command))
    if capability.requires_profiles:
        from workbench_api.profiles import profiles
        missing = set(capability.requires_profiles) - {profile.id for profile in profiles()}
        if missing:
            raise ModuleError("command requires enabled, admitted profiles: " + ", ".join(sorted(missing)))
    context.check_cancelled()
    package, name = capability.handler.split(":")
    if "logs" in context.locations:
        from .output_routing import OutputInvocation
        recording = OutputInvocation(
            context.locations, owner.id, capability.id, workspace=context.workspace
        )
    else:
        recording = nullcontext(None)
    with recording as invocation:
        operation_context = (
            replace(context, output_resolver=invocation.output_path)
            if invocation is not None else context
        )
        try:
            handler = getattr(import_module(package), name)
            result = handler(list(arguments[len(capability.command):]), context=operation_context)
        except SystemExit as exc:
            if type(exc.code) is int and 0 <= exc.code <= 255:
                result = exc.code
            else:
                raise ModuleError(f"capability {capability.id} exited without a valid status") from exc
        except Exception as exc:
            raise ModuleError(f"capability {capability.id} is unavailable: {exc}") from exc
        if type(result) is not int:
            raise ModuleError(f"capability {capability.id} returned an invalid exit code")
        if invocation is not None:
            invocation.exit_code = result
        return result
