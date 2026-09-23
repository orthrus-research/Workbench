"""Explicit profile resource declarations supplied by installed packages."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from importlib import metadata
from pathlib import Path
import re
from typing import Iterable, Mapping

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from .modules import ModuleError


@dataclass(frozen=True)
class Profile:
    id: str
    kind: str
    root: Path
    resources: Mapping[str, str]
    api_version: int = 1

    def resource(self, role: str) -> Path:
        relative = Path(self.resources[role])
        if relative.is_absolute() or ".." in relative.parts:
            raise ModuleError("profile resource escapes its owner")
        result = self.root / relative
        if not result.is_file() or result.is_symlink() or not result.resolve().is_relative_to(self.root.resolve()):
            raise ModuleError(f"profile {self.id!r} is missing its {role!r} resource")
        return result


_disabled: ContextVar[frozenset[str]] = ContextVar("workbench_disabled_profiles", default=frozenset())
_unavailable_distributions: ContextVar[frozenset[str]] = ContextVar("workbench_unavailable_distributions", default=frozenset())


@contextmanager
def profile_scope(*, disabled: Iterable[str] = (), unavailable_distributions: Iterable[str] = ()):
    """Bind host-selected admission policy without API-owned mutable state."""
    token = _disabled.set(frozenset(disabled))
    unavailable_token = _unavailable_distributions.set(frozenset(canonicalize_name(name) for name in unavailable_distributions))
    try:
        yield
    finally:
        _disabled.reset(token)
        _unavailable_distributions.reset(unavailable_token)


def require_optional_distribution(requirement: str) -> dict[str, str]:
    """Admit an explicitly selected optional integration under host policy.

    Installing an extra does not bypass the disabled/unavailable module policy
    Core supplies through ``profile_scope``. This check performs no imports or
    execution of the optional provider.
    """
    selected = Requirement(requirement)
    if selected.url or selected.marker or selected.extras:
        raise ModuleError("optional integration requires an explicit versioned distribution")
    if canonicalize_name(selected.name) in _unavailable_distributions.get():
        raise ModuleError(f"optional integration is disabled or unavailable: {selected.name}")
    try:
        version = metadata.version(selected.name)
    except metadata.PackageNotFoundError as exc:
        raise ModuleError(f"optional integration is not installed: {selected.name}") from exc
    if not selected.specifier.contains(version, prereleases=True):
        raise ModuleError(f"optional integration version is incompatible: {selected.name}")
    return {"distribution": canonicalize_name(selected.name), "version": version}


@dataclass(frozen=True)
class ProfileStatus:
    id: str
    distribution: str
    version: str
    state: str
    reason: str = ""
    profile: Profile | None = None

    def record(self) -> dict[str, object]:
        return {"id": self.id, "distribution": self.distribution, "version": self.version,
                "state": self.state, "reason": self.reason,
                "kind": self.profile.kind if self.profile else None,
                "resources": sorted(self.profile.resources) if self.profile else []}


def profile_status(*, entries=None, disabled: Iterable[str] | None = None) -> tuple[ProfileStatus, ...]:
    """Isolate invalid optional profiles and reject every duplicate claimant."""
    selected = tuple(metadata.entry_points(group="workbench.profiles") if entries is None else entries)
    disabled_ids = _disabled.get() if disabled is None else frozenset(disabled)
    counts = {entry.name: sum(other.name == entry.name for other in selected) for entry in selected}
    result = []
    requirements_by_id = {}
    for entry in sorted(selected, key=lambda entry: entry.name):
        distribution = version = "unknown"
        try:
            if entry.dist is None:
                raise ModuleError("profile has no installed distribution metadata")
            distribution, version = entry.dist.metadata["Name"], entry.dist.version
            if not distribution or not version or not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", entry.name):
                raise ModuleError("profile distribution identity is incomplete")
            if counts[entry.name] != 1:
                raise ModuleError("profile ID has multiple installed owners")
            if entry.name in disabled_ids:
                result.append(ProfileStatus(entry.name, distribution, version, "disabled"))
                continue
            required = set()
            for text in entry.dist.requires or ():
                requirement = Requirement(text)
                if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                    continue
                if requirement.url or not requirement.specifier.contains(metadata.version(requirement.name), prereleases=True):
                    raise ModuleError(f"incompatible dependency: {requirement.name}")
                required.add(canonicalize_name(requirement.name))
            requirements_by_id[entry.name] = required
            if required & _unavailable_distributions.get():
                raise ModuleError("required module distribution is disabled or unavailable")
            profile = entry.load()()
            if not isinstance(profile, Profile) or profile.id != entry.name or type(profile.api_version) is not int or profile.api_version != 1 or profile.kind not in {"pack", "platform"} or not isinstance(profile.root, Path) or not isinstance(profile.resources, Mapping):
                raise ModuleError("profile declaration is incompatible")
            for role in profile.resources:
                profile.resource(role)
            result.append(ProfileStatus(profile.id, distribution, version, "available", profile=profile))
        except (Exception, SystemExit) as exc:
            result.append(ProfileStatus(entry.name, distribution, version, "unavailable", f"{type(exc).__name__}: {exc}"))
    while True:
        unavailable = {canonicalize_name(row.distribution) for row in result if row.state != "available"}
        rejected = {row.id for row in result if row.profile and requirements_by_id[row.id] & unavailable}
        if not rejected:
            break
        result = [ProfileStatus(row.id, row.distribution, row.version, "unavailable", "required profile is disabled or unavailable")
                  if row.id in rejected else row for row in result]
    return tuple(result)


def profiles(*, disabled: Iterable[str] | None = None) -> tuple[Profile, ...]:
    return tuple(row.profile for row in profile_status(disabled=disabled) if row.profile is not None)


def profile_resources(role: str) -> dict[str, Path]:
    return {profile.id: profile.resource(role) for profile in profiles() if role in profile.resources}
