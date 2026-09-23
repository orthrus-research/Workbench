"""Consumer-owned native construction contracts.

Profiles implement construction policy. Admission and API compatibility are
checked at each operation; neither this module nor Core imports a concrete pack.
"""

from typing import Any

from workbench_api.profile_extensions import (
    ProfileExtensionError,
    require_profile_extension,
)


def _authority(group: str, profile_id: str, methods: tuple[str, ...]) -> Any:
    owner = require_profile_extension(group, profile_id)
    if any(not callable(getattr(owner, name, None)) for name in methods):
        raise ProfileExtensionError(
            f"{profile_id} has an incomplete construction contract"
        )
    return owner


def recipe_change_authority(profile_id: str) -> Any:
    return _authority(
        "workbench.recipe_changes",
        profile_id,
        (
            "recipe_change_options",
            "build_recipe_change_plan",
            "validate_recipe_change_plan",
            "verify_recipe_change_plan",
            "apply_recipe_change_plan",
            "rollback_recipe_change",
            "recover_recipe_change",
        ),
    )


def quest_change_authority(profile_id: str) -> Any:
    return _authority(
        "workbench.quest_changes",
        profile_id,
        (
            "discover_quest_for_process_options",
            "render_quest_for_process_update",
            "validate_quest_for_process_render",
        ),
    )
