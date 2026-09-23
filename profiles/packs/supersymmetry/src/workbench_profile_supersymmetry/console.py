"""Supersymmetry-owned selectable console inputs and experiment policy."""
from pathlib import Path

from workbench_api.console_policy import ConsolePolicy


COMPATIBILITY_EXPERIMENTS = {
    "susy-reccomplex-arg3": Path("profiles/packs/supersymmetry/compatibility/recurrent-complex-1.4.8.6-structure-context-arg3-v1.json"),
    "susy-reccomplex-susycore-0112-flag": Path("profiles/packs/supersymmetry/compatibility/susycore-0.1.112-reccomplex-flag-v1.json"),
}
SERVER_SHUTDOWN_EXPERIMENT = "susy-server-shutdown-bridge"
SERVER_EXPERIMENTS = frozenset(COMPATIBILITY_EXPERIMENTS) | {SERVER_SHUTDOWN_EXPERIMENT}
RECCOMPLEX_EXPERIMENT_BY_PACK_VERSION = {
    "0.1.16.11": "susy-reccomplex-arg3",
    "0.1.16.12": "susy-reccomplex-susycore-0112-flag",
}


def policy() -> ConsolePolicy:
    from .examples import EXAMPLE_KEYS

    return ConsolePolicy(
        profile_id="supersymmetry",
        roles=("qualification", "cockpit", "subsurface"),
        compatibility_experiments={key: key for key in COMPATIBILITY_EXPERIMENTS},
        server_experiments=tuple(sorted(SERVER_EXPERIMENTS)),
        examples=EXAMPLE_KEYS,
    )
