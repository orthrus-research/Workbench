"""Reviewed behavior references, separate from suite execution and pass claims.

The map identifies important executable checks and their prerequisites. It is
not a line-coverage metric, an exhaustive test-value audit, or skip authority.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from suite_catalog import PYTHON_TEST_SUITES, ROOT, SUITES_BY_NAME


CATEGORIES = {
    "policy": "Documentation, manifests, source structure and declared policy.",
    "isolated-behavior": "Bounded logic or synthetic data with assertions on behavior.",
    "owner-integration": "Owner APIs, CLI, filesystem or cross-owner integration.",
    "installed-product": "Installed package/process lifecycle or installation boundaries.",
    "runtime-custody": "Runtime evidence, durable state, recovery and identity/custody boundaries.",
    "physical-runtime": "Real platform/build/runtime checks with explicit external prerequisites.",
}


@dataclass(frozen=True)
class CoverageArea:
    category: str
    behavior: str
    test_files: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()


def _area(category: str, behavior: str, *files: str, prerequisites: tuple[str, ...] = ()) -> CoverageArea:
    return CoverageArea(category, behavior, files, prerequisites)


# Paths are relative to each suite's test directory, and are validated against
# its exact file partition. Whole-file references deliberately avoid claiming
# that every test in a mixed file has the same category or prerequisites.
OWNER_COVERAGE: dict[str, tuple[CoverageArea, ...]] = {
    "axiom": (
        _area("owner-integration", "Engine installation identity, native registration and verbatim domain-result forwarding through the Core process port.", "test_integration.py"),
    ),
    "core-api": (
        _area("isolated-behavior", "Public contract/event/profile admission and rejection.", "test_contracts.py", "test_events.py", "test_profiles.py", "test_profile_extensions.py"),
        _area("owner-integration", "Host-port declarations and owner handoff boundaries.", "test_host_port.py"),
    ),
    "core": (
        _area("owner-integration", "Module admission, session ownership, execution and filesystem confinement.", "test_modules.py", "test_sessions.py", "test_runner.py", "test_host_filesystem.py", "test_service_ownership.py"),
        _area("runtime-custody", "Atomic storage publication and cross-platform wire-path rejection.", "test_storage_publication.py", "test_storage_wire_paths.py"),
        _area("installed-product", "Package lifecycle, installed provenance and installer confinement.", "test_package_lifecycle.py", "test_installed_provenance.py", "test_package_guard.py", prerequisites=("Real pip isolation case requires pip to be importable; Core-only installations may omit pip.",)),
    ),
    "validation": (
        _area("policy", "Public source/package boundaries, publication readiness and setup declarations.", "test_public_repository.py", "test_public_package.py", "test_publication_readiness.py", "test_pixi_setup.py"),
        _area("owner-integration", "Exact suite ownership, collection/report admission, timeout and interruption cleanup.", "test_test_suites.py", "test_run_python_suite.py", "test_validation_orchestration.py", "test_validation_scheduler.py"),
    ),
    "developer-feature": (
        _area("owner-integration", "Feature planning, transaction preview/application and rollback-safe workspace changes.", "test_developer_feature.py", "test_developer_feature_transaction_view.py", "test_feature_change_workspace.py"),
        _area("runtime-custody", "Runtime comparison, shared developer context and session selection preserve source/evidence boundaries.", "test_recipe_runtime_comparison.py", "test_feature_change_session_context.py", "test_developer_context.py", "test_source_navigation.py"),
    ),
    "project-intelligence": (
        _area("owner-integration", "Workspace/artifact inspection and exact build/dependency provenance.", "test_inspector.py", "test_workspace_doctor.py", "test_mixin_dependency_closure.py", "test_mixin_build_provenance.py"),
        _area("runtime-custody", "Git tree materialization, acquisition and invalid source/path rejection.", "test_git_tree.py", "test_project_acquisition.py", prerequisites=("Native Windows long-path cases require Windows.",)),
    ),
    "pack-program-studio": (
        _area("isolated-behavior", "Source declarations, exact UTF-16 locations and language-service diagnostics preserve pack ownership.", "test_source_declarations.py", "test_source_locations.py", "test_language_service.py"),
        _area("owner-integration", "Managed language sessions, review CLI and IDE handoffs.", "test_managed_language_session.py", "test_recipe_review_cli.py", "test_ide_bridge.py", "test_pack_program_studio.py", prerequisites=("Native Windows candidate/profile long-path case requires Windows.",)),
    ),
    "process-studio": (_area("owner-integration", "Bounded effect comparison rejects incompatible or insufficient observations.", "test_bounded_effect_comparison.py"),),
    "sentinel": (_area("owner-integration", "Owner diagnostics, strict exit status and invalid-archive rejection.", "test_cli.py"),),
    "relay": (_area("runtime-custody", "Exact retained-evidence navigation rejects tampering, ambiguity and static-only identities.", "test_cli.py"),),
    "service-conformance": (_area("policy", "Foundation relationships, package declarations and protocol fixture compatibility.", "test_foundation_relationships.py"),),
    "subsurface-studio": (_area("owner-integration", "Exact maps/sections, bounded explanation and aligned comparison reject identity/scope drift.", "test_subsurface_studio.py"),),
    "worldgen-cockpit": (_area("owner-integration", "Content-addressed comparisons distinguish reproducibility failures, seed mismatch and candidate effects.", "test_worldgen_cockpit.py"),),
    "worldgen-qualifier": (_area("runtime-custody", "Bounded qualification rejects missing evidence, changed controls and unsupported release coverage.", "test_worldgen_qualifier.py"),),
    "runtime-explorer": (_area("owner-integration", "Runtime provider/query/graph resolution and public CLI boundaries.", "test_query.py", "test_graph_query_v2.py", "test_providers.py", "test_cli.py"),),
    "cleanroom-profile": (
        _area("owner-integration", "Profile construction and Mixin diagnostics preserve exact source/epoch identity.", "test_cleanroom_new_project_v2.py", "test_mixin_doctor.py", "test_runtime_analysis_epochs.py"),
        _area("runtime-custody", "Captured transformer and defining-loader evidence is imported at its declared boundary.", "test_cleanmix_defining_loader_discovery_trace.py", "test_cleanmix_transformer_chain_capture.py"),
        _area("physical-runtime", "Real generic-mod build rejects corrupt artifacts; source-contract cases are separate.", "test_generic_mod_daily_loop_fixture.py", prerequisites=("WORKBENCH_CLEANROOM_FIXTURE_GRADLEW and WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME must name the declared fixture toolchain.",)),
    ),
    "supersymmetry-worldgen": (_area("policy", "Exact resource failure and cascading-review policy remains pack-owned.", "test_recurrent_complex_diagnostic_policy.py"),),
    "supersymmetry-blueprints": (_area("owner-integration", "Pack construction examples, quest/process binding and recipe-change generation.", "test_release_examples.py", "test_quest_for_process.py", "test_recipe_change.py"),),
    "supersymmetry-runtime": (_area("runtime-custody", "Material/fluid client and dedicated-server pair evidence remains bound to its owner.", "test_material_fluid_runtime_pair.py"),),
    "cleanroom-worldgen-observatory-fixture": (_area("policy", "Declared observatory candidate Java source contracts.", "test_source_contract.py"),),
    "cleanroom-worldgen-prototype-fixture": (
        _area("policy", "Declared prototype candidate Java source contracts.", "test_source_contract.py"),
        _area("isolated-behavior", "Log and JFR summaries/comparisons preserve the observed diagnostic boundary.", "test_log_summary.py", "test_log_comparison.py", "test_jfr_summary.py"),
    ),
    "manuals": (_area("policy", "Teaching requirements, authority wording and bounded guide/entry-point links.", "test_manuals_contract.py", "test_experimental_worldgen_cleanmix_guides.py"),),
    "atlas": (
        _area("owner-integration", "Corpus answers, source navigation and runtime route/recycling queries preserve exact evidence and structural bounds.", "test_corpus_bridge.py", "test_runtime_graph_chain_query.py", "test_runtime_graph_recycling_query.py", "test_atlas_query.py", "test_source_navigation.py"),
        _area("runtime-custody", "Reader identity, continuation recovery and comparison reject rebinding or stale evidence.", "test_runtime_graph_query.py", "test_atlas_continuation.py", "test_runtime_recipe_comparison_v1.py"),
        _area("isolated-behavior", "Causal/semantic projection keeps source, runtime and derived authority separate.", "test_semantic_projection.py", "test_atlas_causal_projection.py", "test_atlas_causal_provenance_contract.py"),
    ),
    "supersymmetry-atlas": (
        _area("isolated-behavior", "Pack semantic regressions and recipe registration/invalidation diagnoses.", "test_semantic_regressions.py", "test_gt_recipe_registration_diagnostic.py", "test_recipe_invalidation_diagnostic.py"),
        _area("runtime-custody", "Pack observation receipts and source/runtime material or quest comparisons.", "test_material_backed_fluid_observation.py", "test_material_fluid_recipe_observation.py", "test_betterquesting_source_runtime.py"),
    ),
    "validation-authority": (_area("owner-integration", "Product-open projects a Cleanroom workspace into developer jobs.", "test_product_open_authority.py"),),
    "validation-native-fixtures": (_area(
        "physical-runtime",
        "Original-input early, Groovy and selection stages execute in fresh sandbox workers.",
        "test_axiom_native_execution.py",
        prerequisites=("Explicit source-matched candidate, engine, JVM and new report roots for all three stages.",),
    ),),
    "workbench-shell": (
        _area("owner-integration", "Public CLI, service protocol/host and feature-session handoffs.", "test_cli.py", "test_service_protocol_v3_contracts.py", "test_service_host_v3.py", "test_work_session_v2.py"),
        _area("runtime-custody", "Materialization, executable custody and dependency identities reject drift.", "test_runtime_materialize_v2.py", "test_executable_custody.py", "test_installed_runtime_dependency_identity.py"),
        _area("installed-product", "Installed service start/attach/restart/upgrade/rollback, interruption and duplicate-owner rejection.", "test_installed_service_lifecycle_v3.py"),
        _area("physical-runtime", "Physical Cleanroom jobs retain public-session custody across frontend termination.", "test_product_spine_cli_v2.py", prerequisites=("Declared Cleanroom fixture toolchain and physical runtime opt-in; synthetic CLI cases are separate.",)),
    ),
    "blueprints": (
        _area("isolated-behavior", "Contracts, standards and registrations validate deterministic rendering inputs.", "test_contract.py", "test_standards.py", "test_registration_catalog.py", "test_registration_render.py"),
        _area("runtime-custody", "Filesystem publication preserves rollback, drift and no-side-effect gates.", "test_publication_filesystem.py"),
    ),
    "blueprints-native-fixtures": (
        _area("physical-runtime", "Engine conformance, interface, lifecycle and simulation exercise isolated execution on real fixture repositories.", "test_conformance.py", "test_interface.py", "test_lifecycle.py", "test_simulation.py", prerequisites=("Bubblewrap installed and permitted to create the sandbox namespaces used by Blueprints.",)),
    ),
    "crucible": (
        _area("runtime-custody", "Graph incremental reuse, independent reconstruction, recovery, custody and bounded ancestry.", "test_crucible_worldgen_v2.py", "test_crucible_job_v2_contracts.py", "test_cleanroom_runtime_custody.py", "test_runtime_manager_journal.py"),
        _area("owner-integration", "Service lifecycle and runtime manager cancellation/process isolation.", "test_crucible_service_v3.py", "test_runtime_manager.py"),
        _area("isolated-behavior", "Canonical/API contracts and cross-language vectors preserve exact byte identities.", "test_crucible_v2_contracts.py", "test_crucible_v2_canonical.py", "test_crucible_v2_cross_language.py", prerequisites=("Node is required by the canonical cross-language tests.",)),
    ),
}


def coverage_inventory(*, root: Path | None = None) -> dict[str, object]:
    repository = Path(root) if root is not None else ROOT
    if set(OWNER_COVERAGE) != set(SUITES_BY_NAME):
        raise ValueError("coverage owners must match the current suite catalog exactly")
    rows = []
    for suite in PYTHON_TEST_SUITES:
        assigned = {path.name for path in suite.test_files()}
        references: set[str] = set()
        areas = []
        for area in OWNER_COVERAGE[suite.name]:
            if area.category not in CATEGORIES or not area.behavior or not area.test_files:
                raise ValueError(f"invalid coverage area for {suite.name}")
            if set(area.test_files) - assigned:
                raise ValueError(f"coverage references unowned test files for {suite.name}: {sorted(set(area.test_files)-assigned)}")
            for name in area.test_files:
                if not (repository / suite.start_dir / name).is_file():
                    raise ValueError(f"coverage test file is missing: {suite.start_dir}/{name}")
            references.update(area.test_files)
            document = asdict(area)
            document["test_files"] = [f"{suite.start_dir}/{name}" for name in area.test_files]
            document["prerequisites"] = list(area.prerequisites)
            areas.append(document)
        rows.append({
            "suite": suite.name, "authority": suite.authority, "purpose": suite.purpose,
            "areas": areas,
            "other_assigned_test_files": [f"{suite.start_dir}/{name}" for name in sorted(assigned-references)],
            "temporary_storage": "repository" if suite.repository_temp else "external",
            "exclusive": suite.exclusive, "resource_locks": list(suite.resource_locks),
        })
    return {
        "format": "workbench-behavior-coverage-map-v1",
        "categories": CATEGORIES,
        "suites": rows,
        "limitations": [
            "References identify important checks; they do not prove those checks ran or exhaust all behaviors.",
            "A mixed test file can contribute to multiple categories; prerequisites apply to its relevant cases.",
            "Other assigned files remain in their existing suite and required lane; none are deleted or omitted by this map.",
            "Category declarations do not change skip policy, scheduling, or runtime/release qualification.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(coverage_inventory(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
