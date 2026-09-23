#!/usr/bin/env python3

"""Build deterministic VSIX and IntelliJ developer client packages."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence
import zipfile
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SHELL_SOURCE = ROOT / "modules/workbench-shell/src"
if str(SHELL_SOURCE) not in sys.path:
    sys.path.insert(0, str(SHELL_SOURCE))
TOOLS_SOURCE = ROOT / "tools"
if str(TOOLS_SOURCE) not in sys.path:
    sys.path.insert(0, str(TOOLS_SOURCE))

from release_track import artifact_filename as release_artifact_filename  # noqa: E402
from release_track import component as release_component  # noqa: E402
from release_track import load_release_descriptor  # noqa: E402

VSCODE_ROOT = ROOT / "clients/vscode"
INTELLIJ_ROOT = ROOT / "clients/intellij-community"
CANONICAL_CLIENTS = {
    "intellij-community": "clients/intellij-community",
    "vscode": "clients/vscode",
}
FORMAT = "workbench-developer-client-artifact-manifest-v1"
SCHEMA_VERSION = 1
PUBLIC_LANE = "public-v1"
MAX_ARCHIVE_ENTRIES = 4096
MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
EXPECTED_INTELLIJ_ACTION_CLASSES = {
    "Workbench.BrowseAtlasRecipes": "dev.cleanroommc.workbench.intellij.community.BrowseAtlasRecipesAction",
    "Workbench.NavigateSource": "dev.cleanroommc.workbench.intellij.community.NavigateSourceAction",
    "Workbench.ReviewLocalChanges": "dev.cleanroommc.workbench.intellij.community.ReviewLocalChangesAction",
    "Workbench.RunSavedChecks": "dev.cleanroommc.workbench.intellij.community.RunSavedChecksAction",
    "Workbench.ConfigureInstalledCore": (
        "dev.cleanroommc.workbench.intellij.community.ConfigureInstalledCoreAction"
    ),
    "Workbench.OpenInstallationHelp": (
        "dev.cleanroommc.workbench.intellij.community.OpenInstallationHelpAction"
    ),
    "Workbench.OpenSetup": (
        "dev.cleanroommc.workbench.intellij.community.OpenWorkbenchSetupAction"
    ),
    "Workbench.QualifyProject": (
        "dev.cleanroommc.workbench.intellij.community.OpenProjectQualificationAction"
    ),
    "Workbench.ReviewRecipes": (
        "dev.cleanroommc.workbench.intellij.community.OpenRecipeReviewAction"
    ),
    "Workbench.OpenDeveloperTools": (
        "dev.cleanroommc.workbench.intellij.community.OpenDeveloperToolsAction"
    ),
    "Workbench.OpenRecipeImpact": (
        "dev.cleanroommc.workbench.intellij.community.OpenRecipeImpactAction"
    ),
    "Workbench.OpenRetainedRecords": (
        "dev.cleanroommc.workbench.intellij.community.OpenRetainedRecordsAction"
    ),
    "Workbench.OpenWorkspaceHome": (
        "dev.cleanroommc.workbench.intellij.community.OpenWorkspaceHomeAction"
    ),
    "Workbench.ReopenFeatureStudioJob": (
        "dev.cleanroommc.workbench.intellij.community.ReopenFeatureStudioJobAction"
    ),
    "Workbench.RunMaterialFluidRecipe": (
        "dev.cleanroommc.workbench.intellij.community.RunMaterialFluidRecipeAction"
    ),
}
EXPECTED_INTELLIJ_ACTION_IDS = frozenset(EXPECTED_INTELLIJ_ACTION_CLASSES)
EXPECTED_INTELLIJ_ACTION_CLASS_FILES = frozenset(
    class_name.replace(".", "/") + ".class"
    for class_name in EXPECTED_INTELLIJ_ACTION_CLASSES.values()
)
EXPECTED_VSCODE_COMMAND_IDS = frozenset(
    {
        "workbench.source.navigate",
        "workbench.review.local",
        "workbench.checks.saved",
        "workbench.axiomResults.open",
        "workbench.axiomResults.captured",
        "workbench.axiomResults.evidence",
        "workbench.axiomResults.next",
        "workbench.axiomResults.snapshot",
        "workbench.axiomResults.actions",

        "workbench.atlas.searchRecipes",
        "workbench.atlas.analyzeRecipeImpact",
        "workbench.atlas.recipeImpact.openReport",
        "workbench.commandCenter.open",
        "workbench.core.checkInstallation",
        "workbench.core.configureExecutable",
        "workbench.core.openInstallationGuide",
        "workbench.feature.browseExamples",
        "workbench.feature.browseRetainedRecords",
        "workbench.feature.records.refresh",
        "workbench.feature.records.openRecord",
        "workbench.feature.records.openDiff",
        "workbench.feature.reopenJob",
        "workbench.feature.runMaterialFluidRecipe",
        "workbench.pr.openFindingDiff",
        "workbench.pr.openReport",
        "workbench.pr.openWorkspaceFile",
        "workbench.project.qualify",
        "workbench.review.pullRequest",
        "workbench.review.recipes",
        "workbench.setup.open",
        "workbench.workspaceHome.open",
    }
)


class ClientBuildError(RuntimeError):
    """A developer-client input, toolchain, build, or package is invalid."""


RELEASE_DESCRIPTOR = load_release_descriptor(ROOT)
if RELEASE_DESCRIPTOR["package"]["release_track"] != "public-v1":
    raise ClientBuildError("developer-client builder requires the public-v1 track")
VSCODE_COMPONENT = release_component(ROOT, "workbench-vscode")
INTELLIJ_COMPONENT = release_component(ROOT, "workbench-intellij-community")
VSCODE_VERSION = str(VSCODE_COMPONENT["version"])
INTELLIJ_VERSION = str(INTELLIJ_COMPONENT["version"])
VSCODE_ARTIFACT_NAME = release_artifact_filename(
    ROOT, "workbench-vscode.vsix"
)
INTELLIJ_ARTIFACT_NAME = release_artifact_filename(
    ROOT, "workbench-intellij-community.plugin-zip"
)
VSCODE_STAGE_FILES = (
    "package.json",
    ".vscodeignore",
    "README.md",
    "extension.js",
    "coreLaunch.js",
    "coreClient.js",
    "coreStatusClient.js",
    "recipeReviewClient.js",
    "prRecipeReviewClient.js",
    "prRecipeReviewTree.js",
    "projectQualificationClient.js",
    "coreCommandClient.js",
    "currentContextFeatureClient.js",
    "commandCenterClient.js",
    "developerToolsClient.js",
    "workspaceHomeClient.js",
    "workspaceHomeV2Client.js",
    "workspaceHomeV2Tree.js",
    "workSessionClient.js",
    "diagnoseClient.js",
    "featureServiceClient.js",
    "developerFeatureClient.js",
    "featureRecordClient.js",
    "featureRecordTree.js",
    "atlasCompleteRecipeImpactClient.js",
    "atlasRecipeImpactClient.js",
    "atlasRecipeImpactFlow.js",
    "atlasRecipeBrowser.js",
    "atlasRecipeBrowseValidation.js",
    "atlasRecipeSession.js",
    "sourceNavigationClient.js",
    "localReviewClient.js",
    "localReview.js",
    "developerContext.js",
    "developerChecksClient.js",
    "developerChecks.js",
    "materialSnapshotClient.js",
    "materialDeliveryClient.js",
    "materialResults.js",
    "materialChecksClient.js",
    "materialChecks.js",
    "atlasRecipeImpactTree.js",
    "CHANGELOG.md",
    "SUPPORT.md",
    "media/workbench-icon.png",
    "media/axiom-results.svg",
)


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def manifest_id(value: Mapping[str, Any]) -> str:
    projection = deepcopy(dict(value))
    projection.pop("client_artifact_manifest_id", None)
    return "workbench-developer-clients:sha256:" + hashlib.sha256(
        canonical_bytes(projection)
    ).hexdigest()


def _safe_name(name: str) -> str:
    if not name or "\\" in name or "\0" in name:
        raise ClientBuildError(f"unsafe ZIP member name: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ClientBuildError(f"unsafe ZIP member path: {name!r}")
    return path.as_posix()


def normalize_zip(source: Path | str, destination: Path | str) -> None:
    """Rewrite one ZIP with stable order, modes, timestamps, and compression."""

    source_path = Path(source)
    destination_path = Path(destination)
    if source_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ClientBuildError(f"archive exceeds {MAX_ARCHIVE_BYTES} bytes: {source_path}")
    rows: list[tuple[str, bytes, bool]] = []
    observed: set[str] = set()
    folded: set[str] = set()
    with zipfile.ZipFile(source_path, "r") as archive:
        if len(archive.infolist()) > MAX_ARCHIVE_ENTRIES:
            raise ClientBuildError("archive has too many entries")
        for info in archive.infolist():
            name = _safe_name(info.filename)
            if name in observed or name.casefold() in folded:
                raise ClientBuildError(f"duplicate or case-colliding ZIP member: {name}")
            observed.add(name)
            folded.add(name.casefold())
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ClientBuildError(f"symbolic-link ZIP member is forbidden: {name}")
            is_directory = info.is_dir()
            if is_directory:
                data = b""
            else:
                if not 0 <= info.file_size <= MAX_ENTRY_BYTES:
                    raise ClientBuildError(f"ZIP member exceeds its bound: {name}")
                data = archive.read(info)
                if len(data) != info.file_size:
                    raise ClientBuildError(f"ZIP member changed size while read: {name}")
            rows.append((name, data, is_directory))
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_path.with_name(destination_path.name + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for name, data, is_directory in sorted(
                rows, key=lambda row: row[0].encode("utf-8")
            ):
                normalized = name.rstrip("/") + "/" if is_directory else name
                info = zipfile.ZipInfo(normalized, (1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = ((0o40755 if is_directory else 0o100644) << 16)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        os.replace(temporary, destination_path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(command: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise ClientBuildError(
            f"command failed with exit {completed.returncode}: {' '.join(command)}"
        )


def verify_client_source_boundaries() -> None:
    """Fail unless the public client source roots are canonical and complete."""

    missing = [
        path for path in CANONICAL_CLIENTS.values() if not (ROOT / path).is_dir()
    ]
    if missing:
        raise ClientBuildError(
            "canonical developer-client sources are unavailable: " + ", ".join(missing)
        )


def _tool_versions(
    node: Path,
    npm_cli: Path,
    environment: Mapping[str, str],
) -> dict[str, str]:
    lock = json.loads((ROOT / "validation/ide-toolchains-v1.json").read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    commands = (("node", "node", [str(node)]), ("npm", "npm", [str(node), str(npm_cli)]))
    for executable, key, command in commands:
        actual = subprocess.check_output(
            [*command, "--version"], text=True, encoding="utf-8", env=environment
        ).strip().removeprefix("v")
        expected = str(lock[key]["version"])
        if actual != expected:
            raise ClientBuildError(
                f"{executable} {actual!r} does not match locked {expected!r}"
            )
        versions[executable] = actual
    return versions


def build_vscode(
    raw_output: Path,
    *,
    skip_extension_host: bool,
) -> dict[str, str]:
    sys.path.insert(0, str(ROOT / "validation"))
    try:
        from provision_ide_toolchains import provision_node, provision_npm
    except ImportError as error:
        raise ClientBuildError("could not load the Node toolchain provisioner") from error
    node_home = provision_node()
    npm_home = provision_npm()
    node = node_home / "bin/node"
    npm_cli = npm_home / "bin/npm-cli.js"
    if not node.is_file() or not npm_cli.is_file():
        raise ClientBuildError("provisioned Node/npm entry points are missing")
    environment = os.environ.copy()
    environment["PATH"] = str(node_home / "bin") + os.pathsep + environment.get("PATH", "")
    versions = _tool_versions(node, npm_cli, environment)
    _run(
        [str(node), str(npm_cli), "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        cwd=VSCODE_ROOT,
        env=environment,
    )
    _run([str(node), str(npm_cli), "test"], cwd=VSCODE_ROOT, env=environment)
    _run([str(node), str(npm_cli), "run", "test:package"], cwd=VSCODE_ROOT, env=environment)
    vsce = VSCODE_ROOT / "node_modules/.bin/vsce"
    if not vsce.is_file():
        raise ClientBuildError("locked VSCE executable was not installed")
    with tempfile.TemporaryDirectory(
        prefix="workbench-vsix-stage-", dir=raw_output.parent
    ) as directory:
        stage = Path(directory)
        for name in VSCODE_STAGE_FILES:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(VSCODE_ROOT / name, destination)
        shutil.copyfile(ROOT / "LICENSE", stage / "LICENSE")
        shutil.copyfile(ROOT / "NOTICE.md", stage / "NOTICE.md")
        _run(
            [
                str(node),
                str(vsce),
                "package",
                "--no-dependencies",
                "--allow-missing-repository",
                "--out",
                str(raw_output),
            ],
            cwd=stage,
            env=environment,
        )
    normalized = raw_output.with_name(raw_output.name + ".normalized")
    normalize_zip(raw_output, normalized)
    os.replace(normalized, raw_output)
    if not skip_extension_host:
        _run(
            [
                str(node),
                str(npm_cli),
                "run",
                "test:integration",
                "--",
                str(raw_output.resolve(strict=True)),
            ],
            cwd=VSCODE_ROOT,
            env=environment,
        )
    return {"node": versions["node"], "npm": versions["npm"], "vsce": "3.9.2"}


def _provision_intellij() -> tuple[Path, Path, Path]:
    sys.path.insert(0, str(ROOT / "validation"))
    try:
        from provision_ide_toolchains import provision
    except ImportError as error:
        raise ClientBuildError("could not load the IDE toolchain provisioner") from error
    return provision()


def build_intellij(
    raw_output: Path,
) -> dict[str, str]:
    java_home, java_platform_home, gradle_home = _provision_intellij()
    lock = json.loads((ROOT / "validation/ide-toolchains-v1.json").read_text(encoding="utf-8"))
    environment = os.environ.copy()
    environment.update(
        {
            "JAVA_HOME": str(java_home),
            "GRADLE_USER_HOME": str(ROOT / ".workbench/gradle-home/intellij-community-stage1"),
            "PATH": str(java_home / "bin") + os.pathsep + environment.get("PATH", ""),
            "SOURCE_DATE_EPOCH": "0",
            "TZ": "UTC",
        }
    )
    executable = gradle_home / "bin/gradle"
    command = [
        str(executable),
        "--no-daemon",
        "--console=plain",
        f"-Dorg.gradle.java.installations.paths={java_platform_home}",
        "-Dorg.gradle.java.installations.auto-detect=false",
    ]
    command.extend(
        (
            "clean",
            "unitTest",
            "buildPlugin",
            "verifyPluginStructure",
            "verifyPlugin",
        )
    )
    _run(
        command,
        cwd=INTELLIJ_ROOT,
        env=environment,
    )
    built = INTELLIJ_ROOT / "build/distributions" / INTELLIJ_ARTIFACT_NAME
    if not built.is_file():
        raise ClientBuildError("Gradle did not create the expected IntelliJ package")
    shutil.copyfile(built, raw_output)
    return {
        "gradle": str(lock["gradle"]["version"]),
        "java": str(lock["java"]["version"]),
        "platform_java": str(lock["java_platform"]["version"]),
        "intellij_platform_gradle_plugin": "2.18.1",
        "intellij": "2026.2.0.1",
    }


def _members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as archive:
        return {
            _safe_name(info.filename): archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
        }


def verify_vscode(path: Path) -> dict[str, Any]:
    members = _members(path)
    forbidden_members = {
        "extension/featureApplicationClient.js",
        "extension/foundationAssistClient.js",
        "extension/resources/workbench-component-compatibility-v1.json",
        "extension/workbench-stage6-accessibility-v1.json",
        "extension/workbench-stage6-accessibility-v2.json",
        "extension/workspaceHomeTree.js",
    }
    retained = sorted(forbidden_members & members.keys())
    if retained:
        raise ClientBuildError(
            "VSIX retains removed release application members: "
            + ", ".join(retained)
        )
    required = {
        "extension/package.json",
        "extension/extension.js",
        "extension/coreLaunch.js",
        "extension/coreClient.js",
        "extension/coreStatusClient.js",
        "extension/recipeReviewClient.js",
        "extension/prRecipeReviewClient.js",
        "extension/prRecipeReviewTree.js",
        "extension/projectQualificationClient.js",
        "extension/coreCommandClient.js",
        "extension/currentContextFeatureClient.js",
        "extension/commandCenterClient.js",
        "extension/developerToolsClient.js",
        "extension/workspaceHomeClient.js",
        "extension/workspaceHomeV2Client.js",
        "extension/workspaceHomeV2Tree.js",
        "extension/workSessionClient.js",
        "extension/diagnoseClient.js",
        "extension/featureServiceClient.js",
        "extension/developerFeatureClient.js",
        "extension/featureRecordClient.js",
        "extension/featureRecordTree.js",
        "extension/atlasCompleteRecipeImpactClient.js",
        "extension/atlasRecipeImpactClient.js",
        "extension/atlasRecipeImpactFlow.js",
        "extension/atlasRecipeBrowser.js",
        "extension/atlasRecipeBrowseValidation.js",
        "extension/atlasRecipeSession.js",
        "extension/sourceNavigationClient.js",
        "extension/localReviewClient.js",
        "extension/localReview.js",
        "extension/developerContext.js",
        "extension/developerChecksClient.js",
        "extension/developerChecks.js",
        "extension/atlasRecipeImpactTree.js",
        "extension/changelog.md",
        "extension/SUPPORT.md",
        "extension/media/workbench-icon.png",
        "extension/LICENSE.txt",
        "extension/NOTICE.md",
    }
    missing = sorted(required - members.keys())
    if missing:
        raise ClientBuildError("VSIX is missing: " + ", ".join(missing))
    icon = members["extension/media/workbench-icon.png"]
    if (
        len(icon) < 24
        or icon[:8] != b"\x89PNG\r\n\x1a\n"
        or icon[12:16] != b"IHDR"
        or int.from_bytes(icon[16:20], "big") != 256
        or int.from_bytes(icon[20:24], "big") != 256
    ):
        raise ClientBuildError("VSIX Workbench icon is not the qualified 256px PNG")
    descriptor = json.loads(members["extension/package.json"].decode("utf-8"))
    if (
        descriptor.get("name") != "workbench-vscode"
        or descriptor.get("displayName") != "Workbench for Minecraft Development"
        or descriptor.get("publisher") != "cleanroom-workbench"
        or descriptor.get("version") != VSCODE_VERSION
        or descriptor.get("main") != "./extension.js"
        or descriptor.get("icon") != "media/workbench-icon.png"
        or descriptor.get("preview") is not True
        or descriptor.get("description")
        != "Review Minecraft modpack recipe changes and run local development checks from VS Code with the Workbench CLI."
        or descriptor.get("extensionKind") != ["workspace"]
        or descriptor.get("capabilities", {})
        .get("virtualWorkspaces", {})
        .get("supported")
        is not False
        or descriptor.get("capabilities", {})
        .get("untrustedWorkspaces", {})
        .get("supported")
        is not False
        or descriptor.get("repository", {}).get("url")
        != "https://github.com/orthrus-research/workbench.git"
        or descriptor.get("homepage") != "https://github.com/orthrus-research/workbench#readme"
        or descriptor.get("bugs", {}).get("url")
        != "https://github.com/orthrus-research/workbench/issues"
    ):
        raise ClientBuildError("VSIX descriptor differs from the developer-client boundary")
    contributes = descriptor.get("contributes")
    commands = contributes.get("commands") if type(contributes) is dict else None
    if type(commands) is not list or any(
        type(row) is not dict
        or any(
            marker in str(row.get("command", ""))
            for marker in (
                "checkCore",
                "applicationStatus",
                "applyDisposableFeature",
                "rollbackDisposableFeature",
                "applyDirectFeature",
                "rollbackDirectFeature",
            )
        )
        for row in commands
    ):
        raise ClientBuildError("VSIX descriptor retains removed application commands")
    if any(
        marker in str(row.get("command", "")).lower()
        for row in commands
        for marker in ("diagnos", "foundationassist", "foundation.assist")
    ):
        raise ClientBuildError(
            "VSIX exposes an unqualified internal adapter as a user command"
        )
    if any(str(row.get("command", "")).startswith("workbench.release.") for row in commands):
        raise ClientBuildError("VSIX descriptor retains the release-process command namespace")
    if {
        str(row.get("command", "")) for row in commands
    } != EXPECTED_VSCODE_COMMAND_IDS:
        raise ClientBuildError("VSIX descriptor does not expose the exact developer command set")
    source = (
        members["extension/extension.js"]
        + members["extension/coreLaunch.js"]
        + members["extension/coreClient.js"]
        + members["extension/coreStatusClient.js"]
        + members["extension/recipeReviewClient.js"]
        + members["extension/prRecipeReviewClient.js"]
        + members["extension/prRecipeReviewTree.js"]
        + members["extension/projectQualificationClient.js"]
        + members["extension/coreCommandClient.js"]
        + members["extension/currentContextFeatureClient.js"]
        + members["extension/commandCenterClient.js"]
        + members["extension/developerToolsClient.js"]
        + members["extension/workspaceHomeClient.js"]
        + members["extension/workspaceHomeV2Client.js"]
        + members["extension/workspaceHomeV2Tree.js"]
        + members["extension/workSessionClient.js"]
        + members["extension/diagnoseClient.js"]
        + members["extension/featureServiceClient.js"]
        + members["extension/developerFeatureClient.js"]
        + members["extension/featureRecordClient.js"]
        + members["extension/featureRecordTree.js"]
        + members["extension/atlasCompleteRecipeImpactClient.js"]
        + members["extension/atlasRecipeImpactClient.js"]
        + members["extension/atlasRecipeImpactFlow.js"]
        + members["extension/atlasRecipeImpactTree.js"]
    )
    if (
        b"installedWindowsWslMappingV1" not in members["extension/coreLaunch.js"]
    ):
        raise ClientBuildError("VSIX omits the Windows/WSL parity mapping adapter")
    diagnosis_source = members["extension/diagnoseClient.js"]
    if (
        b"workbench-diagnosis-v1" not in diagnosis_source
        or b"workbench-reproduction-capsule-inspection-v1" not in diagnosis_source
        or b"pathForCoreLaunch" not in diagnosis_source
        or b"invokeCoreJson" not in diagnosis_source
        or b'require("./diagnoseClient")' not in members["extension/extension.js"]
    ):
        raise ClientBuildError("VSIX omits the read-only diagnosis/capsule adapter")
    if source.count(b"execFile(") < 3 or source.count(b"shell: false") < 3 or b"shell: true" in source:
        raise ClientBuildError("VSIX does not retain the no-shell core boundary")
    if b"core-compatibility" in source or b"invokeCore(" in source:
        raise ClientBuildError("VSIX retains the obsolete core compatibility probe")
    pr_review_client = members["extension/prRecipeReviewClient.js"]
    pr_review_tree = members["extension/prRecipeReviewTree.js"]
    if (
        b'"review", "pr"' not in pr_review_client
        or b'"--plan"' not in pr_review_client
        or b'"--apply"' not in pr_review_client
        or b"workbench-pr-preparation-plan-v2" not in pr_review_client
        or b"workbench-recipe-review-v2" not in pr_review_client
        or b"pathForCoreLaunch" not in pr_review_client
        or b"attention_scope" not in pr_review_client
        or b"git_hygiene" not in pr_review_client
        or b"--yes" in pr_review_client
        or b'"vscode.diff"' not in pr_review_tree
        or b'"vscode.open"' not in pr_review_tree
        or b"PR-introduced and supplied-runtime attention" not in pr_review_tree
        or b"Pre-existing and candidate-wide attention" not in pr_review_tree
        or b'location: { viewId: "workbench.prRecipeReview" }'
        not in members["extension/extension.js"]
        or b"createWebviewPanel" in pr_review_tree
    ):
        raise ClientBuildError("VSIX omits the native two-phase PR Recipe Review boundary")
    if b"release-feature-application" in source or b"FeatureApplication" in source:
        raise ClientBuildError("VSIX retains the removed release application surface")
    if b"workbench.release." in source:
        raise ClientBuildError("VSIX retains the release-process command namespace")
    if b"workbench.workspaceHome.runJob" in source:
        raise ClientBuildError("VSIX bypasses core custody with a cached Home argv executor")
    if b"feature\", \"run\", \"material-fluid-recipe" not in source:
        raise ClientBuildError("VSIX omits the developer feature run boundary")
    for marker in (
        b"workbench-workspace-home-v2",
        b"workbench-work-session-summary-v1",
        b"WorkspaceHomeV2TreeProvider",
        b"Recovery Required",
        b"Recommended Jobs",
        b"timeline-event",
        b"workbench.workspaceHome.inspectOwner",
        b"Home argv is a preview",
    ):
        if marker not in source:
            raise ClientBuildError(
                "VSIX omits an active Workspace Home V2 / Work Session consumer: "
                + marker.decode("utf-8")
            )
    command_center_markers = (
        b'console\", \"catalog\", \"--json',
        b"--expect-catalog-digest",
        b"--expect-action-digest",
        b"--expect-review-digest",
        b'feature\", \"examples',
        b'atlas\", \"recipes\", \"search',
    )
    if any(marker not in source for marker in command_center_markers):
        raise ClientBuildError("VSIX omits a reviewed developer-tools command boundary")
    views = contributes.get("views") if type(contributes) is dict else None
    explorer_views = views.get("explorer") if type(views) is dict else None
    expected_record_view = {
        "id": "workbench.featureRecords",
        "name": "Workbench Retained Records",
        "when": "isWorkspaceTrusted",
        "visibility": "collapsed",
    }
    if type(explorer_views) is not list or expected_record_view not in explorer_views:
        raise ClientBuildError("VSIX omits the trusted retained-record tree")
    record_markers = (
        b'feature", "records',
        b'feature", "present',
        b"provideTextDocumentContent",
        b"vscode.diff",
    )
    if any(marker not in source for marker in record_markers):
        raise ClientBuildError("VSIX omits the native retained-record diff boundary")
    expected_impact_view = {
        "id": "workbench.recipeImpact",
        "name": "Atlas Recipe Impact Candidates",
        "when": "isWorkspaceTrusted",
        "visibility": "collapsed",
    }
    if expected_impact_view not in explorer_views:
        raise ClientBuildError("VSIX omits the trusted Atlas recipe-impact tree")
    impact_markers = (
        b'atlas", "recipes", "impact',
        b"--max-depth",
        b"--max-nodes",
        b"workbench-atlas-recipe-impact-report-v1",
        b"workbench-atlas-recipe-impact-report-v2",
        b'"--exploration", "complete-finite"',
        b"Observed finite exploration complete",
        b"workbench-atlas-impact",
        b"Propagation candidates",
    )
    if any(marker not in source for marker in impact_markers):
        raise ClientBuildError("VSIX omits the native Atlas recipe-impact boundary")
    return {
        "client_id": "vscode",
        "package_artifact_id": "vscode-vsix",
        "version": VSCODE_VERSION,
        "host_boundary": "vscode >=1.125.0 <2.0.0",
    }


def _verify_intellij_action_contract(descriptor_root: ElementTree.Element) -> list[str]:
    action_rows = descriptor_root.findall(".//action")
    action_bindings = [(row.get("id"), row.get("class")) for row in action_rows]
    if (
        len(action_bindings) != len(EXPECTED_INTELLIJ_ACTION_CLASSES)
        or dict(action_bindings) != EXPECTED_INTELLIJ_ACTION_CLASSES
    ):
        raise ClientBuildError(
            "IntelliJ plugin does not expose the exact developer action-to-class mapping"
        )
    return [str(action_id) for action_id, _ in action_bindings]


def verify_intellij(path: Path) -> dict[str, Any]:
    members = _members(path)
    jars = sorted(name for name in members if name.endswith(".jar"))
    if len(jars) != 1:
        raise ClientBuildError("IntelliJ package must contain exactly one plugin JAR")
    with zipfile.ZipFile(io.BytesIO(members[jars[0]]), "r") as archive:
        jar_members = {
            _safe_name(info.filename): archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
        }
    forbidden_markers = (
        "FeatureApplication",
        "ApplyDisposableFeature",
        "RollbackDisposableFeature",
        "ApplyDirectFeature",
        "RollbackDirectFeature",
        "Stage5ParityProbe",
        "Stage6ParityProbe",
        "CheckInstalledCore",
        "CoreCompatibility",
        "WorkspaceHomeClient",
        "workbench-component-compatibility",
        "workbench-stage6-accessibility",
    )
    retained = sorted(
        name
        for name in jar_members
        if any(marker in name for marker in forbidden_markers)
    )
    if retained:
        raise ClientBuildError(
            "IntelliJ plugin retains removed release application members: "
            + ", ".join(retained)
        )
    required = {
        "META-INF/plugin.xml",
        "META-INF/LICENSE",
        "META-INF/NOTICE.md",
        "META-INF/pluginIcon.svg",
        "dev/cleanroommc/workbench/intellij/community/CoreLaunch.class",
        "dev/cleanroommc/workbench/intellij/community/CoreLaunch$InstalledWindowsWslMappingV1.class",
        "dev/cleanroommc/workbench/intellij/community/DeveloperFeatureClient.class",
        "dev/cleanroommc/workbench/intellij/community/FeatureServiceClient.class",
        "dev/cleanroommc/workbench/intellij/community/CommunityFeatureServiceProbe.class",
        "dev/cleanroommc/workbench/intellij/community/CoreLocation.class",
        "dev/cleanroommc/workbench/intellij/community/ConfigureInstalledCoreAction.class",
        "dev/cleanroommc/workbench/intellij/community/CoreStatus.class",
        "dev/cleanroommc/workbench/intellij/community/OpenWorkbenchSetupAction.class",
        "dev/cleanroommc/workbench/intellij/community/OpenInstallationHelpAction.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReview.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewDiffOpener.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewJson.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewNavigation.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewPanel.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewPlan.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewRequest.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewToolWindowFactory.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeReviewTree.class",
        "dev/cleanroommc/workbench/intellij/community/OpenRecipeReviewAction.class",
        "dev/cleanroommc/workbench/intellij/community/WorkbenchProjectTrust.class",
        "dev/cleanroommc/workbench/intellij/community/OpenDeveloperToolsAction.class",
        "dev/cleanroommc/workbench/intellij/community/CommandCatalog.class",
        "dev/cleanroommc/workbench/intellij/community/CommandFlow.class",
        "dev/cleanroommc/workbench/intellij/community/CommandProcess.class",
        "dev/cleanroommc/workbench/intellij/community/CommandOptionsDialog.class",
        "dev/cleanroommc/workbench/intellij/community/CommandReviewDialog.class",
        "dev/cleanroommc/workbench/intellij/community/CommandOutputDialog.class",
        "dev/cleanroommc/workbench/intellij/community/CatalogSelectionDialog.class",
        "dev/cleanroommc/workbench/intellij/community/CanonicalJson.class",
        "dev/cleanroommc/workbench/intellij/community/ProductSpineJson.class",
        "dev/cleanroommc/workbench/intellij/community/ProductSpineDetailDialog.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHome.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHomeV2.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHomeV2Client.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHomeV2Regions.class",
        "dev/cleanroommc/workbench/intellij/community/WorkSessionV2.class",
        "dev/cleanroommc/workbench/intellij/community/WorkSessionV2Client.class",
        "dev/cleanroommc/workbench/intellij/community/DiagnoseV1.class",
        "dev/cleanroommc/workbench/intellij/community/DiagnoseV1Client.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHomePanel.class",
        "dev/cleanroommc/workbench/intellij/community/WorkspaceHomeToolWindowFactory.class",
        "dev/cleanroommc/workbench/intellij/community/OpenWorkspaceHomeAction.class",
        "dev/cleanroommc/workbench/intellij/community/StrictJson.class",
        "dev/cleanroommc/workbench/intellij/community/FeatureRecordKinds.class",
        "dev/cleanroommc/workbench/intellij/community/FeatureRecordCatalog.class",
        "dev/cleanroommc/workbench/intellij/community/FeaturePresentation.class",
        "dev/cleanroommc/workbench/intellij/community/RetainedFeatureClient.class",
        "dev/cleanroommc/workbench/intellij/community/FeaturePresentationTree.class",
        "dev/cleanroommc/workbench/intellij/community/FeatureDiffOpener.class",
        "dev/cleanroommc/workbench/intellij/community/FeaturePresentationJsonDialog.class",
        "dev/cleanroommc/workbench/intellij/community/RetainedRecordsPanel.class",
        "dev/cleanroommc/workbench/intellij/community/WorkbenchRecordsToolWindowFactory.class",
        "dev/cleanroommc/workbench/intellij/community/OpenRetainedRecordsAction.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeContract.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeContext.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeSearch.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeImpact.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeClient.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeImpactTree.class",
        "dev/cleanroommc/workbench/intellij/community/AtlasRecipeImpactJsonDialog.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeImpactPanel.class",
        "dev/cleanroommc/workbench/intellij/community/RecipeImpactToolWindowFactory.class",
        "dev/cleanroommc/workbench/intellij/community/OpenRecipeImpactAction.class",
        "dev/cleanroommc/workbench/intellij/community/RunMaterialFluidRecipeAction.class",
        "dev/cleanroommc/workbench/intellij/community/ReopenFeatureStudioJobAction.class",
    }
    required.update(EXPECTED_INTELLIJ_ACTION_CLASS_FILES)
    missing = sorted(required - jar_members.keys())
    if missing:
        raise ClientBuildError("IntelliJ plugin is missing: " + ", ".join(missing))
    try:
        icon_root = ElementTree.fromstring(jar_members["META-INF/pluginIcon.svg"])
    except ElementTree.ParseError as error:
        raise ClientBuildError("IntelliJ Workbench icon is invalid SVG") from error
    if (
        icon_root.tag != "{http://www.w3.org/2000/svg}svg"
        or icon_root.get("width") != "40"
        or icon_root.get("height") != "40"
        or icon_root.get("viewBox") != "0 0 40 40"
        or any(row.tag.endswith("}image") or row.tag == "image" for row in icon_root.iter())
        or len(jar_members["META-INF/pluginIcon.svg"]) > 4 * 1024
    ):
        raise ClientBuildError("IntelliJ Workbench icon is not the qualified compact 40px vector")
    if (
        b"workbench-diagnosis-v1" not in jar_members[
            "dev/cleanroommc/workbench/intellij/community/DiagnoseV1.class"
        ]
        or b"workbench-reproduction-capsule-inspection-v1" not in jar_members[
            "dev/cleanroommc/workbench/intellij/community/DiagnoseV1.class"
        ]
        or b"Workbench diagnosis state root" not in jar_members[
            "dev/cleanroommc/workbench/intellij/community/DiagnoseV1Client.class"
        ]
    ):
        raise ClientBuildError("IntelliJ plugin omits the read-only diagnosis/capsule adapter")
    release_namespace_members = sorted(
        name for name, raw in jar_members.items() if b"workbench.release." in raw
    )
    if release_namespace_members:
        raise ClientBuildError(
            "IntelliJ plugin retains the release-process setting namespace: "
            + ", ".join(release_namespace_members)
        )
    descriptor = jar_members["META-INF/plugin.xml"].decode("utf-8")
    forbidden = (
        "com.intellij.modules.lsp",
        "org.intellij.groovy",
        "intellijIdeaUltimate",
        "platform.lsp",
    )
    if any(value in descriptor for value in forbidden):
        raise ClientBuildError("IntelliJ descriptor has a commercial-only dependency")
    if "FeatureApplication" in descriptor or "release-feature-application" in descriptor:
        raise ClientBuildError("IntelliJ plugin retains the removed release application surface")
    if "Workbench.CheckInstalledCore" in descriptor or "CheckInstalledCoreAction" in descriptor:
        raise ClientBuildError("IntelliJ plugin retains the obsolete core compatibility action")
    try:
        descriptor_root = ElementTree.fromstring(descriptor)
    except ElementTree.ParseError as error:
        raise ClientBuildError("IntelliJ plugin descriptor is invalid XML") from error
    action_ids = _verify_intellij_action_contract(descriptor_root)
    if any(
        marker in str(action_id).lower()
        for action_id in action_ids
        for marker in ("diagnos", "foundationassist", "foundation.assist")
    ):
        raise ClientBuildError(
            "IntelliJ plugin exposes an unqualified internal adapter as a user action"
        )
    plugin_name = descriptor_root.findtext("name")
    description = descriptor_root.findtext("description") or ""
    vendor = descriptor_root.find("vendor")
    if (
        plugin_name != "Workbench for Minecraft"
        or len(plugin_name) > 30
        or "Review Minecraft modpack changes" not in description
        or "Requires the Workbench CLI" not in description
        or "setup wizard" not in description
        or vendor is None
        or vendor.get("url") != "https://github.com/orthrus-research/workbench"
        or (vendor.text or "").strip() != "Orthrus Research"
        or descriptor_root.find("change-notes") is None
    ):
        raise ClientBuildError("IntelliJ plugin listing metadata is incomplete")
    if descriptor.count("<depends>") != 1 or "<depends>com.intellij.modules.platform</depends>" not in descriptor:
        raise ClientBuildError("IntelliJ descriptor is not platform-only")
    record_windows = [
        row
        for row in descriptor_root.findall(".//toolWindow")
        if row.get("id") == "Workbench Records"
    ]
    if len(record_windows) != 1 or record_windows[0].get("factoryClass") != (
        "dev.cleanroommc.workbench.intellij.community."
        "WorkbenchRecordsToolWindowFactory"
    ):
        raise ClientBuildError("IntelliJ plugin omits the retained-record tool window")
    impact_windows = [
        row
        for row in descriptor_root.findall(".//toolWindow")
        if row.get("id") == "Recipe Impact"
    ]
    if len(impact_windows) != 1 or impact_windows[0].get("factoryClass") != (
        "dev.cleanroommc.workbench.intellij.community."
        "RecipeImpactToolWindowFactory"
    ):
        raise ClientBuildError("IntelliJ plugin omits the Atlas recipe-impact tool window")
    review_windows = [
        row
        for row in descriptor_root.findall(".//toolWindow")
        if row.get("id") == "Recipe Review"
    ]
    if len(review_windows) != 1 or review_windows[0].get("factoryClass") != (
        "dev.cleanroommc.workbench.intellij.community."
        "RecipeReviewToolWindowFactory"
    ):
        raise ClientBuildError("IntelliJ plugin omits the native PR Recipe Review tool window")
    client_source = b"".join(
        jar_members[name]
        for name in sorted(jar_members)
        if name.startswith("dev/cleanroommc/workbench/intellij/community/")
        and name.endswith(".class")
    )
    record_markers = (b"feature", b"records", b"present", b"DiffManager")
    if any(marker not in client_source for marker in record_markers):
        raise ClientBuildError("IntelliJ plugin omits the native retained-record diff boundary")
    impact_markers = (
        b"atlas",
        b"recipes",
        b"impact",
        b"--max-depth",
        b"--max-nodes",
        b"workbench-atlas-recipe-impact-report-v1",
        b"Propagation candidates",
        b"Task$Backgroundable",
        b"Recipe Impact",
        b"frontiers",
        b"unknowns",
        b"evidence_gaps",
        b"Verifying graph and searching Atlas recipes",
        b"Verifying graph and analyzing recipe impact",
    )
    if any(marker not in client_source for marker in impact_markers):
        raise ClientBuildError("IntelliJ plugin omits the native Atlas recipe-impact boundary")
    pr_review_source = b"".join(
        raw
        for name, raw in sorted(jar_members.items())
        if name.startswith(
            "dev/cleanroommc/workbench/intellij/community/RecipeReview"
        )
        and name.endswith(".class")
    )
    pr_review_markers = (
        b"workbench-pr-preparation-plan-v2",
        b"workbench-recipe-review-v2",
        b"--plan",
        b"--apply",
        b"attention_scope",
        b"git_hygiene",
        b"Task$Backgroundable",
        b"DiffManager",
        b"PR-introduced decision scope",
        b"Preexisting and candidate-wide context",
    )
    if (
        any(marker not in pr_review_source for marker in pr_review_markers)
        or b"--yes" in jar_members[
            "dev/cleanroommc/workbench/intellij/community/RecipeReviewRequest.class"
        ]
    ):
        raise ClientBuildError(
            "IntelliJ plugin omits the native two-phase PR Recipe Review boundary"
        )
    command_catalog_source = jar_members[
        "dev/cleanroommc/workbench/intellij/community/CommandCatalog.class"
    ]
    command_center_source = jar_members[
        "dev/cleanroommc/workbench/intellij/community/OpenDeveloperToolsAction.class"
    ]
    command_center_markers = (
        b"commandsForSuite",
        b"Workbench Command Center",
        b"Choose one exact core-owned tool suite",
        b"Documentation-only catalog entry",
    )
    if any(marker not in command_catalog_source + command_center_source
           for marker in command_center_markers):
        raise ClientBuildError("IntelliJ plugin omits the complete live-catalog Command Center")
    if (
        b"developer-features." in command_catalog_source
        or b"atlas.recipes-" in command_catalog_source
        or "Open Command Center" not in descriptor
    ):
        raise ClientBuildError("IntelliJ plugin retains a command-suite allowlist")
    return {
        "client_id": "intellij-community",
        "package_artifact_id": "intellij-community-zip",
        "version": INTELLIJ_VERSION,
        "host_boundary": "IntelliJ 262.*; com.intellij.modules.platform only",
    }


def _artifact_row(path: Path, verified: Mapping[str, Any], tools: Mapping[str, str]) -> dict[str, Any]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        **dict(verified),
        "path": path.name,
        "size": path.stat().st_size,
        "sha256": digest,
        "artifact_identity": "artifact:sha256:" + digest,
        "build_tools": dict(sorted(tools.items())),
    }


def build(
    output_dir: Path,
    *,
    component: str | None = None,
    lane: str = PUBLIC_LANE,
    skip_build: bool = False,
    skip_vscode_extension_host: bool = False,
) -> dict[str, Any]:
    if lane != PUBLIC_LANE:
        raise ClientBuildError(f"unsupported developer-client build lane: {lane}")
    selected = (
        {"workbench-vscode", "workbench-intellij-community"}
        if component is None
        else {component}
    )
    supported = {"workbench-vscode", "workbench-intellij-community"}
    if not selected <= supported:
        raise ClientBuildError(
            "unsupported developer-client component: " + ", ".join(sorted(selected))
        )
    vscode_name = VSCODE_ARTIFACT_NAME
    intellij_name = INTELLIJ_ARTIFACT_NAME
    verify_client_source_boundaries()
    output_dir.mkdir(parents=True, exist_ok=True)
    vscode = output_dir / vscode_name
    intellij = output_dir / intellij_name
    if "workbench-vscode" in selected:
        (output_dir / "workbench-vscode-extension-host-proof-v1.json").unlink(
            missing_ok=True
        )
    tools: dict[str, Mapping[str, str]] = {"vscode": {}, "intellij-community": {}}
    with tempfile.TemporaryDirectory(prefix="workbench-client-raw-", dir=output_dir) as directory:
        raw = Path(directory)
        raw_vscode = raw / vscode_name
        raw_intellij = raw / intellij_name
        if skip_build:
            source_vscode = (
                VSCODE_ROOT
                / f"workbench-vscode-{VSCODE_VERSION}.vsix"
            )
            source_intellij = (
                INTELLIJ_ROOT / "build/distributions" / INTELLIJ_ARTIFACT_NAME
            )
            required_sources = {
                "workbench-vscode": source_vscode,
                "workbench-intellij-community": source_intellij,
            }
            missing_sources = [
                path for name, path in required_sources.items()
                if name in selected and not path.is_file()
            ]
            if missing_sources:
                raise ClientBuildError(
                    "--skip-build requires existing component output: "
                    + ", ".join(str(path) for path in missing_sources)
                )
            if "workbench-vscode" in selected:
                shutil.copyfile(source_vscode, raw_vscode)
            if "workbench-intellij-community" in selected:
                shutil.copyfile(source_intellij, raw_intellij)
        else:
            if "workbench-vscode" in selected:
                tools["vscode"] = build_vscode(
                    raw_vscode,
                    skip_extension_host=skip_vscode_extension_host,
                )
            if "workbench-intellij-community" in selected:
                tools["intellij-community"] = build_intellij(raw_intellij)
        if "workbench-vscode" in selected:
            normalize_zip(raw_vscode, vscode)
        if "workbench-intellij-community" in selected:
            normalize_zip(raw_intellij, intellij)
    rows = []
    if "workbench-intellij-community" in selected:
        rows.append(
            _artifact_row(intellij, verify_intellij(intellij), tools["intellij-community"])
        )
    if "workbench-vscode" in selected:
        rows.append(_artifact_row(vscode, verify_vscode(vscode), tools["vscode"]))
    result: dict[str, Any] = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "client_artifact_manifest_id": "pending",
        "lane": PUBLIC_LANE,
        "artifacts": rows,
    }
    result["client_artifact_manifest_id"] = manifest_id(result)
    manifest_path = output_dir / "workbench-developer-clients-manifest-v1.json"
    manifest_path.write_bytes(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / ".workbench/distribution/developer-clients",
    )
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-vscode-extension-host", action="store_true")
    parser.add_argument(
        "--component",
        choices=("workbench-vscode", "workbench-intellij-community"),
        help="build one independently versioned client; omit to build both locally",
    )
    parser.add_argument(
        "--lane",
        choices=(PUBLIC_LANE,),
        default=PUBLIC_LANE,
        help="build the sole public-v1 component track",
    )
    args = parser.parse_args(argv)
    try:
        result = build(
            args.output_dir.resolve(),
            component=args.component,
            lane=args.lane,
            skip_build=args.skip_build,
            skip_vscode_extension_host=args.skip_vscode_extension_host,
        )
    except (
        ClientBuildError,
        ElementTree.ParseError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"developer client build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
