"""World Studio composition contract, independent of its host."""

from dataclasses import dataclass
from typing import Any, Mapping

from workbench_api.canonical import canonical_json_bytes
from workbench_api.service import ServiceHandlerRegistration

@dataclass(frozen=True, slots=True)
class WorldStudioRegistryBundleV3:
    registry: Mapping[str, Any]
    source_tree_manifest: Mapping[str, Any]
    dependency_lock_manifest: Mapping[str, Any]
    health_receipt: Mapping[str, Any]
    service_distribution: Mapping[str, Any]


_PRESENTER_BINDING_MINT = object()


def _bundle_bytes(bundle: WorldStudioRegistryBundleV3) -> bytes:
    return canonical_json_bytes({
        "registry": bundle.registry,
        "source_tree_manifest": bundle.source_tree_manifest,
        "dependency_lock_manifest": bundle.dependency_lock_manifest,
        "health_receipt": bundle.health_receipt,
        "service_distribution": bundle.service_distribution,
    })


@dataclass(frozen=True, slots=True, init=False)
class WorldStudioPresenterBindingV3:
    """In-process producer capability, not a serializable authority assertion.

    The trusted host producer reconstructs its current registry and mints this
    binding. Receiving a bundle, JSON or matching content hashes cannot create
    it. This is an API custody boundary, not a Python security sandbox.
    """

    composition: WorldStudioRegistryBundleV3
    registration: ServiceHandlerRegistration
    _composition_bytes: bytes
    _mint: object

    def __init__(
        self,
        composition: WorldStudioRegistryBundleV3,
        registration: ServiceHandlerRegistration,
        *,
        _mint: object = None,
    ) -> None:
        if _mint is not _PRESENTER_BINDING_MINT:
            raise ValueError("World Studio presenter binding requires its trusted producer")
        if (
            type(composition) is not WorldStudioRegistryBundleV3
            or type(registration) is not ServiceHandlerRegistration
        ):
            raise ValueError("World Studio presenter binding has invalid owners")
        object.__setattr__(self, "composition", composition)
        object.__setattr__(self, "registration", registration)
        object.__setattr__(self, "_composition_bytes", _bundle_bytes(composition))
        object.__setattr__(self, "_mint", _mint)

    def matches_current_composition(self) -> bool:
        try:
            return (
                self._mint is _PRESENTER_BINDING_MINT
                and self._composition_bytes == _bundle_bytes(self.composition)
            )
        except (AttributeError, TypeError, ValueError, RecursionError):
            return False


def _mint_world_studio_presenter_binding(
    composition: WorldStudioRegistryBundleV3,
    registration: ServiceHandlerRegistration,
) -> WorldStudioPresenterBindingV3:
    """Private owner seam for the trusted host's reconstructing factory only."""

    from .view import (
        WorldStudioProvingViewHandler,
        validate_world_studio_proving_request,
        validate_world_studio_proving_result,
    )
    if not (
        type(registration) is ServiceHandlerRegistration
        and type(registration.handler) is WorldStudioProvingViewHandler
        and registration.method == "graph/query"
        and registration.capability_version == "1.0.0"
        and registration.mutation_boundary == "none"
        and registration.asynchronous is False
        and registration.maximum_concurrency == 4
        and registration.context_binding == "required"
        and registration.input_binding == "required"
        and registration.request_validator is validate_world_studio_proving_request
        and registration.result_validator is validate_world_studio_proving_result
    ):
        raise ValueError("World Studio presenter binding requires its genuine owner registration")
    return WorldStudioPresenterBindingV3(
        composition, registration, _mint=_PRESENTER_BINDING_MINT
    )
