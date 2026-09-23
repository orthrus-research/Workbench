"""Bind the retained Cleanroom runtime implementation to this pack profile."""

from types import SimpleNamespace
from workbench_api.runtime import RuntimeProviderError
from workbench_api.resources import repository_root
from . import profile


def provider():
    from workbench_crucible_worldgen_iteration import iteration
    selected = profile()

    def resolve_profile_path(_root, name):
        if name != selected.id:
            raise RuntimeProviderError("this provider only owns Supersymmetry")
        return selected.resource("worldgen")

    def load_profile(path, _root):
        if path.resolve() != selected.resource("worldgen").resolve():
            raise RuntimeProviderError("runtime profile is outside the selected package")
        return iteration.load_profile(path, repository_root(__file__))

    return SimpleNamespace(
        api_version=1,
        resolve_profile_path=resolve_profile_path,
        load_profile=load_profile,
        **{name: getattr(iteration, name) for name in (
            "discover_runtime_template",
            "audit_runtime_template", "provision_runtime", "configure_runtime",
        )},
    )
