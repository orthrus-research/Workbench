"""Command-line orchestration for one World Studio development iteration."""

from __future__ import annotations

from workbench_api.resources import module_root as _module_resource_root, repository_root as _repository_resource_root

import argparse
from collections import deque
import json
import os
from pathlib import Path
import shlex
import signal
import secrets
import socket
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
import webbrowser

from workbench_api.canonical import canonical_json_bytes
from workbench_crucible_worldgen import (
    audit_population_capture,
    extract_causal_trace,
    join_same_run,
    seal_execution_envelope,
    seal_execution_terminal,
)

from .iteration import (
    IterationReport,
    WorldgenIterationError,
    configure_runtime,
    discover_gradle,
    discover_java,
    discover_runtime_template,
    executable_identity,
    find_built_artifact,
    freeze_world_studio_plan,
    gradle_version,
    install_mod,
    inventory_mods,
    java_major,
    java_version,
    load_profile,
    parse_region,
    provision_runtime,
    resolve_profile_path,
    sha256_file,
)


def log(message: str) -> None:
    print(f"[workbench-worldgen] {message}", flush=True)


def _default_label() -> str:
    return time.strftime("worldgen-%Y%m%d-%H%M%S", time.gmtime())


def _path_argument(value: str | None) -> Path | None:
    return Path(value).expanduser() if value else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen[Any], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise WorldgenIterationError(
                f"Strata viewer exited before becoming ready (exit {code})"
            )
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.2)
    raise WorldgenIterationError(
        f"Strata viewer did not listen on 127.0.0.1:{port} within {timeout:g}s"
    )


def _run_streaming(
    command: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    environment: Mapping[str, str] | None = None,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log("running " + shlex.join(command))
    recent: deque[str] = deque(maxlen=30)
    with log_path.open("w", encoding="utf-8") as output:
        output.write("$ " + shlex.join(command) + "\n")
        output.flush()
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment) if environment is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            recent.append(line)
            output.write(line)
            print(line, end="")
        return_code = process.wait()
        output.write(f"exit_code={return_code}\n")
    if return_code:
        tail = "".join(recent).strip()
        raise WorldgenIterationError(
            f"command exited {return_code}: {shlex.join(command)}"
            + (f"\nlast output:\n{tail}" if tail else "")
        )


def _run_json_tool(
    command: Sequence[str],
    *,
    cwd: Path,
    output_path: Path,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> tuple[dict[str, Any], int]:
    log("running " + shlex.join(command))
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in accepted_codes:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise WorldgenIterationError(
            f"JSON tool exited {completed.returncode}: {shlex.join(command)}"
            + (f"\n{detail}" if detail else "")
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise WorldgenIterationError(
            f"JSON tool returned invalid output: {shlex.join(command)}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise WorldgenIterationError("JSON tool returned a non-object document")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return value, completed.returncode


def _server_jar(runtime: Path, profile: Mapping[str, Any]) -> Path:
    matches = sorted(runtime.glob(profile["runtime"]["server_jar_glob"]))
    if len(matches) != 1:
        raise WorldgenIterationError(
            "disposable runtime does not contain exactly one selected Cleanroom server jar"
        )
    return matches[0]


def _regular_file(path: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise WorldgenIterationError(f"{label} cannot be a symlink: {expanded}")
    resolved = expanded.resolve()
    if not resolved.is_file():
        raise WorldgenIterationError(f"{label} is not a regular file: {resolved}")
    return resolved


def _artifact_binding(path: Path, role: str) -> dict[str, Any]:
    path = _regular_file(path, role)
    return {
        "role": role,
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _load_stage3_action_policy(
    profile_path: Path,
    profile: Mapping[str, Any],
) -> tuple[Path, dict[str, Any]]:
    policy_path = _regular_file(
        profile_path.parent / "supersymmetry-worldgen-action-policy-draft-v1.json",
        "Stage 3 worldgen action policy",
    )
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorldgenIterationError(f"cannot parse Stage 3 action policy: {exc}") from exc
    scope = policy.get("scope") if isinstance(policy, dict) else None
    if (
        not isinstance(scope, dict)
        or policy.get("format")
        != "workbench-supersymmetry-worldgen-action-policy-draft-v1"
        or policy.get("schema_version") != 1
        or policy.get("state") != "experimental-draft"
        or policy.get("owner_profile_id") != "workbench-pack:supersymmetry"
        or scope.get("platform_profile_id") != profile["platform_profile_id"]
        or scope.get("world_type") != profile["world_type"]
        or not isinstance(scope.get("generator_id"), str)
        or not scope["generator_id"]
    ):
        raise WorldgenIterationError("Stage 3 worldgen action policy/profile scope differs")
    blocked = {
        row.get("action")
        for row in policy.get("rules", [])
        if isinstance(row, dict) and row.get("disposition") == "block"
    }
    if not {
        "apply-to-protected-world",
        "promote-stable-support",
        "publish-authoritative-worldgen-semantics",
    } <= blocked:
        raise WorldgenIterationError("Stage 3 action policy lacks required fail-closed rules")
    return policy_path, policy


def _runtime_region(
    args: argparse.Namespace, profile: Mapping[str, Any]
) -> tuple[int, int, int, int]:
    if args.region:
        return parse_region(args.region)
    key = "fast_region" if args.mode == "fast" else "debug_region"
    value = profile["defaults"].get(key)
    if not isinstance(value, list) or len(value) != 4:
        raise WorldgenIterationError(f"profile defaults.{key} is invalid")
    return parse_region(",".join(str(part) for part in value))


def _passthrough_integrations(
    profile: Mapping[str, Any], mod_inventory: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    declarations = profile["runtime"].get("passthrough_integrations", [])
    if not isinstance(declarations, list):
        raise WorldgenIterationError(
            "profile runtime.passthrough_integrations must be an array"
        )
    result: dict[str, dict[str, Any]] = {}
    expected = {"filename_tokens", "integration_id", "mod_ids", "policy"}
    for declaration in declarations:
        if not isinstance(declaration, dict) or set(declaration) != expected:
            raise WorldgenIterationError(
                "profile passthrough integration has an unsupported shape"
            )
        integration_id = declaration["integration_id"]
        policy = declaration["policy"]
        filename_tokens = declaration["filename_tokens"]
        mod_ids = declaration["mod_ids"]
        if (
            not isinstance(integration_id, str)
            or not integration_id
            or not isinstance(policy, str)
            or not policy
            or not isinstance(filename_tokens, list)
            or not all(isinstance(token, str) and token for token in filename_tokens)
            or not isinstance(mod_ids, list)
            or not all(isinstance(mod_id, str) and mod_id for mod_id in mod_ids)
        ):
            raise WorldgenIterationError(
                "profile passthrough integration contains invalid values"
            )
        if integration_id in result:
            raise WorldgenIterationError(
                f"profile repeats passthrough integration {integration_id!r}"
            )
        matching = [
            dict(row)
            for row in mod_inventory
            if any(
                token.lower() in str(row.get("file", "")).lower()
                for token in filename_tokens
            )
            or any(
                mod_id in row.get("mod_ids", [])
                for mod_id in mod_ids
            )
        ]
        result[integration_id] = {
            "policy": policy,
            "present": bool(matching),
            "artifacts": matching,
            "modified_by_runner": False,
        }
    return result


def _reproduction_command(args: Sequence[str]) -> str:
    reusable: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--label":
            index += 2
            continue
        if argument.startswith("--label="):
            index += 1
            continue
        reusable.append(argument)
        index += 1
    return shlex.join(
        ["python3", "tools/workbench.py", "worldgen", "dev", *reusable]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workbench worldgen dev",
        description=(
            "Build World Studio, provision a fresh Cleanroom world, capture an "
            "aligned chunk window, summarize diagnostics, and hand it to Strata."
        ),
    )
    parser.add_argument("--profile", default="supersymmetry")
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--runtime-template", type=Path)
    parser.add_argument("--strata-root", type=Path)
    parser.add_argument("--java-cmd")
    parser.add_argument("--gradle-cmd")
    parser.add_argument("--plan", type=Path)
    artifact_selection = parser.add_mutually_exclusive_group()
    artifact_selection.add_argument(
        "--artifact",
        type=Path,
        help=(
            "install this exact production-remapped artifact without rebuilding; "
            "the path, size, hash, and Forge mod identity remain in the report"
        ),
    )
    parser.add_argument(
        "--observatory-artifact",
        type=Path,
        help=(
            "install this exact Worldgen Observatory artifact and produce the "
            "additive Stage 3 same-run V2 proof"
        ),
    )
    parser.add_argument("--seed", type=int)
    parser.add_argument(
        "--region",
        help="minChunkX,minChunkZ,widthChunks,heightChunks; up to 1024 chunks",
    )
    parser.add_argument(
        "--mode", choices=("fast", "debug", "performance"), default="debug"
    )
    parser.add_argument("--label", default=_default_label())
    parser.add_argument("--heap")
    parser.add_argument("--diagnostic-sample-modulo", type=int)
    parser.add_argument("--server-port", type=int)
    parser.add_argument("--viewer-port", type=int)
    parser.add_argument("--startup-timeout", type=int, default=300)
    parser.add_argument("--scan-timeout", type=int, default=600)
    parser.add_argument("--stop-timeout", type=int, default=60)
    parser.add_argument(
        "--compare",
        type=Path,
        metavar="BASELINE_LOG",
        help="write a same-seed semantic comparison; intentional differences do not fail the run",
    )
    artifact_selection.add_argument(
        "--skip-build",
        action="store_true",
        help="explicitly reuse the one existing remapped artifact and record that decision",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="validate the Strata handoff without leaving its local viewer running",
    )
    return parser


def run(argv: Sequence[str], *, root: Path) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv))
    iteration_root = root / ".workbench/iterations/worldgen" / args.label
    if iteration_root.exists():
        raise WorldgenIterationError(
            f"iteration label already exists; choose a fresh --label: {iteration_root}"
        )
    iteration_root.mkdir(parents=True)
    report = IterationReport.start(
        iteration_root / "iteration-report-v1.json",
        root,
        label=args.label,
        profile=args.profile,
        mode=args.mode,
        argv=argv,
    )
    report.value["reproduction_command"] = _reproduction_command(argv)
    report.write()

    runtime = iteration_root / "runtime"
    artifacts = iteration_root / "artifacts"
    logs = iteration_root / "logs"
    strata_output = artifacts / "strata"
    viewer_port = args.viewer_port or _free_port()

    profile: dict[str, Any]
    java: Path
    gradle: Path | None
    fixture: Path
    plan: Path
    runtime_template: Path
    region: tuple[int, int, int, int]
    seed: int
    heap: str
    sample_modulo: int
    strata_root: Path
    profile_path: Path
    observatory_artifact: Path | None = None
    action_policy_path: Path | None = None
    action_policy: dict[str, Any] | None = None
    raw_schema_path: Path | None = None
    causal_schema_path: Path | None = None
    probe_plan_path: Path | None = None
    environment = os.environ.copy()

    with report.stage(
        "preflight",
        "Resolve the exact profile, modern Java roles, build tool, runtime template, and sample before mutation.",
    ) as details:
        profile_path = (
            args.profile_file.expanduser().resolve()
            if args.profile_file
            else resolve_profile_path(root, args.profile)
        )
        profile = load_profile(profile_path, root)
        fixture = Path(profile["_fixture"])
        if args.observatory_artifact is not None:
            observatory_artifact = _regular_file(
                args.observatory_artifact,
                "Worldgen Observatory artifact",
            )
            if observatory_artifact.suffix.lower() != ".jar":
                raise WorldgenIterationError(
                    f"Worldgen Observatory artifact is not a JAR: {observatory_artifact}"
                )
            action_policy_path, action_policy = _load_stage3_action_policy(
                profile_path,
                profile,
            )
            resource_bounds = action_policy.get("resource_bounds")
            if (
                not isinstance(resource_bounds, dict)
                or resource_bounds.get("minecraft_watchdog_max_tick_time_ms") != -1
                or resource_bounds.get("scan_timeout_seconds") != args.scan_timeout
            ):
                raise WorldgenIterationError(
                    "Stage 3 action policy/resource bounds differ from this launch"
                )
            candidate_root = (
                root
                / "profiles/platforms/cleanroom/candidates"
                / profile["cleanroom"]
            )
            raw_schema_path = _regular_file(
                candidate_root
                / "worldgen-observatory-fixture/src/main/resources/"
                "workbench-worldgen-observatory-raw-v1.schema.json",
                "Worldgen Observatory raw schema",
            )
            causal_schema_path = _regular_file(
                root / "modules/crucible/schemas/worldgen-causal-trace-v2.schema.json",
                "World Studio causal schema",
            )
            probe_plan_path = _regular_file(
                candidate_root / "worldgen-observatory-probe-plan-v1.json",
                "Worldgen Observatory probe plan",
            )
        raw_plan = args.plan.expanduser() if args.plan else Path(profile["_plan"])
        if raw_plan.is_symlink():
            raise WorldgenIterationError(
                f"selected Groovy plan cannot be a symlink: {raw_plan}"
            )
        plan = raw_plan.resolve()
        if not plan.is_file():
            raise WorldgenIterationError(f"selected Groovy plan is not a regular file: {plan}")
        configured_template = args.runtime_template or _path_argument(
            os.environ.get("WORKBENCH_WORLDGEN_RUNTIME_TEMPLATE")
        )
        runtime_template = discover_runtime_template(root, profile, configured_template)
        java = discover_java(root, args.java_cmd)
        java_home = java.parent.parent
        environment["JAVA_HOME"] = str(java_home)
        java_identity = executable_identity(java, ["-version"])
        minimum_java = profile["toolchains"].get("minimum_cleanroom_java_major")
        if not isinstance(minimum_java, int) or java_major(java_identity) < minimum_java:
            raise WorldgenIterationError(
                f"Cleanroom runtime requires Java {minimum_java}+ for this profile"
            )
        proven_gradle = profile["toolchains"].get("proven_gradle")
        if not isinstance(proven_gradle, str) or not proven_gradle:
            raise WorldgenIterationError("profile toolchains.proven_gradle is invalid")
        gradle = (
            None
            if args.skip_build or args.artifact is not None
            else discover_gradle(fixture, args.gradle_cmd, proven_gradle)
        )
        gradle_identity = (
            None
            if gradle is None
            else executable_identity(gradle, ["--version"], environment=environment)
        )
        automatic_java = args.java_cmd is None and not os.environ.get(
            "WORKBENCH_CLEANROOM_JAVA"
        )
        proven_java = profile["toolchains"].get("proven_cleanroom_java")
        if (
            automatic_java
            and isinstance(proven_java, str)
            and java_version(java_identity) != proven_java
        ):
            raise WorldgenIterationError(
                f"automatic Java selection is not the profile-proven {proven_java}; "
                "pass --java-cmd to make an intentional override"
            )
        automatic_gradle = (
            gradle is not None
            and args.gradle_cmd is None
            and not os.environ.get("WORKBENCH_GRADLE")
        )
        if (
            automatic_gradle
            and gradle_identity is not None
            and gradle_version(gradle_identity) != proven_gradle
        ):
            raise WorldgenIterationError(
                f"automatic Gradle selection is not the profile-proven {proven_gradle}; "
                "pass --gradle-cmd to make an intentional override"
            )
        strata_root = (
            args.strata_root
            or _path_argument(os.environ.get("WORKBENCH_STRATA_ROOT"))
            or root.parent / "strata"
        ).expanduser().resolve()
        if not (strata_root / "tools/capture_dense_chunk_package.py").is_file():
            raise WorldgenIterationError(
                f"Strata capture checkout is unavailable or incompatible: {strata_root}"
            )
        region = _runtime_region(args, profile)
        defaults = profile["defaults"]
        raw_seed = args.seed if args.seed is not None else defaults.get("seed")
        if not isinstance(raw_seed, int):
            raise WorldgenIterationError("seed must be an integer")
        seed = raw_seed
        heap = args.heap or defaults.get("heap")
        if not isinstance(heap, str) or not heap:
            raise WorldgenIterationError("heap must be a non-empty JVM size")
        raw_modulo = (
            args.diagnostic_sample_modulo
            if args.diagnostic_sample_modulo is not None
            else defaults.get("diagnostic_sample_modulo")
        )
        if not isinstance(raw_modulo, int) or raw_modulo < 1:
            raise WorldgenIterationError("diagnostic sample modulo must be positive")
        sample_modulo = raw_modulo
        details.update(
            {
                "profile_path": str(profile_path),
                "platform_profile_id": profile["platform_profile_id"],
                "minecraft_version": profile["minecraft_version"],
                "cleanroom": profile["cleanroom"],
                "fixture": str(fixture),
                "plan": str(plan),
                "runtime_template": str(runtime_template),
                "strata_root": str(strata_root),
                "java": java_identity,
                "gradle": gradle_identity,
                "build_skipped": args.skip_build or args.artifact is not None,
                "artifact_override": (
                    None if args.artifact is None else str(args.artifact.expanduser())
                ),
                "observatory_artifact": (
                    None if observatory_artifact is None else str(observatory_artifact)
                ),
                "stage3_same_run_proof": observatory_artifact is not None,
                "seed": seed,
                "region": list(region),
                "heap": heap,
                "diagnostic_sample_modulo": sample_modulo,
            }
        )
        report.set_input("profile", {
            "file": str(profile_path),
            "sha256": sha256_file(profile_path),
            "profile_id": profile["profile_id"],
            "platform_profile_id": profile["platform_profile_id"],
        })
        report.set_input("toolchains", {"java": java_identity, "gradle": gradle_identity})
        report.set_input("sample", {"seed": seed, "region": list(region), "mode": args.mode})
        if observatory_artifact is not None:
            assert action_policy_path is not None
            assert raw_schema_path is not None
            assert causal_schema_path is not None
            assert probe_plan_path is not None
            report.set_input(
                "worldgen_observatory_artifact",
                {
                    "path": str(observatory_artifact),
                    "sha256": sha256_file(observatory_artifact),
                    "size_bytes": observatory_artifact.stat().st_size,
                },
            )
            report.set_input(
                "stage3_worldgen_boundary",
                {
                    "action_policy": {
                        "path": str(action_policy_path),
                        "sha256": sha256_file(action_policy_path),
                        "state": "experimental-draft",
                    },
                    "causal_schema": {
                        "path": str(causal_schema_path),
                        "sha256": sha256_file(causal_schema_path),
                    },
                    "probe_plan": {
                        "path": str(probe_plan_path),
                        "sha256": sha256_file(probe_plan_path),
                    },
                    "raw_schema": {
                        "path": str(raw_schema_path),
                        "sha256": sha256_file(raw_schema_path),
                    },
                },
            )

    if args.artifact is not None:
        log(
            "preflight complete: the next stage verifies the caller-selected exact "
            "artifact so the iteration cannot silently substitute mutable build output"
        )
    elif args.skip_build:
        log(
            "preflight complete: the next stage records the explicit diagnostic build "
            "skip and resolves the one existing remapped artifact"
        )
    else:
        log(
            "preflight complete: the next stage compiles the current source so the "
            "iteration cannot accidentally inspect a stale mod jar"
        )
    with report.stage(
        "build",
        "Resolve one exact production-remapped Cleanroom mod, compiling current source unless an explicit selection policy says otherwise.",
    ) as details:
        if args.artifact is not None:
            requested_artifact = args.artifact.expanduser()
            if requested_artifact.is_symlink():
                raise WorldgenIterationError(
                    f"selected artifact cannot be a symlink: {requested_artifact}"
                )
            artifact = requested_artifact.resolve()
            if not artifact.is_file():
                raise WorldgenIterationError(
                    f"selected artifact is not a regular file: {artifact}"
                )
            if artifact.suffix.lower() != ".jar":
                raise WorldgenIterationError(
                    f"selected artifact is not a JAR: {artifact}"
                )
            log("using the caller-selected exact production artifact; no build was run")
            details["skipped"] = True
            details["reason"] = "caller supplied --artifact"
            details["selection"] = "explicit-exact-artifact"
        elif args.skip_build:
            log("build explicitly skipped; selecting and hashing the existing remapped artifact")
            details["skipped"] = True
            details["reason"] = "caller supplied --skip-build"
            details["selection"] = "existing-profile-build-output"
            artifact = find_built_artifact(root, profile)
        else:
            assert gradle is not None
            command = [
                str(gradle),
                "--no-daemon",
                "-p",
                str(fixture),
                "--project-cache-dir",
                str(root / ".workbench/gradle-project-cache/worldgen-prototype-fixture"),
                "clean",
                "remapJar",
            ]
            details["command"] = command
            _run_streaming(command, cwd=root, log_path=logs / "build.log", environment=environment)
            artifact = find_built_artifact(root, profile)
            details["selection"] = "fresh-profile-build"
        details["artifact"] = str(artifact)
        details["artifact_sha256"] = sha256_file(artifact)
        report.set_input(
            "worldgen_artifact",
            {
                "path": str(artifact),
                "sha256": sha256_file(artifact),
                "size_bytes": artifact.stat().st_size,
                "build_skipped": args.skip_build or args.artifact is not None,
                "selection": details["selection"],
            },
        )

    log(
        "provisioning a new runtime: worlds, logs, caches, prior observer output, "
        "and retained experiments are excluded; every copied file has an independent inode"
    )
    with report.stage(
        "provision",
        "Create a fresh disposable runtime without mutating the source template or any existing world.",
    ) as details:
        details.update(provision_runtime(runtime_template, runtime, profile))

    log(
        "configuring one frozen Groovy plan and installing the exact selected mod; "
        "profile-declared passthrough integrations remain untouched"
    )
    with report.stage(
        "configure",
        "Select the world type and seed, enforce a fresh world, install one mod artifact, and freeze one Groovy plan.",
    ) as details:
        runtime_config = configure_runtime(
            runtime,
            seed=seed,
            world_type=profile["world_type"],
            server_port=args.server_port,
            max_tick_time_ms=-1 if observatory_artifact is not None else None,
        )
        mod_id = profile["artifact"].get("mod_id")
        if not isinstance(mod_id, str) or not mod_id:
            raise WorldgenIterationError("profile artifact.mod_id is invalid")
        installation = install_mod(runtime, artifact, mod_id)
        observatory_installation = (
            None
            if observatory_artifact is None
            else install_mod(
                runtime,
                observatory_artifact,
                "workbench_worldgen_observatory",
            )
        )
        frozen_plan = freeze_world_studio_plan(runtime, plan)
        mod_inventory = inventory_mods(runtime)
        passthrough = _passthrough_integrations(profile, mod_inventory)
        details.update(
            {
                "runtime": runtime_config,
                "installation": installation,
                "observatory_installation": observatory_installation,
                "plan": frozen_plan,
                "mod_count": len(mod_inventory),
                "passthrough_integrations": passthrough,
            }
        )
        report.set_input("plan", frozen_plan)
        report.set_input("runtime_template", str(runtime_template))
        report.set_input("runtime_mods", mod_inventory)
        report.set_input("integration_boundaries", passthrough)
        report.set_output("runtime", str(runtime))

    min_x, min_z, width, height = region
    micro_region = width == 16 and height == 16
    server_jar = _server_jar(runtime, profile)
    execution_envelope = None
    stage3_artifacts = artifacts / "worldgen-v2"
    raw_capture_path = stage3_artifacts / "observatory.raw.ndjson"
    if observatory_artifact is not None:
        assert action_policy_path is not None
        assert action_policy is not None
        assert raw_schema_path is not None
        assert causal_schema_path is not None
        assert probe_plan_path is not None
        frozen_plan_path = runtime / "groovy/postInit/workbench_world_studio_plan.groovy"
        strata_capture_runner = _regular_file(
            root / "modules/crucible/tools/run_strata_observation.py",
            "Strata capture runner",
        )
        strata_observer_source = _regular_file(
            strata_root
            / "tools/worldgen-observer/src/main/java/strata/worldgenobserver/"
            "StrataWorldgenObserverMod.java",
            "Strata observer source",
        )
        envelope_artifacts = [
            _artifact_binding(action_policy_path, "action-policy"),
            _artifact_binding(causal_schema_path, "world-studio-causal-schema"),
            _artifact_binding(server_jar, "cleanroom-server"),
            _artifact_binding(profile_path, "worldgen-iteration-profile"),
            _artifact_binding(observatory_artifact, "worldgen-observatory-mod"),
            _artifact_binding(probe_plan_path, "worldgen-observatory-probe-plan"),
            _artifact_binding(raw_schema_path, "worldgen-observatory-raw-schema"),
            _artifact_binding(strata_capture_runner, "strata-capture-runner"),
            _artifact_binding(strata_observer_source, "strata-observer-source"),
            _artifact_binding(frozen_plan_path, "world-studio-plan"),
            _artifact_binding(artifact, "world-studio-mod"),
        ]
        execution_envelope = seal_execution_envelope(
            workspace_key=root.name,
            profile_id=profile["profile_id"],
            platform_profile_id=profile["platform_profile_id"],
            execution_nonce=secrets.token_hex(32),
            seed=seed,
            dimension=0,
            region=region,
            world_type=profile["world_type"],
            generator_id=action_policy["scope"]["generator_id"],
            max_tick_time_ms=-1,
            scan_timeout_seconds=args.scan_timeout,
            artifacts=envelope_artifacts,
            action_policy_id=(
                "supersymmetry-worldgen-action-policy:sha256:"
                + sha256_file(action_policy_path)
            ),
        )
        stage3_artifacts.mkdir(parents=True, exist_ok=False)
        envelope_path = stage3_artifacts / "execution-envelope-v2.json"
        envelope_path.write_bytes(execution_envelope.canonical_bytes)
        envelope_value = execution_envelope.to_dict()
        (stage3_artifacts / "context-ref-v2.json").write_bytes(
            canonical_json_bytes(envelope_value["context_ref"])
        )
        (stage3_artifacts / "input-binding-v2.json").write_bytes(
            canonical_json_bytes(envelope_value["input_binding"])
        )
        report.set_output(
            "stage3_execution_envelope",
            {
                "context_ref_id": execution_envelope.context_ref_id,
                "execution_envelope_id": execution_envelope.envelope_id,
                "input_binding_id": execution_envelope.input_binding_id,
                "path": str(envelope_path),
                "run_plan_id": execution_envelope.run_plan_id,
            },
        )
    capture_command = [
        sys.executable,
        str(root / "modules/crucible/tools/run_strata_observation.py"),
        "--strata-root",
        str(strata_root),
        "--runtime",
        str(runtime),
        "--server-jar",
        str(server_jar),
        "--java-cmd",
        str(java),
        "--dimension",
        "0",
        "--min-chunk-x",
        str(min_x),
        "--min-chunk-z",
        str(min_z),
        "--chunk-size-x",
        str(width),
        "--chunk-size-z",
        str(height),
        "--halo-chunks",
        str(profile["defaults"].get("halo_chunks", 1)),
        "--tile-size",
        "4" if micro_region else "2",
        "--sample-profile",
        "micro-region" if micro_region else "custom",
        "--manifest-version",
        "2" if micro_region else "1",
        "--heap",
        heap,
        "--startup-timeout",
        str(args.startup_timeout),
        "--scan-timeout",
        str(args.scan_timeout),
        "--stop-timeout",
        str(args.stop_timeout),
        "--viewer-port",
        str(viewer_port),
        "--label",
        args.label,
        "--output-root",
        str(strata_output),
        f"--jvm-arg=-Dworkbench.worldgen.diagnostics=true",
        f"--jvm-arg=-Dworkbench.worldgen.diagnostics.sample_modulo={sample_modulo}",
        "--jvm-arg=-Dworkbench.worldgen.jfr.biome_points=false",
    ]
    if execution_envelope is not None:
        capture_command.extend(
            [
                "--jvm-arg=-Dworkbench.worldgen.execution_envelope_id="
                + execution_envelope.envelope_id,
                f"--jvm-arg=-Dworkbench.worldgen.causal.min_chunk_x={min_x}",
                f"--jvm-arg=-Dworkbench.worldgen.causal.min_chunk_z={min_z}",
                "--jvm-arg=-Dworkbench.worldgen.causal.max_chunk_x_exclusive="
                + str(min_x + width),
                "--jvm-arg=-Dworkbench.worldgen.causal.max_chunk_z_exclusive="
                + str(min_z + height),
                "--jvm-arg=-Dworkbench.worldgen.observatory.probe.enabled=true",
                "--jvm-arg=-Dworkbench.worldgen.observatory.probe.capture_id="
                + execution_envelope.envelope_id,
                "--jvm-arg=-Dworkbench.worldgen.observatory.probe.mode=trace",
                "--jvm-arg=-Dworkbench.worldgen.observatory.probe.output="
                + str(raw_capture_path.resolve()),
                "--jvm-arg=-Dworkbench.worldgen.observatory.probe.defer_until_fixture_driver=true",
                "--jvm-arg=-Dworkbench.worldgen.observatory.iteration_auto.enabled=true",
                "--jvm-arg=-Dworkbench.worldgen.observatory.iteration_auto.min_chunk_x="
                + str(min_x),
                "--jvm-arg=-Dworkbench.worldgen.observatory.iteration_auto.min_chunk_z="
                + str(min_z),
                "--jvm-arg=-Dworkbench.worldgen.observatory.iteration_auto.max_chunk_x_exclusive="
                + str(min_x + width),
                "--jvm-arg=-Dworkbench.worldgen.observatory.iteration_auto.max_chunk_z_exclusive="
                + str(min_z + height),
                "--jvm-arg=-Dworkbench.worldgen.observatory.synthetic.enabled=false",
            ]
        )
    recording = runtime / "worldgen-iteration.jfr"
    if args.mode == "performance":
        capture_command.extend(
            [
                "--jvm-arg=-XX:FlightRecorderOptions=stackdepth=128",
                "--jvm-arg=-XX:StartFlightRecording="
                f"filename={recording},settings=profile,dumponexit=true",
            ]
        )

    log(
        "starting Cleanroom because final terrain and mod interactions only exist "
        "inside the assembled runtime; Strata will request only the aligned sample, "
        "then stop the server cleanly"
    )
    with report.stage(
        "capture",
        "Launch the exact disposable Cleanroom server, generate/populate the selected chunks, and package final state with Strata.",
    ) as details:
        details["command"] = capture_command
        details["sample_profile"] = "micro-region" if micro_region else "custom"
        _run_streaming(
            capture_command,
            cwd=root,
            log_path=logs / "strata-capture.log",
            environment=environment,
        )
        details["output"] = str(strata_output)
        report.set_output("strata", str(strata_output))

    log(
        "reducing the raw launch log into chunk, biome, lithology, hydrology, cache, "
        "carver, population, failure, and latency summaries"
    )
    with report.stage(
        "summarize",
        "Turn sampled World Studio diagnostics into a compact decision-oriented report and reject an unhealthy run.",
    ) as details:
        launch_log = runtime / "logs" / f"strata-scan-{args.label}.log"
        summarizer = fixture / "tools/summarize_world_studio_log.py"
        summary_path = artifacts / "world-studio-summary.json"
        summary, _ = _run_json_tool(
            [sys.executable, str(summarizer), str(launch_log)],
            cwd=root,
            output_path=summary_path,
        )
        runtime_health = summary.get("runtime")
        failures = summary.get("prototype_failures")
        generation = summary.get("generation")
        if (
            not isinstance(runtime_health, dict)
            or runtime_health.get("ready") is not True
            or runtime_health.get("clean_stop_observed") is not True
            or runtime_health.get("invalid_prototype_records") != 0
            or not isinstance(failures, list)
            or failures
            or not isinstance(generation, dict)
            or not isinstance(generation.get("chunks"), int)
            or generation["chunks"] < 1
        ):
            raise WorldgenIterationError(
                f"World Studio diagnostic health check failed; inspect {summary_path}"
            )
        details.update(
            {
                "summary": str(summary_path),
                "generated_chunks_with_diagnostics": generation["chunks"],
                "hydrology": summary.get("hydrology"),
                "latency": generation.get("latency"),
            }
        )
        report.set_output("world_studio_summary", str(summary_path))
        report.set_output("cleanroom_launch_log", str(launch_log))

    if execution_envelope is not None:
        assert raw_schema_path is not None
        assert causal_schema_path is not None
        assert probe_plan_path is not None
        log(
            "auditing the bounded Observatory prefix, replaying World Studio RNG, "
            "and joining the exact writes to Strata final state"
        )
        with report.stage(
            "stage3_same_run_proof",
            "Validate and join Observatory, World Studio, and Strata under one prelaunch execution envelope.",
        ) as details:
            strata_receipt_path = strata_output / "strata-observation-receipt-v1.json"
            strata_scan_path = (
                runtime / "strata-worldgen-observer" / f"{args.label}.json"
            )
            audit = audit_population_capture(
                raw_capture_path,
                envelope=execution_envelope,
                raw_schema_path=raw_schema_path,
                probe_plan_path=probe_plan_path,
                requested_mode="trace",
            )
            causal = extract_causal_trace(
                launch_log,
                envelope=execution_envelope,
                causal_schema_path=causal_schema_path,
            )
            joined = join_same_run(
                envelope=execution_envelope,
                audit=audit,
                causal=causal,
                strata_receipt_path=strata_receipt_path,
                strata_scan_path=strata_scan_path,
            )
            terminal = seal_execution_terminal(
                execution_envelope,
                process_exit_code=0,
                process_outcome="complete",
                producer_receipt_ids=[
                    audit.audit_id,
                    causal.receipt_id,
                    joined.receipt_id,
                    joined.strata_receipt_id,
                ],
                launch_log_sha256=causal.launch_log_sha256,
                raw_capture_sha256=audit.raw_sha256,
            )
            proof_files = {
                "capture_audit": stage3_artifacts
                / "population-capture-audit-v2.json",
                "causal_receipt": stage3_artifacts
                / "causal-trace-receipt-v2.json",
                "join_receipt": stage3_artifacts / "same-run-join-receipt-v2.json",
                "terminal": stage3_artifacts / "execution-terminal-v2.json",
            }
            proof_files["capture_audit"].write_bytes(audit.canonical_bytes)
            proof_files["causal_receipt"].write_bytes(causal.canonical_bytes)
            proof_files["join_receipt"].write_bytes(joined.canonical_bytes)
            proof_files["terminal"].write_bytes(terminal.canonical_bytes)
            details.update(
                {
                    "capture_audit_id": audit.audit_id,
                    "causal_receipt_id": causal.receipt_id,
                    "execution_envelope_id": execution_envelope.envelope_id,
                    "join_receipt_id": joined.receipt_id,
                    "population_roots": audit.population_root_count,
                    "raw_records": audit.record_count,
                    "realized_sites": sum(
                        site["site_state"] == "realized" for site in joined.sites
                    ),
                    "terminal_id": terminal.terminal_id,
                    "terminal_writes": audit.terminal_write_count,
                }
            )
            report.set_output(
                "stage3_same_run_proof",
                {
                    "capture_audit_id": audit.audit_id,
                    "causal_receipt_id": causal.receipt_id,
                    "execution_terminal_id": terminal.terminal_id,
                    "files": {key: str(path) for key, path in proof_files.items()},
                    "join_receipt_id": joined.receipt_id,
                    "strata_receipt_id": joined.strata_receipt_id,
                },
            )

    if args.compare:
        with report.stage(
            "compare",
            "Compare same-seed semantic chunk records while excluding the declared telemetry allowlist.",
        ) as details:
            baseline = args.compare.expanduser().resolve()
            if not baseline.is_file():
                raise WorldgenIterationError(f"comparison baseline is unavailable: {baseline}")
            comparison_path = artifacts / "world-studio-comparison.json"
            comparison, code = _run_json_tool(
                [
                    sys.executable,
                    str(fixture / "tools/compare_world_studio_logs.py"),
                    str(baseline),
                    str(launch_log),
                ],
                cwd=root,
                output_path=comparison_path,
                accepted_codes=frozenset({0, 1}),
            )
            details.update(
                {
                    "baseline": str(baseline),
                    "comparison": str(comparison_path),
                    "equivalent": comparison.get("equivalent"),
                    "tool_exit_code": code,
                }
            )
            report.set_output("comparison", str(comparison_path))

    if args.mode == "performance":
        log("summarizing only the four bounded World Studio JFR event types")
        with report.stage(
            "performance",
            "Summarize bounded World Studio JFR events from the same modern JDK used by Cleanroom.",
        ) as details:
            if not recording.is_file():
                raise WorldgenIterationError(f"expected JFR recording is missing: {recording}")
            jfr = java.parent / "jfr"
            jfr_summary_path = artifacts / "world-studio-jfr-summary.json"
            jfr_summary, _ = _run_json_tool(
                [
                    sys.executable,
                    str(fixture / "tools/summarize_world_studio_jfr.py"),
                    "--jfr-bin",
                    str(jfr),
                    str(recording),
                ],
                cwd=root,
                output_path=jfr_summary_path,
            )
            details.update(
                {
                    "recording": str(recording),
                    "summary": str(jfr_summary_path),
                    "event_counts": jfr_summary.get("event_counts"),
                }
            )
            report.set_output("jfr_recording", str(recording))
            report.set_output("jfr_summary", str(jfr_summary_path))

    handoff = strata_output / "viewer-handoff.json"
    log("validating the exact Strata manifest-to-viewer handoff before presenting it")
    with report.stage(
        "handoff",
        "Validate that the viewer is bound to this iteration's retained manifest and local-only endpoint.",
    ) as details:
        check_command = [
            sys.executable,
            str(root / "modules/crucible/tools/serve_strata_observation.py"),
            str(handoff),
            "--check",
        ]
        _run_streaming(
            check_command,
            cwd=root,
            log_path=logs / "viewer-handoff-check.log",
            environment=environment,
        )
        handoff_value = json.loads(handoff.read_text(encoding="utf-8"))
        details.update({"handoff": str(handoff), "url": handoff_value["url"]})
        report.set_output("viewer_handoff", str(handoff))
        report.set_output("viewer_url", handoff_value["url"])

    if not args.no_open:
        log("starting the local Strata viewer; its PID and stop command will be retained")
        with report.stage(
            "open_viewer",
            "Start the checked local Strata viewer and request the operating system browser to open its deep link.",
        ) as details:
            viewer_log_path = logs / "viewer.log"
            viewer_log = viewer_log_path.open("w", encoding="utf-8")
            viewer_command = [
                sys.executable,
                str(root / "modules/crucible/tools/serve_strata_observation.py"),
                str(handoff),
            ]
            process = subprocess.Popen(
                viewer_command,
                cwd=root,
                env=environment,
                stdout=viewer_log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            try:
                _wait_for_port(viewer_port, process)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                viewer_log.close()
                raise
            viewer_log.close()
            browser_opened = webbrowser.open(str(handoff_value["url"]), new=2)
            details.update(
                {
                    "pid": process.pid,
                    "url": handoff_value["url"],
                    "browser_opened": browser_opened,
                    "log": str(viewer_log_path),
                    "stop_command": f"kill -- -{process.pid}",
                }
            )
            report.set_output(
                "viewer_process",
                {
                    "pid": process.pid,
                    "url": handoff_value["url"],
                    "log": str(viewer_log_path),
                    "stop_command": f"kill -- -{process.pid}",
                },
            )

    report.complete()
    log(f"iteration complete: {report.path}")
    log(f"inspect: {report.value['outputs']['viewer_url']}")
    log(f"reproduce: {report.value['reproduction_command']}")
    return 0


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    workbench_root = (
        root.resolve()
        if root is not None
        else _repository_resource_root(__file__)
    )
    try:
        return run(arguments, root=workbench_root)
    except (OSError, ValueError, WorldgenIterationError) as exc:
        print(f"Worldgen iteration failed: {exc}", file=sys.stderr)
        return 1


__all__ = ["build_parser", "main", "run"]
