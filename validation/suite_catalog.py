"""Canonical Python test-suite catalog for Workbench validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_FILE_NAME_PATTERN = re.compile(r"^test_[A-Za-z0-9_]+\.py$")


@dataclass(frozen=True)
class PythonTestSuite:
    """One independently runnable owner or cross-owner Python test suite."""

    name: str
    authority: str
    start_dir: str
    tier: str
    purpose: str
    python_paths: tuple[str, ...] = ()
    repository_temp: bool = False
    exclusive: bool = False
    resource_locks: tuple[str, ...] = ()
    timeout_seconds: int = 900
    include_test_files: tuple[str, ...] = ()

    @property
    def path(self) -> Path:
        return ROOT / self.start_dir

    def test_files(self) -> tuple[Path, ...]:
        """Return the exact test-file partition owned by this suite."""

        configured = self.include_test_files
        if len(configured) != len(set(configured)):
            raise ValueError(f"suite {self.name} repeats a test-file name")
        invalid = tuple(
            name
            for name in configured
            if Path(name).name != name
            or TEST_FILE_NAME_PATTERN.fullmatch(name) is None
        )
        if invalid:
            raise ValueError(
                f"suite {self.name} has invalid test-file names: "
                + ", ".join(invalid)
            )
        available = {path.name: path for path in self.path.glob("test_*.py")}
        missing = tuple(name for name in configured if name not in available)
        if missing:
            raise ValueError(
                f"suite {self.name} names missing test files: "
                + ", ".join(missing)
            )
        if configured:
            return tuple(available[name] for name in configured)
        return tuple(path for _, path in sorted(available.items()))


VALIDATION_FAST_TEST_FILES = (
    "test_axiom_artifacts_build.py",
    "test_axiom_platform_build.py",
    "test_axiom_registry.py",
    "test_axiom_fluids.py",
    "test_axiom_construction.py",
    "test_axiom_prefixes.py",
    "test_axiom_forge_registries.py",
    "test_axiom_stacks.py",
    "test_axiom_native_identities.py",
    "test_axiom_native_root_stage.py",
    "test_axiom_native_early_stage.py",
    "test_axiom_native_early_inputs.py",
    "test_axiom_native_materials.py",
    "test_axiom_catalog.py",
    "test_axiom_items.py",
    "test_axiom_material_items.py",
    "test_axiom_material_blocks.py",
    "test_axiom_material_ores.py",
    "test_axiom_material_program.py",
    "test_axiom_pack_configuration.py",
    "test_axiom_pack_platform.py",
    "test_axiom_pack_coremod.py",
    "test_axiom_pack_effects.py",
    "test_axiom_pack_material_effects.py",
    "test_axiom_addon_inventory.py",
    "test_axiom_pack_mutations.py",
    "test_axiom_pack_meta_items.py",
    "test_axiom_pack_gcym.py",
    "test_axiom_pack_supercritical.py",
    "test_axiom_pack_gtfo_configuration.py",
    "test_axiom_material_authoring.py",
    "test_axiom_material_arguments.py",
    "test_axiom_material_cached_authoring.py",
    "test_axiom_material_content.py",
    "test_axiom_material_property_values.py",
    "test_axiom_compiler_linkage.py",
    "test_axiom_material_api.py",
    "test_axiom_recipe_maps.py",
    "test_axiom_groovy_language.py",
    "test_axiom_events.py",
    "test_axiom_source_conformance.py",
    "test_axiom_runtime.py",
    "test_axiom_target_build.py",
    "test_build_paths.py",
    "test_component_versions.py",
    "test_public_assets.py",
    "test_public_package.py",
    "test_public_repository.py",
    "test_publication_readiness.py",
    "test_install_workbench.py",
    "test_native_artifacts.py",
    "test_platform_matrix.py",
    "test_pixi_setup.py",
    "test_product_smoke.py",
    "test_release_client_build_lanes.py",
    "test_ci_validation.py",
    "test_stage_diagnostics.py",
    "test_native_reuse.py",
    "test_validate_ide.py",
    "test_run_python_suite.py",
    "test_suite_selection.py",
    "test_coverage_catalog.py",
    "test_test_suites.py",
    "test_validate.py",
    "test_validate_public_tree.py",
    "test_validation_orchestration.py",
    "test_validation_scheduler.py",
)


VALIDATION_AUTHORITY_TEST_FILES = (
    "test_product_open_authority.py",
)

VALIDATION_NATIVE_FIXTURE_TEST_FILES = (
    "test_axiom_native_execution.py",
)


# Order is intentional. Fast product, profile, and cross-owner checks run in
# the default developer tier. The product-open integration regression remains
# in the explicit canonical tier so ordinary feedback stays proportional.
PYTHON_TEST_SUITES: tuple[PythonTestSuite, ...] = (
    PythonTestSuite("core-api", "Workbench Core API", "api/tests", "quick", "Independent API contracts."),
    PythonTestSuite("core", "Workbench Core", "core/tests", "quick", "Core module admission and lifecycle without domain imports."),
    PythonTestSuite("axiom", "Axiom", "modules/axiom/tests", "quick", "Independent JVM invocation contract; Java conformance runs through tools/build_axiom.py.", ("modules/axiom/src",)),
    PythonTestSuite(
        "validation",
        "Workbench validation",
        "validation/tests",
        "quick",
        "Source/build, orchestration, schema, and validator regressions.",
        include_test_files=VALIDATION_FAST_TEST_FILES,
    ),
    PythonTestSuite(
        "developer-feature",
        "Workbench developer feature",
        "modules/workbench-shell/tests/developer_feature",
        "quick",
        "Current-checkout planning, transaction safety, and rollback behavior.",
        (
            "modules/workbench-shell/src",
            "modules/project-intelligence/src",
        ),
    ),
    PythonTestSuite(
        "project-intelligence",
        "Project Intelligence",
        "modules/project-intelligence/tests",
        "quick",
        "Workspace, artifact, build-provenance, and runtime-surface inspection.",
        ("modules/project-intelligence/src",),
    ),
    PythonTestSuite(
        "pack-program-studio",
        "Pack Program Studio",
        "modules/pack-program-studio/tests",
        "quick",
        "GroovyScript source, language-service, and managed-session contracts.",
        (
            "modules/pack-program-studio/src",
            "modules/project-intelligence/src",
        ),
    ),
    PythonTestSuite(
        "process-studio",
        "Process Studio composition",
        "modules/process-studio/tests",
        "quick",
        "Bounded observed-effect comparison and authority-preserving owner handoffs.",
        (
            "modules/process-studio/src",
            "modules/crucible/src",
        ),
    ),
    PythonTestSuite(
        "sentinel",
        "Sentinel",
        "modules/sentinel/tests",
        "quick",
        "Plain-English orchestration of profile-owned static diagnostics.",
        (
            "modules/sentinel/src",
            "modules/project-intelligence/src",
            "profiles/platforms/cleanroom/src",
        ),
    ),
    PythonTestSuite(
        "relay",
        "Relay",
        "modules/relay/tests",
        "quick",
        "Exact read-only navigation across retained owner evidence.",
        (
            "modules/relay/src",
            "modules/runtime-explorer/src",
            "modules/project-intelligence/src",
            "modules/atlas/src",
            "modules/crucible/src",
        ),
    ),
    PythonTestSuite(
        "service-conformance",
        "Workbench cross-owner conformance",
        "tests/conformance",
        "quick",
        "Public service behavior shared across owner implementations.",
        (
            "modules/workbench-shell/src",
            "modules/project-intelligence/src",
            "modules/atlas/src",
            "modules/blueprints/src",
            "modules/crucible/src",
            "modules/runtime-explorer/src",
        ),
    ),
    PythonTestSuite(
        "subsurface-studio",
        "Subsurface Studio",
        "modules/subsurface-studio/tests",
        "quick",
        "Bounded subsurface projections and owner handoffs.",
        (
            "modules/subsurface-studio/src",
            "modules/crucible/src",
            "modules/workbench-shell/src",
        ),
    ),
    PythonTestSuite(
        "worldgen-cockpit",
        "Worldgen Cockpit",
        "modules/worldgen-cockpit/tests",
        "quick",
        "World-generation cockpit projections across Atlas and Crucible.",
        (
            "modules/worldgen-cockpit/src",
            "modules/subsurface-studio/src",
            "modules/crucible/src",
            "modules/atlas/src",
            "modules/workbench-shell/src",
        ),
    ),
    PythonTestSuite(
        "worldgen-qualifier",
        "Worldgen Qualifier",
        "modules/worldgen-qualifier/tests",
        "quick",
        "Qualification policy, retained evidence, and failure semantics.",
        (
            "modules/worldgen-qualifier/src",
            "modules/worldgen-cockpit/src",
            "modules/subsurface-studio/src",
            "modules/crucible/src",
            "modules/atlas/src",
            "modules/workbench-shell/src",
        ),
    ),
    PythonTestSuite(
        "runtime-explorer",
        "Runtime Explorer",
        "modules/runtime-explorer/tests",
        "quick",
        "Runtime query providers and owner-bounded presentation.",
        (
            "modules/runtime-explorer/src",
            "modules/project-intelligence/src",
            "modules/workbench-shell/src",
            "modules/pack-program-studio/src",
            "modules/atlas/src",
            "modules/crucible/src",
        ),
    ),
    PythonTestSuite(
        "cleanroom-profile",
        "Cleanroom platform profile",
        "profiles/platforms/cleanroom/tests",
        "quick",
        "Active Cleanroom profile imports, diagnostics, and exact regressions.",
        (
            "profiles/platforms/cleanroom/src",
            "modules/project-intelligence/src",
            "modules/crucible/src",
        ),
        resource_locks=("cleanroom-physical-fixture",),
    ),
    PythonTestSuite(
        "supersymmetry-worldgen",
        "Supersymmetry worldgen profile",
        "profiles/packs/supersymmetry/worldgen/tests",
        "quick",
        "Pack-specific world-generation diagnostic policy.",
    ),
    PythonTestSuite(
        "supersymmetry-blueprints",
        "Supersymmetry Blueprints profile",
        "profiles/packs/supersymmetry/blueprints/tests",
        "quick",
        "Pack-owned construction conventions and exact source renderers.",
        (
            "modules/atlas/src",
            "modules/blueprints/src",
            "modules/project-intelligence/src",
            "modules/workbench-shell/src",
        ),
        repository_temp=True,
    ),
    PythonTestSuite(
        "supersymmetry-runtime",
        "Supersymmetry runtime profile",
        "profiles/packs/supersymmetry/runtime/tests",
        "quick",
        "Pack-owned material/fluid client and dedicated-server runtime custody.",
    ),
    PythonTestSuite(
        "cleanroom-worldgen-observatory-fixture",
        "Cleanroom observatory candidate fixture",
        (
            "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
            "worldgen-observatory-fixture/tests"
        ),
        "quick",
        "Exact source contract for the declared Cleanroom candidate fixture.",
    ),
    PythonTestSuite(
        "cleanroom-worldgen-prototype-fixture",
        "Cleanroom prototype candidate fixture",
        (
            "profiles/platforms/cleanroom/candidates/0.6.8-alpha/"
            "worldgen-prototype-fixture/tests"
        ),
        "quick",
        "Prototype source, log, and JFR summarization contracts.",
    ),
    PythonTestSuite(
        "manuals",
        "Manuals",
        "modules/manuals/tests",
        "quick",
        "Teaching-surface structure and non-authorizing language.",
    ),
    PythonTestSuite(
        "atlas",
        "Atlas",
        "modules/atlas/tests",
        "quick",
        "Generic observed and derived knowledge, queries, and provenance.",
        (
            "modules/atlas/src/workbench_atlas",
            "modules/atlas/src",
            "profiles/packs/supersymmetry/atlas/src",
            "modules/crucible/src",
            "modules/pack-program-studio/src",
            "modules/project-intelligence/src",
        ),
        repository_temp=True,
    ),
    PythonTestSuite(
        "supersymmetry-atlas",
        "Supersymmetry Atlas profile",
        "profiles/packs/supersymmetry/atlas/tests",
        "quick",
        "Pack-specific evidence, applicability, and graph projections.",
        (
            "modules/atlas/src/workbench_atlas",
            "modules/atlas/src",
            "profiles/packs/supersymmetry/atlas/src",
            "modules/crucible/src",
            "modules/pack-program-studio/src",
            "modules/project-intelligence/src",
        ),
        repository_temp=True,
    ),
    PythonTestSuite(
        "validation-authority",
        "Workbench validation authority",
        "validation/tests",
        "intensive",
        (
            "Product-open integration from a Cleanroom workspace to developer jobs."
        ),
        timeout_seconds=9000,
        include_test_files=VALIDATION_AUTHORITY_TEST_FILES,
    ),
    PythonTestSuite(
        "validation-native-fixtures",
        "Axiom original-input native conformance",
        "validation/tests",
        "intensive",
        "Explicit candidate, engine, JVM and fresh-report-root native execution.",
        timeout_seconds=3600,
        include_test_files=VALIDATION_NATIVE_FIXTURE_TEST_FILES,
    ),
    PythonTestSuite(
        "workbench-shell",
        "Workbench Shell",
        "modules/workbench-shell/tests",
        "intensive",
        "Orchestration, release, runtime, service, and Feature Studio behavior.",
        (
            "modules/workbench-shell/src",
            "modules/project-intelligence/src",
        ),
        resource_locks=("cleanroom-physical-fixture",),
        timeout_seconds=3600,
    ),
    PythonTestSuite(
        "blueprints",
        "Blueprints",
        "modules/blueprints/tests",
        "intensive",
        "Registered standards, deterministic rendering, simulations, and safe application.",
        python_paths=("modules/blueprints/src",),
        timeout_seconds=3600,
    ),
    PythonTestSuite(
        "crucible",
        "Crucible",
        "modules/crucible/tests",
        "intensive",
        "Controlled execution, custody, recovery, graph, and runtime behavior.",
        ("modules/crucible/src",),
        exclusive=True,
        timeout_seconds=9000,
    ),
)


SUITES_BY_NAME = {suite.name: suite for suite in PYTHON_TEST_SUITES}


def suites_for_tier(tier: str) -> tuple[PythonTestSuite, ...]:
    if tier == "quick":
        return tuple(suite for suite in PYTHON_TEST_SUITES if suite.tier == "quick")
    if tier == "source-ci":
        return tuple(suite for suite in PYTHON_TEST_SUITES if suite.name != "validation-native-fixtures")
    if tier == "canonical":
        return PYTHON_TEST_SUITES
    raise ValueError(f"unknown validation tier: {tier}")
