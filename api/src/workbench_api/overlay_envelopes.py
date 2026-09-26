"""Core-bound custody port for a staged, whole-envelope overlay attempt."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Callable, Iterable, Iterator, Mapping, Protocol

from .managed_trees import ManagedTreeReference, ManagedTreeStage, ManagedTrees


class OverlayEnvelopeAttempt(Protocol):
    attempt_id: str

    def emit_chunk(self, ordinal: int, raw: bytes) -> None: ...

    def seal_inputs(
        self, manifest: Mapping[str, object], *,
        validate_inventory: Callable[[Mapping[str, object], Iterable[bytes]], object],
    ) -> object: ...

    def seal_effects(
        self, effects: tuple[dict[str, object], ...], *,
        validate_plan: Callable[[Iterable[bytes]], object],
    ) -> object: ...

    def copy_source(
        self, *, verify_source: Callable[[Mapping[str, object], Iterable[bytes]], object],
    ) -> Path: ...

    def apply_effects(self, effects: tuple[dict[str, object], ...]) -> Path: ...

    def write_siblings(
        self, *, inventory_bytes: bytes, materialization_bytes: bytes,
        validate_output: Callable[[Path, Iterable[bytes]], object],
    ) -> tuple[Path, Path]: ...

    def publish_envelope(
        self, *, validate_output: Callable[[Path, Iterable[bytes]], object],
    ) -> ManagedTreeReference: ...


class OverlayEnvelopes(Protocol):
    def start(
        self, *, stage: ManagedTreeStage, source_root: Path,
        plan_chunks: Iterable[bytes], content_root: str = "gregtech",
    ) -> OverlayEnvelopeAttempt: ...

    def inventory(self) -> tuple[dict[str, object], ...]: ...

    def reconcile_publication(
        self, *, attempt_id: str, trees: ManagedTrees,
    ) -> ManagedTreeReference: ...


_bound: ContextVar[OverlayEnvelopes | None] = ContextVar(
    "workbench_overlay_envelopes", default=None,
)


@contextmanager
def overlay_envelopes_scope(provider: OverlayEnvelopes) -> Iterator[None]:
    token = _bound.set(provider)
    try:
        yield
    finally:
        _bound.reset(token)


def overlay_envelopes() -> OverlayEnvelopes:
    provider = _bound.get()
    if provider is None:
        raise RuntimeError("no overlay envelope host is bound; invoke through Workbench Core")
    return provider


__all__ = [
    "OverlayEnvelopeAttempt", "OverlayEnvelopes", "overlay_envelopes",
    "overlay_envelopes_scope",
]
