"""Explicit Shell composition of runtime and construction owners."""

from workbench_blueprints.reviewed_plan import ReviewedPlanPorts
from workbench_crucible.runtime_pair import RuntimeExecutionServices


def material_fluid_construction_ports():
    from .developer_feature import (
        validate_material_fluid_recipe_plan,
        verify_material_fluid_recipe_plan,
        material_fluid_recipe_workspace,
    )

    return ReviewedPlanPorts(
        validate_material_fluid_recipe_plan,
        verify_material_fluid_recipe_plan,
        material_fluid_recipe_workspace,
    )


def installed_runtime_services():
    from workbench_core.artifact_store import sha256_file as sha256_file
    from workbench_shell.material_fluid_flow import (
        captured_disposable_groovy_log as captured_disposable_groovy_log,
    )
    from workbench_shell.material_fluid_flow import (
        disposable_runtime_receipt_evidence as disposable_runtime_receipt_evidence,
    )
    from workbench_shell.material_fluid_flow import (
        material_fluid_runtime_compatibility_policy as material_fluid_runtime_compatibility_policy,
    )
    from workbench_shell.material_fluid_flow import (
        summarize_disposable_runtime as summarize_disposable_runtime,
    )
    from workbench_shell.runtime_materialize import (
        INSTALLER_MAIN_CLASS as INSTALLER_MAIN_CLASS,
    )
    from workbench_shell.runtime_materialize import (
        PackwizMaterializationError as PackwizMaterializationError,
    )
    from workbench_shell.runtime_materialize import (
        _packwiz_optional_decisions as _packwiz_optional_decisions,
    )
    from workbench_shell.runtime_materialize import (
        _validate_refreshed_pack as _validate_refreshed_pack,
    )
    from workbench_shell.runtime_materialize import (
        _verify_packwiz_final_state as _verify_packwiz_final_state,
    )
    from workbench_shell.runtime_materialize import (
        _write_packwiz_initial_state as _write_packwiz_initial_state,
    )
    from workbench_project_intelligence.working_tree import (
        copy_tracked_workspace as copy_tracked_workspace,
    )
    from workbench_shell.runtime_observe import (
        observe_project_runtime as observe_project_runtime,
    )
    from workbench_shell.runtime_observe import (
        require_runtime_processes_closed as require_runtime_processes_closed,
    )
    from workbench_shell.runtime_plan import (
        plan_project_runtime as plan_project_runtime,
    )
    from workbench_shell.susy_mod_dev import _load_pack as _load_pack
    from workbench_shell.susy_mod_dev import _runtime_tree as _runtime_tree
    from workbench_shell.susy_mod_launch import (
        _stop_process_group as _stop_process_group,
    )
    from workbench_shell.susy_server_materialize import _copy_seed as _copy_seed
    from workbench_shell.susy_server_materialize import (
        _expected_server_mods as _expected_server_mods,
    )
    from workbench_shell.susy_server_materialize import (
        _run_owned_logged as _run_owned_logged,
    )
    from workbench_shell.susy_server_materialize import (
        _server_seed_paths as _server_seed_paths,
    )
    from workbench_shell.susy_server_materialize import (
        susy_server_materialization_version as susy_server_materialization_version,
    )
    from workbench_shell.susy_server_materialize import (
        verify_susy_server_materialization_receipt_identity as verify_susy_server_materialization_receipt_identity,
    )

    return RuntimeExecutionServices(
        sha256_file=sha256_file,
        captured_disposable_groovy_log=captured_disposable_groovy_log,
        disposable_runtime_receipt_evidence=disposable_runtime_receipt_evidence,
        material_fluid_runtime_compatibility_policy=material_fluid_runtime_compatibility_policy,
        summarize_disposable_runtime=summarize_disposable_runtime,
        INSTALLER_MAIN_CLASS=INSTALLER_MAIN_CLASS,
        PackwizMaterializationError=PackwizMaterializationError,
        packwiz_optional_decisions=_packwiz_optional_decisions,
        validate_refreshed_pack=_validate_refreshed_pack,
        verify_packwiz_final_state=_verify_packwiz_final_state,
        write_packwiz_initial_state=_write_packwiz_initial_state,
        copy_tracked_workspace=copy_tracked_workspace,
        observe_project_runtime=observe_project_runtime,
        require_runtime_processes_closed=require_runtime_processes_closed,
        plan_project_runtime=plan_project_runtime,
        load_pack=_load_pack,
        runtime_tree=_runtime_tree,
        stop_process_group=_stop_process_group,
        copy_seed=_copy_seed,
        expected_server_mods=_expected_server_mods,
        run_owned_logged=_run_owned_logged,
        server_seed_paths=_server_seed_paths,
        server_materialization_version=susy_server_materialization_version,
        verify_server_materialization_identity=verify_susy_server_materialization_receipt_identity,
    )
