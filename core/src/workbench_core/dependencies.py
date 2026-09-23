"""Validate installed dependency contracts without resolving or downloading."""

from importlib import metadata
from typing import Callable, Iterable, Mapping

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version


def dependency_errors(
    requirements: Iterable[str], *,
    replacements: Mapping[str, str | None] | None = None,
    version_lookup: Callable[[str], str] | None = None,
) -> list[str]:
    lookup = version_lookup or metadata.version
    replacements = {canonicalize_name(k): v for k, v in (replacements or {}).items()}
    errors = []
    for text in requirements:
        try:
            requirement = Requirement(text)
            if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
                continue
            if requirement.url:
                errors.append(f"direct-URL dependency is unsupported: {requirement.name}")
                continue
            name = canonicalize_name(requirement.name)
            actual = replacements[name] if name in replacements else lookup(requirement.name)
            if actual is None:
                raise metadata.PackageNotFoundError(requirement.name)
            if not requirement.specifier.contains(Version(actual), prereleases=True):
                errors.append(f"{requirement.name} {actual} does not satisfy {requirement.specifier}")
        except metadata.PackageNotFoundError:
            errors.append(f"missing dependency: {requirement.name}")
        except (InvalidRequirement, InvalidVersion, TypeError, ValueError) as exc:
            errors.append(f"invalid dependency metadata: {text!r}: {exc}")
    return errors


def reverse_dependency_errors(name: str, version: str | None) -> list[str]:
    """Protect every installed consumer, including disabled module packages."""
    selected = canonicalize_name(name)
    errors = []
    for distribution in metadata.distributions():
        owner = distribution.metadata.get("Name", "unknown")
        if canonicalize_name(owner) == selected:
            continue
        for text in distribution.requires or ():
            try:
                requirement = Requirement(text)
            except InvalidRequirement:
                errors.append(f"{owner}: invalid dependency metadata")
                continue
            if canonicalize_name(requirement.name) == selected:
                errors.extend(f"{owner}: {error}" for error in dependency_errors(
                    [text], replacements={selected: version},
                ))
    return errors
