"""Optional Shell composition for Atlas's owner-validated recipe plans.

Independent recipe commands and their presentation live in Atlas. This legacy
Python entry point also accepts them for existing Shell compositions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence, TextIO

from workbench_api import ExecutionContext
from workbench_api.resources import repository_root
from workbench_atlas_recipe_health.cli import (
    RecipePlanAssessmentAdapter,
    build_parser,
    main as atlas_main,
)


def main(
    argv: Sequence[str] | None = None,
    *,
    suite_root: Path | str | None = None,
    output: TextIO | None = None,
    error: TextIO | None = None,
    context: ExecutionContext | None = None,
) -> int:
    suite = (
        repository_root(__file__)
        if suite_root is None
        else Path(suite_root).expanduser().resolve()
    )

    def assess(
        graph_root: Path,
        plan_reference: str,
        *,
        state_root: Path | None,
        max_depth: int,
        max_nodes: int,
    ) -> dict[str, Any]:
        from .atlas_recipe_plan_assessment import assess_recipe_change_plan
        from .developer_feature import default_feature_state_root

        return assess_recipe_change_plan(
            suite,
            graph_root,
            default_feature_state_root(suite) if state_root is None else state_root,
            plan_reference,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )

    return atlas_main(
        argv,
        plan_adapter=RecipePlanAssessmentAdapter(assess),
        context=context,
        output=output,
        error=error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
