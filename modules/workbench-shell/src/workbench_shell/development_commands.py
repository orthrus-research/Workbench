"""Shell-owned development commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)


def _dev_parser() -> argparse.ArgumentParser:
    from workbench_profile_supersymmetry.console import SERVER_EXPERIMENTS

    parser = argparse.ArgumentParser(
        prog="workbench dev",
        description=(
            "Run one exact constituent-mod candidate on its applicable "
            "Supersymmetry client/server sides, or use the lower-level build, "
            "stage, launch, and comparison routes for focused work. Every run "
            "invocation creates fresh selected-side physical attempts."
        ),
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=(
            "run",
            "show",
            "build",
            "stage",
            "launch",
            "launch-server",
            "check",
        ),
        default="show",
        help=(
            "run the primary retained candidate loop; or show a plan, build an "
            "overlay, stage a client, launch one side, or compare server health"
        ),
    )
    parser.add_argument(
        "--project",
        type=Path,
        help="constituent mod checkout (defaults to the current directory)",
    )
    parser.add_argument(
        "--pack",
        type=Path,
        help=(
            "exact Supersymmetry Packwiz checkout used by a fresh run or the "
            "lower-level show/build/stage actions for replacement matching"
        ),
    )
    parser.add_argument(
        "--run",
        help=(
            "exact retained candidate run ID to reopen without rebuilding; required by "
            "the lower-level launch, launch-server, and check actions"
        ),
    )
    parser.add_argument(
        "--pack-mod",
        help="exact mods/*.pw.toml basename when automatic identity matching is ambiguous",
    )
    parser.add_argument(
        "--java-home",
        type=Path,
        help="explicit build JDK; otherwise use the matching managed or Gradle JDK",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="fresh-run or lower-level build timeout (1-7200; default: 1200)",
    )
    parser.add_argument(
        "--launcher",
        choices=("prism", "multimc"),
        help="client launcher family for run or launch (default: prism)",
    )
    parser.add_argument(
        "--launcher-executable",
        type=Path,
        help="explicit Prism or MultiMC executable for a selected client side",
    )
    parser.add_argument(
        "--launcher-root",
        type=Path,
        help="explicit initialized Prism or MultiMC data root",
    )
    launch_identity = parser.add_mutually_exclusive_group()
    launch_identity.add_argument(
        "--launcher-profile",
        help="existing launcher profile; retained evidence redacts its name",
    )
    launch_identity.add_argument(
        "--offline-name",
        help="offline player name when no launcher profile is selected",
    )
    parser.add_argument(
        "--launcher-java",
        type=Path,
        help="explicit launch JDK executable",
    )
    parser.add_argument(
        "--launcher-java-state",
        type=Path,
        help="managed launch-JDK state root",
    )
    parser.add_argument(
        "--server-template",
        type=Path,
        help=(
            "existing measured, world-free SUSY server template for a selected "
            "server side; otherwise a retained client stage is required for "
            "managed materialization"
        ),
    )
    parser.add_argument(
        "--accept-minecraft-eula",
        action="store_true",
        help=(
            "accept the Minecraft EULA for this invocation's automatic "
            "dedicated-server materialization; invalid with --server-template"
        ),
    )
    parser.add_argument(
        "--side",
        choices=("auto", "client", "server", "both"),
        help=(
            "run side (default: auto from exact Packwiz metadata); the "
            "lower-level check v1 requires server"
        ),
    )
    parser.add_argument(
        "--server-java",
        type=Path,
        help="explicit native server JDK executable",
    )
    parser.add_argument(
        "--memory",
        type=int,
        help="client or dedicated-server attempt memory in MiB (default: 8192)",
    )
    parser.add_argument(
        "--launch-timeout",
        type=float,
        help="client or dedicated-server attempt timeout in seconds (default: 600)",
    )
    parser.add_argument(
        "--shutdown-timeout",
        type=float,
        help=(
            "dedicated-server launch/comparison graceful shutdown timeout "
            "in seconds (default: 180)"
        ),
    )
    parser.add_argument(
        "--runtime-experiment",
        action="append",
        choices=tuple(sorted(SERVER_EXPERIMENTS)),
        help=(
            "explicit disposable-projection runtime experiment; repeat for "
            "additional allowlisted experiments"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "emit the complete unified run, plan, retained phase, or "
            "comparison result"
        ),
    )
    return parser

def _dev_main(argv: list[str]) -> int:
    from workbench_shell.susy_mod_dev import (
        SusyModDevError,
        execute_susy_mod_build,
        plan_susy_mod_dev,
        render_susy_mod_plan,
        render_susy_mod_result,
    )
    from workbench_shell.susy_mod_launch import (
        COMPATIBILITY_EXPERIMENTS,
        SusyModLaunchError,
        launch_susy_mod_client,
        render_susy_mod_launch,
    )
    from workbench_shell.susy_mod_check import (
        SusyModCheckError,
        check_susy_mod_server,
        render_susy_mod_check,
    )
    from workbench_shell.susy_mod_server import (
        SusyModServerError,
        launch_susy_mod_server,
        render_susy_mod_server,
    )
    from workbench_shell.susy_server_materialize import (
        SusyServerMaterializationError,
    )
    from workbench_shell.susy_mod_run import (
        SusyModRunError,
        render_susy_mod_run,
        run_susy_mod,
    )

    parser = _dev_parser()
    args = parser.parse_args(argv)
    if args.action == "run":
        side = "auto" if args.side is None else args.side
        fresh = args.run is None
        if fresh and args.pack is None:
            parser.error("run requires either --run or --pack")
        if not fresh and args.pack is not None:
            parser.error("run accepts exactly one of --run or --pack")
        if not fresh:
            for value, option in (
                (args.project, "--project"),
                (args.pack_mod, "--pack-mod"),
                (args.java_home, "--java-home"),
                (args.timeout, "--timeout"),
            ):
                if value is not None:
                    parser.error(f"{option} is only valid when run builds from --pack")
        if args.server_template is not None and args.accept_minecraft_eula:
            parser.error(
                "--accept-minecraft-eula cannot be used with --server-template"
            )
        client_options = (
            (args.launcher, "--launcher"),
            (args.launcher_executable, "--launcher-executable"),
            (args.launcher_root, "--launcher-root"),
            (args.launcher_profile, "--launcher-profile"),
            (args.offline_name, "--offline-name"),
            (args.launcher_java, "--launcher-java"),
            (args.launcher_java_state, "--launcher-java-state"),
        )
        server_options = (
            (args.server_template, "--server-template"),
            (args.server_java, "--server-java"),
            (args.shutdown_timeout, "--shutdown-timeout"),
        )
        if side == "client":
            for value, option in server_options:
                if value is not None:
                    parser.error(f"{option} is not valid with --side client")
            if args.accept_minecraft_eula:
                parser.error(
                    "--accept-minecraft-eula is not valid with --side client"
                )
            server_only_experiments = sorted(
                set(args.runtime_experiment or ()) - set(COMPATIBILITY_EXPERIMENTS)
            )
            if server_only_experiments:
                parser.error(
                    f"{', '.join(server_only_experiments)} is not valid with "
                    "--side client"
                )
        elif side == "server":
            for value, option in client_options:
                if value is not None:
                    parser.error(f"{option} is not valid with --side server")
        if side in {"server", "both"} and args.server_template is None and not (
            args.accept_minecraft_eula
        ):
            parser.error(
                f"run --side {side} requires exactly one of --server-template "
                "or --accept-minecraft-eula"
            )
        # With --side auto, the owner derives applicability from the exact
        # retained Packwiz metadata.  Both option families therefore remain
        # admissible inputs; flags never select a side implicitly.
    elif args.action in {"launch", "launch-server", "check"}:
        if not args.run:
            parser.error(f"{args.action} requires --run")
        if args.pack is not None:
            parser.error(f"{args.action} reopens --run and does not accept --pack")
        for value, option in (
            (args.project, "--project"),
            (args.pack_mod, "--pack-mod"),
            (args.java_home, "--java-home"),
        ):
            if value is not None:
                parser.error(f"{option} is only valid with show, build, or stage")
        if args.timeout is not None:
            parser.error("--timeout is only valid with build or stage")
        if args.action == "launch":
            server_only_experiments = sorted(
                set(args.runtime_experiment or ()) - set(COMPATIBILITY_EXPERIMENTS)
            )
            if server_only_experiments:
                parser.error(
                    f"{', '.join(server_only_experiments)} is only valid with "
                    "launch-server or check"
                )
            for value, option in (
                (args.server_template, "--server-template"),
                (args.server_java, "--server-java"),
                (args.shutdown_timeout, "--shutdown-timeout"),
            ):
                if value is not None:
                    parser.error(f"{option} is only valid with launch-server or check")
            if args.side is not None:
                parser.error("--side is only valid with check")
            if args.accept_minecraft_eula:
                parser.error(
                    "--accept-minecraft-eula is only valid with automatic "
                    "launch-server or check materialization"
                )
        else:
            if args.server_template is None and not args.accept_minecraft_eula:
                parser.error(
                    f"{args.action} automatic server materialization requires "
                    "--accept-minecraft-eula"
                )
            if args.server_template is not None and args.accept_minecraft_eula:
                parser.error(
                    "--accept-minecraft-eula cannot be used with --server-template"
                )
            if args.action == "check":
                if args.side != "server":
                    parser.error("check requires --side server")
            elif args.side is not None:
                parser.error("--side is only valid with check")
            for value, option in (
                (args.launcher, "--launcher"),
                (args.launcher_executable, "--launcher-executable"),
                (args.launcher_root, "--launcher-root"),
                (args.launcher_profile, "--launcher-profile"),
                (args.offline_name, "--offline-name"),
                (args.launcher_java, "--launcher-java"),
                (args.launcher_java_state, "--launcher-java-state"),
            ):
                if value is not None:
                    parser.error(f"{option} is only valid with launch")
    else:
        if args.pack is None:
            parser.error(f"{args.action} requires --pack")
        if args.run is not None:
            parser.error(
                "--run is only valid with run, launch, launch-server, or check"
            )
        for value, option in (
            (args.launcher, "--launcher"),
            (args.launcher_executable, "--launcher-executable"),
            (args.launcher_root, "--launcher-root"),
            (args.launcher_profile, "--launcher-profile"),
            (args.offline_name, "--offline-name"),
            (args.launcher_java, "--launcher-java"),
            (args.launcher_java_state, "--launcher-java-state"),
        ):
            if value is not None:
                parser.error(f"{option} is only valid with launch")
        for value, option in (
            (args.memory, "--memory"),
            (args.launch_timeout, "--launch-timeout"),
            (args.runtime_experiment, "--runtime-experiment"),
        ):
            if value is not None:
                parser.error(
                    f"{option} is only valid with launch, launch-server, or check"
                )
        for value, option in (
            (args.server_template, "--server-template"),
            (args.server_java, "--server-java"),
            (args.shutdown_timeout, "--shutdown-timeout"),
        ):
            if value is not None:
                parser.error(f"{option} is only valid with launch-server or check")
        if args.side is not None:
            parser.error("--side is only valid with check")
        if args.accept_minecraft_eula:
            parser.error(
                "--accept-minecraft-eula is only valid with automatic "
                "launch-server or check materialization"
            )
        if args.action == "show" and args.timeout is not None:
            parser.error("--timeout is only valid with build or stage")
    try:
        if args.action == "run":
            side = "auto" if args.side is None else args.side
            run_id = args.run
            if run_id is None:
                # Planning once without a client stage reveals the exact
                # Packwiz side metadata without materializing anything.  Only
                # a selected client side justifies a second, stage-bound plan.
                plan = plan_susy_mod_dev(
                    ROOT,
                    args.project or Path.cwd(),
                    args.pack,
                    pack_mod=args.pack_mod,
                    java_home=args.java_home,
                    stage_client=False,
                )
                if plan["state"] != "ready":
                    if args.json:
                        print(json.dumps(plan, indent=2, sort_keys=True))
                    else:
                        sys.stdout.write(render_susy_mod_plan(plan))
                    return 1
                applicable = set(plan["replacement"]["applicable_sides"])
                requested = (
                    applicable
                    if side == "auto"
                    else ({"client", "server"} if side == "both" else {side})
                )
                unavailable = sorted(requested - applicable)
                if unavailable:
                    parser.error(
                        "requested run side is not applicable to the exact "
                        "Packwiz entry: " + ", ".join(unavailable)
                    )
                if "server" not in requested:
                    for value, option in server_options:
                        if value is not None:
                            parser.error(
                                f"{option} is not valid for the Packwiz-derived "
                                "client-only run"
                            )
                    if args.accept_minecraft_eula:
                        parser.error(
                            "--accept-minecraft-eula is not valid for the "
                            "Packwiz-derived client-only run"
                        )
                    server_only_experiments = sorted(
                        set(args.runtime_experiment or ())
                        - set(COMPATIBILITY_EXPERIMENTS)
                    )
                    if server_only_experiments:
                        parser.error(
                            f"{', '.join(server_only_experiments)} is not valid "
                            "for the Packwiz-derived client-only run"
                        )
                if "client" not in requested:
                    for value, option in client_options:
                        if value is not None:
                            parser.error(
                                f"{option} is not valid for the Packwiz-derived "
                                "server-only run"
                            )
                if "server" in requested and args.server_template is None and not (
                    args.accept_minecraft_eula
                ):
                    parser.error(
                        "run selected the server side and requires exactly one of "
                        "--server-template or --accept-minecraft-eula"
                    )
                if requested == {"server"} and args.server_template is None:
                    parser.error(
                        "a fresh server-only run requires --server-template; "
                        "managed Packwiz server materialization requires a "
                        "retained client stage, so use --side both with "
                        "--accept-minecraft-eula when the Packwiz entry admits both"
                    )
                stage_client = "client" in requested
                if stage_client:
                    plan = plan_susy_mod_dev(
                        ROOT,
                        args.project or Path.cwd(),
                        args.pack,
                        pack_mod=args.pack_mod,
                        java_home=args.java_home,
                        stage_client=True,
                    )
                if plan["state"] != "ready":
                    if args.json:
                        print(json.dumps(plan, indent=2, sort_keys=True))
                    else:
                        sys.stdout.write(render_susy_mod_plan(plan))
                    return 1
                if stage_client and set(
                    plan["replacement"]["applicable_sides"]
                ) != applicable:
                    parser.error(
                        "exact Packwiz side metadata changed while preparing "
                        "the client-stage plan; review a fresh run"
                    )
                build = execute_susy_mod_build(
                    ROOT,
                    plan,
                    timeout_seconds=(1200 if args.timeout is None else args.timeout),
                    stage_client=stage_client,
                )
                if build["outcome"] != "passed":
                    if args.json:
                        print(json.dumps(build, indent=2, sort_keys=True))
                    else:
                        sys.stdout.write(render_susy_mod_result(build))
                    return 1
                run_id = str(build["run_id"])
            selected_experiments = tuple(args.runtime_experiment or ())
            result = run_susy_mod(
                ROOT,
                run_id,
                side=side,
                launcher=("prism" if args.launcher is None else args.launcher),
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                client_compatibility_experiments=tuple(
                    experiment
                    for experiment in selected_experiments
                    if experiment in COMPATIBILITY_EXPERIMENTS
                ),
                server_template=args.server_template,
                server_java=args.server_java,
                accept_minecraft_eula=args.accept_minecraft_eula,
                server_compatibility_experiments=selected_experiments,
                memory_mib=(8192 if args.memory is None else args.memory),
                offline_name=(
                    "Workbench" if args.offline_name is None else args.offline_name
                ),
                client_timeout_seconds=(
                    600.0 if args.launch_timeout is None else args.launch_timeout
                ),
                server_timeout_seconds=(
                    600.0 if args.launch_timeout is None else args.launch_timeout
                ),
                shutdown_timeout_seconds=(
                    180.0
                    if args.shutdown_timeout is None
                    else args.shutdown_timeout
                ),
            )
            sys.stdout.write(render_susy_mod_run(result, json_output=args.json))
            return 0 if result["outcome"] == "passed" else 1
        if args.action == "launch":
            result = launch_susy_mod_client(
                ROOT,
                args.run,
                launcher=("prism" if args.launcher is None else args.launcher),
                launcher_executable=args.launcher_executable,
                launcher_root=args.launcher_root,
                launcher_profile=args.launcher_profile,
                launcher_java=args.launcher_java,
                launcher_java_state=args.launcher_java_state,
                compatibility_experiments=tuple(args.runtime_experiment or ()),
                memory_mib=(8192 if args.memory is None else args.memory),
                offline_name=(
                    "Workbench" if args.offline_name is None else args.offline_name
                ),
                timeout_seconds=(
                    600.0 if args.launch_timeout is None else args.launch_timeout
                ),
            )
            sys.stdout.write(
                render_susy_mod_launch(result, json_output=args.json)
            )
            return 0 if result["outcome"] == "passed" else 1
        if args.action == "launch-server":
            result = launch_susy_mod_server(
                ROOT,
                args.run,
                server_template=args.server_template,
                accept_minecraft_eula=args.accept_minecraft_eula,
                server_java=args.server_java,
                compatibility_experiments=tuple(args.runtime_experiment or ()),
                memory_mib=(8192 if args.memory is None else args.memory),
                timeout_seconds=(
                    600.0 if args.launch_timeout is None else args.launch_timeout
                ),
                shutdown_timeout_seconds=(
                    180.0
                    if args.shutdown_timeout is None
                    else args.shutdown_timeout
                ),
            )
            sys.stdout.write(render_susy_mod_server(result, json_output=args.json))
            return 0 if result["outcome"] == "passed" else 1
        if args.action == "check":
            result = check_susy_mod_server(
                ROOT,
                args.run,
                server_template=args.server_template,
                accept_minecraft_eula=args.accept_minecraft_eula,
                server_java=args.server_java,
                compatibility_experiments=tuple(args.runtime_experiment or ()),
                memory_mib=(8192 if args.memory is None else args.memory),
                timeout_seconds=(
                    600.0 if args.launch_timeout is None else args.launch_timeout
                ),
                shutdown_timeout_seconds=(
                    180.0
                    if args.shutdown_timeout is None
                    else args.shutdown_timeout
                ),
            )
            sys.stdout.write(render_susy_mod_check(result, json_output=args.json))
            return 0 if result["verdict"] in {
                "no-observed-regression",
                "candidate-improvement",
            } else 1
        plan = plan_susy_mod_dev(
            ROOT,
            args.project or Path.cwd(),
            args.pack,
            pack_mod=args.pack_mod,
            java_home=args.java_home,
            stage_client=args.action == "stage",
        )
        if args.action == "show":
            if args.json:
                print(json.dumps(plan, indent=2, sort_keys=True))
            else:
                sys.stdout.write(render_susy_mod_plan(plan))
            return 0 if plan["state"] == "ready" else 1
        if plan["state"] != "ready":
            if args.json:
                print(json.dumps(plan, indent=2, sort_keys=True))
            else:
                sys.stdout.write(render_susy_mod_plan(plan))
            return 1
        result = execute_susy_mod_build(
            ROOT,
            plan,
            timeout_seconds=(1200 if args.timeout is None else args.timeout),
            stage_client=args.action == "stage",
        )
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            sys.stdout.write(render_susy_mod_result(result))
        return 0 if result["outcome"] == "passed" else 1
    except (
        OSError,
        ValueError,
        SusyModDevError,
        SusyModCheckError,
        SusyModLaunchError,
        SusyModServerError,
        SusyModRunError,
        SusyServerMaterializationError,
    ) as exc:
        print(f"Workbench dev failed: {exc}", file=sys.stderr)
        return 2
