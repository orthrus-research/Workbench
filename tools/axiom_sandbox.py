"""Core-owned worker selection for standalone Axiom build/conformance tools."""

from contextlib import contextmanager
from pathlib import Path
import sys
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "api/src", ROOT / "core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

from workbench_api.state_paths import default_suite_state_root
from workbench_core.axiom_sandbox import open_axiom, close_axiom


@contextmanager
def selected_worker(backend: str):
    if backend == "bubblewrap":
        yield ()
        return
    selection = open_axiom(backend, state_root=default_suite_state_root(ROOT) / "axiom/standalone-sandbox",
                           cancelled=Event())
    try:
        yield selection.jvm_arguments
    finally:
        close_axiom(selection)
