"""Atlas-owned contracts for admitted native recipe interpreters."""

from typing import Any

from workbench_api.profile_extensions import (
    ProfileExtensionError,
    require_profile_extension,
)


def recipe_observer(profile_id: str) -> Any:
    owner = require_profile_extension("workbench.recipe_observers", profile_id)
    methods = (
        "build_recipe_change_probe",
        "build_recipe_change_probe_overlay",
        "compare_recipe_change_observations",
        "derive_recipe_change_probe_spec",
        "interpret_recipe_change_observation",
        "validate_recipe_change_assessment",
        "validate_recipe_change_comparison",
    )
    if any(not callable(getattr(owner, name, None)) for name in methods):
        raise ProfileExtensionError(
            f"{profile_id} has an incomplete recipe observation contract"
        )
    return owner
