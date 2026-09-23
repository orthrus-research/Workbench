#!/usr/bin/env python3

"""Capture, validate, package, shard, and bind one external Strata observation."""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "modules/crucible/src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from workbench_crucible_strata_observation import (  # noqa: E402
    StrataObservationValidationError,
    build_strata_observation_receipt,
    write_strata_observation_receipt,
)
from workbench_crucible_strata_micro_region import (  # noqa: E402
    build_strata_micro_region_receipt,
    write_strata_micro_region_receipt,
)


LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


def log(message: str) -> None:
    print(f"[workbench-strata] {message}", flush=True)


def require_under(path: Path, root: Path, context: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise StrataObservationValidationError(
            f"{context} must be inside {root.resolve()}: {resolved}"
        ) from exc
    return resolved


def discover_server_jar(runtime: Path, configured: Path | None) -> Path:
    if configured:
        candidate = configured.expanduser()
        if not candidate.is_absolute():
            candidate = runtime / candidate
        return candidate.resolve(strict=True)
    candidates = sorted(runtime.glob("cleanroom-*.jar"))
    if len(candidates) != 1:
        raise StrataObservationValidationError(
            "expected one Cleanroom server jar or an explicit --server-jar; "
            f"found {[path.name for path in candidates]}"
        )
    return candidates[0].resolve(strict=True)


def normalize_executable(command: str, invocation_cwd: Path) -> str:
    """Keep a path-like executable stable when an external tool changes cwd."""

    if os.sep not in command and (os.altsep is None or os.altsep not in command):
        return command
    candidate = Path(command).expanduser()
    if not candidate.is_absolute():
        candidate = invocation_cwd / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise StrataObservationValidationError(
            f"executable path is not a file: {resolved}"
        )
    if not os.access(resolved, os.X_OK):
        raise StrataObservationValidationError(
            f"executable path is not executable: {resolved}"
        )
    return str(resolved)


def java_home_from_command(command: str) -> Path | None:
    candidate = Path(command)
    if not candidate.is_absolute() or candidate.name != "java":
        return None
    if candidate.parent.name != "bin":
        return None
    java_home = candidate.parent.parent
    return java_home if (java_home / "release").is_file() else None


def discover_chromium() -> str | None:
    configured = os.environ.get("STRATA_CHROMIUM_EXECUTABLE")
    if configured:
        candidate = Path(configured).expanduser().absolute()
        if not candidate.is_file():
            raise StrataObservationValidationError(
                f"configured Chromium executable does not exist: {candidate}"
            )
        return str(candidate)
    for command in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(command)
        if found:
            return str(Path(found).absolute())
    return None


def is_progress_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith(
        (
            "[dense-capture]",
            "[worldgen-observer]",
            "[worldgen-scan]",
            "PASS ",
            "warning:",
            "BUILD SUCCESSFUL",
            "sharded smoke ok:",
            "timing:",
            "stats:",
        )
    )


def run_logged(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str] | None = None,
    progress_only: bool = False,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"running {' '.join(command)}")
    with log_path.open("a", encoding="utf-8") as output:
        output.write(f"$ {' '.join(command)}\n")
        output.flush()
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        recent_lines: deque[str] = deque(maxlen=24)
        for line in process.stdout:
            recent_lines.append(line)
            if not progress_only or is_progress_line(line):
                print(line, end="")
            output.write(line)
        return_code = process.wait()
        output.write(f"exit_code={return_code}\n")
    if return_code:
        if progress_only:
            print("[workbench-strata] external command tail:", file=sys.stderr)
            print("".join(recent_lines), end="", file=sys.stderr)
        raise StrataObservationValidationError(
            f"external command failed with exit {return_code}: {' '.join(command)}"
        )


def manifest_url_path(manifest: Path) -> str:
    return "/@fs/" + quote(str(manifest.resolve(strict=True)), safe="/")


def write_viewer_handoff(
    path: Path, strata_root: Path, manifest: Path, port: int
) -> dict[str, str | int]:
    url_path = manifest_url_path(manifest)
    handoff: dict[str, str | int] = {
        "cwd": str(strata_root / "tools/render-explorer"),
        "externalArtifactRoot": str(manifest.parent),
        "manifest": str(manifest),
        "port": port,
        "url": f"http://127.0.0.1:{port}/?view=region&manifest={quote(url_path, safe='')}",
    }
    path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return handoff


def default_label() -> str:
    return time.strftime("world-studio-strata-%Y%m%d-%H%M%S", time.gmtime())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strata-root",
        type=Path,
        default=Path(os.environ.get("WORKBENCH_STRATA_ROOT", ROOT.parent / "strata")),
    )
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--server-jar", type=Path)
    parser.add_argument(
        "--scan",
        type=Path,
        help="Reuse an existing exact dense scan under Workbench .workbench storage.",
    )
    parser.add_argument("--java-cmd", default="java")
    parser.add_argument("--observer-java-home")
    parser.add_argument("--observer-compiler-java-home")
    parser.add_argument("--dimension", type=int, default=0)
    parser.add_argument("--min-chunk-x", type=int, default=-2)
    parser.add_argument("--min-chunk-z", type=int, default=-2)
    parser.add_argument("--chunk-size-x", type=int, default=4)
    parser.add_argument("--chunk-size-z", type=int, default=4)
    parser.add_argument("--halo-chunks", type=int, default=1)
    parser.add_argument("--tile-size", type=int, default=2)
    parser.add_argument(
        "--sample-profile",
        choices=("custom", "micro-region"),
        default="custom",
        help="micro-region selects a measured 16x16-chunk, 4x4-shard observation.",
    )
    parser.add_argument("--manifest-version", type=int, choices=(1, 2))
    parser.add_argument("--heap", default="2048M")
    parser.add_argument("--startup-timeout", type=int, default=300)
    parser.add_argument("--scan-timeout", type=int, default=420)
    parser.add_argument("--stop-timeout", type=int, default=60)
    parser.add_argument("--label", default=default_label())
    parser.add_argument("--jvm-arg", action="append", default=[])
    parser.add_argument("--no-render-smoke", action="store_true")
    parser.add_argument("--viewer-port", type=int, default=5173)
    parser.add_argument("--output-root", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.sample_profile == "micro-region":
        args.chunk_size_x = 16
        args.chunk_size_z = 16
        args.tile_size = 4
    manifest_version = args.manifest_version or (
        2 if args.sample_profile == "micro-region" else 1
    )
    invocation_cwd = Path.cwd()
    if not LABEL_RE.fullmatch(args.label):
        raise StrataObservationValidationError(
            "--label must match [A-Za-z0-9][A-Za-z0-9_.-]{0,95}"
        )
    strata_root = args.strata_root.expanduser().resolve(strict=True)
    runtime = require_under(
        args.runtime,
        ROOT / ".workbench",
        "runtime",
    )
    server_jar = discover_server_jar(runtime, args.server_jar)
    java_cmd = normalize_executable(args.java_cmd, invocation_cwd)
    runtime_java_home = java_home_from_command(java_cmd)
    default_build_java_home = strata_root / ".tools/zulu21"
    build_java_home = Path(
        args.observer_java_home or default_build_java_home
    ).expanduser().resolve(strict=True)
    compiler_java_home = Path(
        args.observer_compiler_java_home
        or runtime_java_home
        or build_java_home
    ).expanduser().resolve(strict=True)
    output_root = require_under(
        args.output_root or ROOT / ".workbench/evidence/strata" / args.label,
        ROOT / ".workbench",
        "output root",
    )
    if output_root.exists():
        raise StrataObservationValidationError(
            f"output root already exists; choose a fresh label: {output_root}"
        )
    output_root.mkdir(parents=True)

    package_path = output_root / f"{args.label}.strataview"
    manifest_path = Path(str(package_path) + ".d") / "manifest.json"
    report_path = output_root / f"{args.label}.capture-report.json"
    renderer_build_log = output_root / "renderer-build.log"
    driver_log = output_root / "capture-driver.log"
    screenshot_path = output_root / f"{args.label}-sharded-smoke.png"
    overview_screenshot_path = output_root / f"{args.label}-micro-region-overview.png"

    run_logged(
        ["npm", "run", "build"],
        cwd=strata_root / "tools/render-explorer",
        log_path=renderer_build_log,
    )

    command = [
        "python3",
        "tools/capture_dense_chunk_package.py",
        "--runtime",
        str(runtime),
        "--server-jar",
        str(server_jar),
        "--java-cmd",
        java_cmd,
        "--dimension",
        str(args.dimension),
        "--min-chunk-x",
        str(args.min_chunk_x),
        "--min-chunk-z",
        str(args.min_chunk_z),
        "--chunk-size-x",
        str(args.chunk_size_x),
        "--chunk-size-z",
        str(args.chunk_size_z),
        "--halo-chunks",
        str(args.halo_chunks),
        "--tile-size",
        str(args.tile_size),
        "--manifest-version",
        str(manifest_version),
        "--heap",
        args.heap,
        "--startup-timeout",
        str(args.startup_timeout),
        "--scan-timeout",
        str(args.scan_timeout),
        "--stop-timeout",
        str(args.stop_timeout),
        "--label",
        args.label,
        "--out",
        str(package_path),
        "--report",
        str(report_path),
        "--shard",
    ]
    reused_scan: Path | None = None
    if args.scan:
        reused_scan = require_under(args.scan, ROOT / ".workbench", "scan")
        command.extend(["--scan", str(reused_scan)])
    command.extend(["--observer-java-home", str(build_java_home)])
    command.extend(
        ["--observer-compiler-java-home", str(compiler_java_home)]
    )
    for argument in args.jvm_arg:
        command.append(f"--jvm-arg={argument}")
    if args.no_render_smoke:
        command.append("--no-render-smoke")

    child_environment = os.environ.copy()
    if not args.no_render_smoke:
        child_environment["STRATA_SCREENSHOT_PATH"] = str(screenshot_path)
        child_environment["STRATA_OVERVIEW_SCREENSHOT_PATH"] = str(
            overview_screenshot_path
        )
        chromium = discover_chromium()
        if chromium:
            child_environment["STRATA_CHROMIUM_EXECUTABLE"] = chromium
    run_logged(
        command,
        cwd=strata_root,
        log_path=driver_log,
        env=child_environment,
        progress_only=True,
    )

    scan_path = reused_scan or (
        runtime / "strata-worldgen-observer" / f"{args.label}.json"
    )
    launch_label = scan_path.stem if reused_scan else args.label
    launch_log = runtime / "logs" / f"strata-scan-{launch_label}.log"
    if manifest_version == 2:
        if args.no_render_smoke:
            raise StrataObservationValidationError(
                "V2 micro-region receipts require renderer smoke and both screenshots"
            )
        if runtime_java_home is None:
            raise StrataObservationValidationError(
                "V2 micro-region receipts require an absolute --java-cmd under a JDK bin directory"
            )
        receipt = build_strata_micro_region_receipt(
            workbench_root=ROOT,
            strata_root=strata_root,
            runtime_root=runtime,
            server_jar=server_jar,
            runtime_java_home=runtime_java_home,
            build_java_home=build_java_home,
            compiler_java_home=compiler_java_home,
            scan_path=scan_path,
            package_path=package_path,
            manifest_path=manifest_path,
            report_path=report_path,
            launch_log_path=launch_log,
            capture_driver_log_path=driver_log,
            renderer_build_log_path=renderer_build_log,
            overview_screenshot_path=overview_screenshot_path,
            exact_tile_screenshot_path=screenshot_path,
        )
        receipt_path = output_root / "strata-micro-region-receipt-v1.json"
        write_strata_micro_region_receipt(receipt_path, receipt)
    else:
        receipt = build_strata_observation_receipt(
            workbench_root=ROOT,
            strata_root=strata_root,
            runtime_root=runtime,
            server_jar=server_jar,
            scan_path=scan_path,
            package_path=package_path,
            manifest_path=manifest_path,
            report_path=report_path,
            launch_log_path=launch_log,
            capture_driver_log_path=driver_log,
            renderer_build_log_path=renderer_build_log,
            render_smoke=not args.no_render_smoke,
            screenshot_path=None if args.no_render_smoke else screenshot_path,
        )
        receipt_path = output_root / "strata-observation-receipt-v1.json"
        write_strata_observation_receipt(receipt_path, receipt)
    handoff = write_viewer_handoff(
        output_root / "viewer-handoff.json",
        strata_root,
        manifest_path,
        args.viewer_port,
    )

    log(f"receipt: {receipt_path}")
    log(f"manifest: {manifest_path}")
    if not args.no_render_smoke:
        log(f"screenshot: {screenshot_path}")
        if manifest_version == 2:
            log(f"micro-region overview: {overview_screenshot_path}")
    log(
        "viewer: STRATA_EXTERNAL_ARTIFACT_ROOT="
        f"{manifest_path.parent} npm run dev -- --port {args.viewer_port}"
    )
    log(f"open: {handoff['url']}")
    print(receipt["receipt_id"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, StrataObservationValidationError) as exc:
        print(f"Strata observation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
