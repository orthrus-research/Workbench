"""Public capability inventory derived from registered Workbench handlers.

This catalog deliberately reports only product facts: registered suites,
commands, direct CLI handlers, availability, and exact action identity.
Development ledgers, checkpoints, and evidence bookkeeping are not runtime
inputs and are never projected into this record.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .catalog import Catalog, CommandSpec, build_catalog


FORMAT = "workbench-product-capability-catalog-v1"
SCHEMA_VERSION = 1
CATALOG_ID_PREFIX = "workbench-product-capabilities:sha256:"
DIRECT_ACTION_FORMAT = "workbench-direct-cli-action-binding-v1"
SCHEMA_RELATIVE = Path(
    "modules/workbench-shell/schemas/workbench-product-capability-catalog-v1.schema.json"
)


class ProductCapabilityCatalogError(ValueError):
    """A supplied capability catalog is malformed or stale."""


@dataclass(frozen=True)
class _DirectAction:
    """One public CLI route registered outside the console command palette.

    The command palette's ``CommandSpec`` model is intentionally not stretched
    around these multi-phase front doors.  Their parsers and consent flows live
    in ``tools/workbench.py``; this record gives the product catalog a stable,
    content-bound identity for that existing direct dispatch.
    """

    command_id: str
    suite_id: str
    title: str
    summary: str
    authority: str
    risk: str
    availability: str
    argv_prefix: tuple[str, ...]
    documentation: str
    limitations: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return True

    def action_digest(self, *, root: Path) -> str:
        # ``root`` is accepted to match CommandSpec's interface.  Direct action
        # identity deliberately uses repository-relative registration facts so
        # it is identical in a checkout, wheel, or WSL launcher.
        del root
        material = {
            "format": DIRECT_ACTION_FORMAT,
            "command_id": self.command_id,
            "suite_id": self.suite_id,
            "entrypoint": "tools/workbench.py",
            "argv_prefix": list(self.argv_prefix),
            "title": self.title,
            "summary": self.summary,
            "authority": self.authority,
            "risk": self.risk,
            "availability": self.availability,
            "documentation": self.documentation,
            "limitations": list(self.limitations),
        }
        return "sha256:" + sha256(_canonical_bytes(material)).hexdigest()


def _direct_actions() -> tuple[_DirectAction, ...]:
    """Return direct public handlers that do not belong in the console wizard."""

    return (
        _DirectAction(
            command_id="project.acquire",
            suite_id="shell",
            title="Acquire a declared project",
            summary=(
                "Plan and apply project acquisition by resolving a profile-owned "
                "channel to one immutable Git commit, then verifying the exact "
                "commit and required workspace shape before publishing the local "
                "destination."
            ),
            authority=(
                "Project Intelligence Git identity and workspace checks under "
                "explicit pack-profile authority; Workbench Shell owns routing"
            ),
            risk="mutating",
            availability="experimental",
            argv_prefix=("project", "acquire"),
            documentation="modules/project-intelligence/README.md",
            limitations=(
                "Remote resolution is a network observation; a non-interactive invocation without an exact --apply plan ID only prints the plan.",
                "A successful apply publishes only into the reviewed destination and retains its receipt under ignored or external Workbench state.",
                "The current public profile set contains only the explicitly selected Supersymmetry project.",
            ),
        ),
        _DirectAction(
            command_id="review.pull-request",
            suite_id="pack-program",
            title="Prepare and review a pull request",
            summary=(
                "Prepare pull request evidence from the profile-declared provider, "
                "retain exact base and head objects only after consent, and review "
                "the historical recipe delta without switching the checkout."
            ),
            authority=(
                "Project Intelligence owns provider and Git-ref custody; Pack "
                "Program Studio owns bounded recipe analysis; Workbench composes "
                "the two flows"
            ),
            risk="mutating",
            availability="experimental",
            argv_prefix=("review", "pr"),
            documentation="modules/project-intelligence/README.md",
            limitations=(
                "Planning and prepared-receipt review do not write refs or use the network; --yes or an exact --apply plan ID authorizes provider re-observation, fetch, immutable Workbench refs, and retained state.",
                "The checkout, current branch, index, and working-tree files are not changed.",
                "The current integrated provider and recipe-review adapter is explicitly Supersymmetry on GitHub.",
            ),
        ),
        _DirectAction(
            command_id="review.recipes",
            suite_id="pack-program",
            title="Review recipe changes",
            summary=(
                "Run recipe review over one explicit directory baseline, local Git "
                "baseline, merge base, or prepared pull-request receipt and one "
                "bounded candidate Groovy program."
            ),
            authority=(
                "Pack Program Studio static analysis over Project Intelligence "
                "Git observation and explicit pack/platform profiles"
            ),
            risk="writes-output",
            availability="experimental",
            argv_prefix=("review", "recipes"),
            documentation="modules/pack-program-studio/README.md",
            limitations=(
                "Local Git selectors never fetch; remote freshness requires a separately consented prepared receipt or the integrated pull-request review action.",
                "The review does not compile or execute Groovy and does not claim that a source statement ran.",
                "The candidate checkout is not mutated; --output may write only the explicitly selected fresh report.",
            ),
        ),
    )


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _identity(prefix: str, value: Mapping[str, Any]) -> str:
    return prefix + sha256(_canonical_bytes(value)).hexdigest()


def _capability(
    command: CommandSpec | _DirectAction,
    *,
    catalog: Catalog,
) -> dict[str, Any]:
    action = {
        "command_id": command.command_id,
        "suite_id": command.suite_id,
        "action_digest": command.action_digest(root=catalog.root),
    }
    handler_kind = "process" if command.executable else "document"
    material = {
        "capability_key": command.command_id,
        "title": command.title,
        "summary": command.summary,
        "authority": command.authority,
        "risk": command.risk,
        "availability": command.availability,
        "handler": {
            "kind": handler_kind,
            "registered": True,
            "executable": (
                command.executable and command.availability != "unavailable"
            ),
        },
        "catalog_action": action,
        "limitations": list(command.limitations),
    }
    return {
        "capability_id": _identity("capability:sha256:", material),
        **material,
    }


def build_product_capability_catalog(
    repository_root: Path | str,
) -> dict[str, Any]:
    """Build the compact public inventory from registered product handlers."""

    root = Path(repository_root).expanduser().resolve()
    catalog = build_catalog(root)
    actions: tuple[CommandSpec | _DirectAction, ...] = (
        *catalog.commands,
        *_direct_actions(),
    )
    command_ids = [action.command_id for action in actions]
    if len(command_ids) != len(set(command_ids)):
        raise ProductCapabilityCatalogError(
            "product capability actions contain duplicate command IDs"
        )
    suite_ids = {suite.suite_id for suite in catalog.suites}
    unknown_suites = sorted({action.suite_id for action in actions} - suite_ids)
    if unknown_suites:
        raise ProductCapabilityCatalogError(
            "product capability actions name unknown suites: "
            + ", ".join(unknown_suites)
        )
    counts = Counter(action.suite_id for action in actions)
    material = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "command_catalog_digest": catalog.catalog_digest,
        "suites": [
            suite.public_dict(counts[suite.suite_id])
            for suite in catalog.suites
        ],
        "capabilities": [
            _capability(action, catalog=catalog) for action in actions
        ],
    }
    catalog_id = _identity(CATALOG_ID_PREFIX, material)
    return {
        **material,
        "catalog_id": catalog_id,
    }


def validate_product_capability_catalog(
    value: Mapping[str, Any],
    *,
    repository_root: Path | str,
) -> dict[str, Any]:
    """Reject catalogs that differ from current registered command behavior."""

    if type(value) is not dict:
        raise ProductCapabilityCatalogError("capability catalog must be an object")
    expected = build_product_capability_catalog(repository_root)
    if value != expected:
        raise ProductCapabilityCatalogError(
            "capability catalog differs from the live registered handlers"
        )
    return dict(value)


def load_product_capability_catalog(
    repository_root: Path | str,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Load an explicit snapshot, or build the authoritative live catalog."""

    root = Path(repository_root).expanduser().resolve()
    if path is None:
        return build_product_capability_catalog(root)
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductCapabilityCatalogError(
            f"cannot read capability catalog snapshot: {candidate}"
        ) from exc
    return validate_product_capability_catalog(value, repository_root=root)


__all__ = [
    "CATALOG_ID_PREFIX",
    "DIRECT_ACTION_FORMAT",
    "FORMAT",
    "ProductCapabilityCatalogError",
    "SCHEMA_RELATIVE",
    "SCHEMA_VERSION",
    "build_product_capability_catalog",
    "load_product_capability_catalog",
    "validate_product_capability_catalog",
]
