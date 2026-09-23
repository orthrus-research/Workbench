"""Optional retained Axiom observations; no native execution or Shell dependency."""
from contextlib import contextmanager
from pathlib import Path

PROFILE_API_VERSION = 1
OBSERVATION_GRAPH_API_VERSION = 1


@contextmanager
def _open(path, check_cancelled):
    from workbench_api.profiles import require_optional_distribution
    from workbench_api.retained_snapshots import retained_snapshot_provider
    require_optional_distribution("workbench-axiom>=0.1.1,<0.2.0")
    provider = retained_snapshot_provider("core", "workbench-core>=0.1.4,<0.2.0")
    try:
        from workbench_axiom.retained_evidence import admit_retained_snapshot
    except ImportError as exc:
        raise ValueError(
            "Axiom observation import requires the profile's optional observations "
            "integration and Core retained snapshot reader"
        ) from exc
    check = check_cancelled or (lambda: None)

    def cancelled():
        check()
        return False

    check()
    with provider.open_snapshot(Path(path), snapshot_id=None, owner_id="axiom",
            admit=admit_retained_snapshot, cancelled=cancelled) as reader:
        context = reader.request["inputs"]["context"]
        if (not isinstance(context.get("id"), str)
                or not context["id"].startswith("supersymmetry:")
                or context.get("platformProfile") != "cleanroom"):
            raise ValueError("retained observations do not belong to the selected Supersymmetry profile")
        yield reader


def project_snapshot(path, output, *, side="single", check_cancelled=None):
    from workbench_atlas_observations.projection import project_retained_observations
    with _open(path, check_cancelled) as reader:
        return project_retained_observations(reader, output, side=side,
            profile_id="supersymmetry", check_cancelled=check_cancelled)


def resolve_evidence(path, references, *, check_cancelled=None):
    from workbench_atlas_observations.projection import resolve_retained_evidence
    with _open(path, check_cancelled) as reader:
        return resolve_retained_evidence(reader, references, check_cancelled=check_cancelled)
