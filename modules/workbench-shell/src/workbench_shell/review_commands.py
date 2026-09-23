"""Shell-owned review commands."""
from __future__ import annotations

import argparse
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryFile


from workbench_api.resources import repository_root

ROOT = repository_root(__file__)

_REVIEW_HELP = """\
usage: workbench review TARGET [options]

Prepare and review exact project changes without hiding network or mutation.

targets:
  pr NUMBER             prepare and review one provider-bound pull request
  prepare-pr NUMBER      prepare exact provider-bound PR refs
  recipes               compare bounded Groovy recipe changes locally

Start with `workbench review pr --help`, `workbench review prepare-pr --help`, or
`workbench review recipes --help` for target-specific options.
"""


_PR_REVIEW_HELP = """\
usage: workbench review pr NUMBER --profile supersymmetry --source CHECKOUT [options] [recipe-review options]

Review one GitHub pull request through the provider-bound preparation authority.
The command first observes a typed provider plan. Applying it re-observes the
provider, fetches exact base/head objects into immutable Workbench refs, then
reviews the retained historical base → head delta without checkout mutation.

options:
  --yes                 explicitly authorize the network/ref write after planning
  --apply PLAN_ID       require this freshly observed provider-bound plan identity
  --plan                print the reviewed plan only; never fetch or write refs
  --prepared-receipt PATH
                        review one already-prepared matching receipt offline
  --channel NAME        acquisition channel for provider-bound preparation
  --state-root PATH     retained receipt/ref state root
  --git-executable PATH exact native Git executable
  --network-timeout SEC bounded provider/fetch timeout (default: 300)

Without --yes, a TTY asks for a plain y/n confirmation. Noninteractive runs
print the reviewed plan and a --yes command instead of mutating refs. Existing
`workbench review prepare-pr` remains the advanced preparation route. Recipe
options such as --json, --full-json-v1, --strict, --strict-all,
and --verbose are forwarded after preparation. For this `review pr` route,
--strict is delta-scoped (introduced collision/incomplete-builder signals and
supplied runtime attention);
--strict-all also includes candidate-wide signals, source configuration warnings,
and observed Git hygiene findings. `review recipes --strict` keeps its existing
owner-report semantics.
"""


_RECIPE_REVIEW_HELP = """\
usage: workbench review recipes --profile supersymmetry (--baseline BASELINE | --baseline-ref REV | --pr-base REV | --prepared-receipt PATH) --source CANDIDATE [options]

Review bounded Supersymmetry Groovy machine-recipe changes. Compare two exact
directory trees with --baseline, or materialize --baseline-ref from the local
Git repository containing --source. Use --pr-base to materialize the merge base
of local target REV and candidate HEAD. The command does not fetch, compile,
launch, or mutate the candidate tree; target freshness remains unverified.
Use --prepared-receipt after `workbench review prepare-pr` to compare the exact
fetched PR head with its provider-recorded base, independent of the current checkout.

required:
  --profile supersymmetry
                         explicit pack adapter (the only bundled pack profile)
  --source PATH          candidate pack root or Groovy root

baseline (choose exactly one):
  --baseline PATH        exact baseline pack root or Groovy root
  --baseline-ref REV     local commit-ish baseline; never fetched
  --pr-base REV          merge base of local target REV and candidate HEAD
  --prepared-receipt PATH
                         exact receipt emitted by review prepare-pr; never fetched

common options:
  --side SIDE            dedicated-server (default), integrated-server, or client
  --json                 emit the bounded reviewer-oriented V2 report
  --full-json-v1         emit the complete source-linked owner V1 report
  --output PATH          also write the selected JSON report to a fresh path
  --strict               return exit 1 when report summary status is attention;
                         supplied runtime evidence can drive it, runConfig warnings do not
  --strict-all           also return exit 1 for source configuration warnings and
                         an observed Git hygiene finding; V1/V2 strict fields stay unchanged
  --verbose              include full program, lifecycle, and provenance detail

Advanced Pack Program Studio dev options are forwarded unchanged. Human output
conservatively pairs unambiguous one-to-one property-only recipe modifications;
the compact V2 record carries the same decision view without temporary paths.
Use --full-json-v1 when complete added/removed static candidates are required.
"""

_RECIPE_REVIEW_PATH_DISPLAY_LIMIT = 8
_RECIPE_REVIEW_V2_PATH_LIMIT = 2_000
_PR_REVIEW_HYGIENE_BYTES = 256 * 1024



def _review_option_values(arguments: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(arguments):
        if token == option:
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                raise ValueError(f"{option} requires one value")
            values.append(arguments[index + 1])
        elif token.startswith(option + "="):
            value = token.partition("=")[2]
            if not value:
                raise ValueError(f"{option} requires one value")
            values.append(value)
    return values

def _review_single_option(arguments: list[str], option: str) -> str | None:
    values = _review_option_values(arguments, option)
    if len(values) > 1:
        raise ValueError(f"{option} may be supplied only once")
    return values[0] if values else None

def _review_without_option(arguments: list[str], option: str) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token == option:
            index += 2
            continue
        if token.startswith(option + "="):
            index += 1
            continue
        result.append(token)
        index += 1
    return result

def _review_terminal_text(value: object) -> str:
    result: list[str] = []
    for character in str(value):
        number = ord(character)
        if character == "\x1b" or number < 32 or number == 127:
            result.append(f"\\x{number:02x}")
        else:
            result.append(character)
    return "".join(result)

def _recipe_git_source_layout(source: Path, layout: dict[str, object]) -> PurePosixPath:
    from workbench_pack_program_studio.profile import (
        native_filesystem_path,
        portable_relative_path,
    )

    requested = native_filesystem_path(source.expanduser())
    if not requested.exists():
        raise ValueError(
            f"--source does not exist: {source}; point --source at the candidate "
            "pack root or its Groovy root"
        )
    if not requested.is_dir():
        raise ValueError(
            f"--source is not a directory: {source}; point --source at the "
            "candidate pack root or its Groovy root"
        )
    run_config = portable_relative_path(
        layout["run_config"], "Groovy source layout run_config"
    )
    if requested.joinpath(*run_config.parts).is_file():
        return PurePosixPath(".")
    groovy_root = portable_relative_path(
        layout["groovy_root"], "Groovy source layout groovy_root"
    )
    if requested.joinpath(*groovy_root.parts, *run_config.parts).is_file():
        return groovy_root
    raise ValueError(
        "--source is neither a compatible pack root nor its Groovy root; "
        f"expected {run_config.as_posix()} or "
        f"{groovy_root.as_posix()}/{run_config.as_posix()}. "
        "Choose the directory containing one of those files"
    )

def _recipe_review_git_error_guidance(error: BaseException) -> str | None:
    """Return a bounded next step without weakening the local-only boundary."""

    detail = str(error).casefold()
    if "git is unavailable" in detail:
        return (
            "Next: install a compatible native Git for --baseline-ref/--pr-base, "
            "or use --baseline PATH for an explicit directory comparison."
        )
    if (
        "filename too long" in detail
        or "$git_dir too big" in detail
        or "supported git for windows path limit" in detail
    ):
        return (
            "Next: move the checkout to a shorter native path for "
            "--baseline-ref/--pr-base, or use --baseline PATH for an explicit "
            "directory comparison."
        )
    if "does not resolve to a local commit" in detail:
        return (
            "Next: inspect or refresh the requested ref outside Workbench, then "
            "rerun; Workbench never fetches."
        )
    if "no local merge base" in detail or "multiple merge bases" in detail:
        return (
            "Next: verify the local target and candidate history, or use "
            "--baseline-ref with an exact known commit."
        )
    if "selected git baseline path" in detail:
        return (
            "Next: choose a ref where the selected pack/Groovy layout exists, "
            "or correct --source."
        )
    if "repository" in detail or "worktree" in detail:
        return (
            "Next: place --source inside the intended Git checkout, or use "
            "--baseline PATH for an explicit directory comparison."
        )
    return None

def _recipe_review_excluded_paths(
    repository_paths: tuple[str, ...], selected_paths: tuple[str, ...]
) -> tuple[str, ...]:
    selected = set(selected_paths)
    return tuple(path for path in repository_paths if path not in selected)

def _recipe_review_bounded_scope(paths: tuple[str, ...]) -> dict[str, object]:
    shown = paths[:_RECIPE_REVIEW_V2_PATH_LIMIT]
    return {
        "paths": list(shown),
        "path_count": len(paths),
        "truncated": len(paths) > len(shown),
    }

def _recipe_review_committed_scope(
    repository_paths: tuple[str, ...],
    selected_paths: tuple[str, ...],
) -> dict[str, object]:
    return {
        "repository": _recipe_review_bounded_scope(repository_paths),
        "selected": _recipe_review_bounded_scope(selected_paths),
        "excluded": _recipe_review_bounded_scope(
            _recipe_review_excluded_paths(repository_paths, selected_paths)
        ),
    }

def _recipe_review_selected_paths(
    *,
    source: Path,
    repository_root: Path,
    selected_relative: PurePosixPath,
    repository_paths: tuple[str, ...],
) -> tuple[str, ...]:
    source_relative = source.resolve(strict=True).relative_to(
        repository_root.resolve(strict=True)
    )
    prefix_parts = [*PurePosixPath(source_relative.as_posix()).parts]
    if selected_relative.as_posix() != ".":
        prefix_parts.extend(selected_relative.parts)
    prefix = "/".join(part for part in prefix_parts if part != ".")
    if not prefix:
        return repository_paths
    boundary = prefix + "/"
    return tuple(
        path
        for path in repository_paths
        if path == prefix or path.startswith(boundary)
    )

def _recipe_review_path_rows(
    label: str,
    paths: tuple[str, ...] | None,
) -> list[str]:
    prefix = f"  {label:<17}"
    if paths is None:
        return [prefix + "unavailable"]
    if not paths:
        return [prefix + "none"]
    shown = paths[:_RECIPE_REVIEW_PATH_DISPLAY_LIMIT]
    rows = [prefix + _review_terminal_text(shown[0])]
    rows.extend(" " * len(prefix) + _review_terminal_text(path) for path in shown[1:])
    omitted = len(paths) - len(shown)
    if omitted:
        rows.append(" " * len(prefix) + f"… {omitted:,} more")
    return rows

def _recipe_review_dirty_scope_rows(
    materialized: object,
    *,
    pr_mode: bool,
) -> list[str]:
    repository_dirty_paths = tuple(
        getattr(materialized, "repository_dirty_paths", ())
    )
    selected_dirty_paths = tuple(
        getattr(materialized, "selected_dirty_paths", ())
    )
    excluded_dirty_paths = _recipe_review_excluded_paths(
        repository_dirty_paths,
        selected_dirty_paths,
    )
    if not repository_dirty_paths:
        return ["  Dirty scope      Git status observed clean before analysis"]
    dirty_boundary = (
        "committed PR counts" if pr_mode else "the exact baseline"
    )
    return [
        (
            "  Dirty scope      "
            f"{len(selected_dirty_paths):,} selected / "
            f"{len(repository_dirty_paths):,} total working-tree paths"
        ),
        (
            f"                   not included in {dirty_boundary}; "
            "selected dirty paths affect comparison scope"
        ),
        (
            "                   analysis reads the resulting candidate tree; "
            "a source/deleted path does not imply candidate bytes were read"
        ),
        *_recipe_review_path_rows("Dirty selected", selected_dirty_paths),
        *_recipe_review_path_rows("Dirty excluded", excluded_dirty_paths),
    ]

def _render_recipe_git_selection(materialized: object) -> str:
    candidate = materialized.candidate_git_binding
    rows = [
        "",
        "Git scope and provenance",
        (
            f"  Selection        "
            f"{'PR merge-base' if materialized.selection_kind == 'merge-base' else 'exact baseline'} "
            f"· {materialized.commit_oid}"
        ),
        f"  Repository       {_review_terminal_text(materialized.repository_root)}",
        (
            "  Candidate        working tree at "
            f"{candidate['revision']} · {'dirty' if candidate['dirty'] else 'clean'}"
        ),
    ]
    if materialized.selection_kind == "merge-base":
        repository_paths_value = getattr(materialized, "repository_changed_paths", None)
        selected_paths_value = getattr(materialized, "selected_changed_paths", None)
        repository_paths = (
            None
            if repository_paths_value is None
            else tuple(repository_paths_value)
        )
        selected_paths = (
            None if selected_paths_value is None else tuple(selected_paths_value)
        )
        excluded_paths = (
            None
            if repository_paths is None or selected_paths is None
            else _recipe_review_excluded_paths(repository_paths, selected_paths)
        )
        rows.extend(
            [
                f"  Requested target {_review_terminal_text(materialized.requested_ref)}",
                f"  Target tip       {materialized.target_tip_oid}",
                f"  Merge base       {materialized.commit_oid}",
                (
                    "  PR scope         "
                    f"{materialized.selected_changed_file_count:,} selected / "
                    f"{materialized.repository_changed_file_count:,} total committed files"
                ),
                *_recipe_review_path_rows("Selected files", selected_paths),
                *_recipe_review_path_rows("Excluded files", excluded_paths),
                *_recipe_review_dirty_scope_rows(materialized, pr_mode=True),
            ]
        )
        rows.append(
            "  Target freshness unverified · local ref only; no fetch performed"
        )
    else:
        rows.extend(
            [
                f"  Requested base   {_review_terminal_text(materialized.requested_ref)}",
                f"  Resolved commit  {materialized.commit_oid}",
                *_recipe_review_dirty_scope_rows(materialized, pr_mode=False),
            ]
        )
    rows.extend(
        [
            f"  Commit tree      {materialized.tree_oid}",
            f"  Selected root    {_review_terminal_text(materialized.repository_relative_path)}",
            (
                f"  Materialized     {materialized.file_count:,} raw Git blobs · "
                f"{materialized.total_bytes:,} bytes · temporary"
            ),
            "  Boundary         no fetch, checkout filters, hooks, LFS hydration, symlinks, or submodules",
            "",
        ]
    )
    return "\n".join(rows)

def _render_prepared_recipe_selection(receipt: dict[str, object]) -> str:
    if receipt["pull_request_merged"]:
        state = "merged · reviewing provider-recorded base → head PR delta"
    elif receipt["pull_request_state"] == "open":
        state = "open · reviewing provider-recorded base → head PR delta"
    else:
        state = "closed without merge · reviewing retained provider delta"
    provider_merge = receipt["provider_merge_oid"] or "not supplied"
    return "\n".join(
        [
            "",
            "Git scope and provenance",
            (
                f"  Selection        provider-bound {receipt['project_id']} "
                f"pull request #{receipt['pull_request']}"
            ),
            f"  Pull request     {_review_terminal_text(receipt['pull_request_url'])}",
            f"  Provider state   {state}",
            (
                f"  Original base    {receipt['base_repository']}:{receipt['base_name']} "
                f"at {receipt['base_oid']}"
            ),
            (
                f"  Original head    {receipt['head_repository']}:{receipt['head_name']} "
                f"at {receipt['head_oid']}"
            ),
            f"  Provider merge   {provider_merge}",
            "  Historical delta provider-recorded base → provider-recorded head",
            f"  Prepared         {_review_terminal_text(receipt['prepared_at'])}",
            f"  Receipt          {receipt['receipt_id']}",
            (
                "  Boundary         provider response was bound at preparation; "
                "this offline review performed no network access"
            ),
            "",
        ]
    )

def _compose_recipe_git_review(rendered: str, selection: str) -> str:
    """Place Git scope after the decision summary and before detailed findings."""

    summary, separator, detail = rendered.partition("\n\n")
    if not separator:
        return rendered + selection
    return summary + "\n" + selection + "\n" + detail

def _recipe_review_flag(arguments: list[str], option: str) -> bool:
    count = arguments.count(option)
    if any(token.startswith(option + "=") for token in arguments):
        raise ValueError(f"{option} does not take a value")
    if count > 1:
        raise ValueError(f"{option} may be supplied only once")
    return count == 1

def _recipe_review_without_flag(arguments: list[str], option: str) -> list[str]:
    return [token for token in arguments if token != option]

def _write_recipe_review_report(path: Path, report: dict[str, object]) -> Path:
    requested = path.expanduser()
    if requested.is_symlink():
        raise ValueError(f"Recipe Review output cannot be a symlink: {requested}")
    destination = requested.resolve()
    if destination.exists():
        raise ValueError(
            "Recipe Review output already exists; choose a fresh path: "
            f"{destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
    ) + "\n"
    temporary_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_name, destination)
        except FileExistsError as exc:
            raise ValueError(
                "Recipe Review output already exists; choose a fresh path: "
                f"{destination}"
            ) from exc
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return destination

def _pr_review_hygiene(
    repository: Path,
    base_ref: str,
    head_ref: str,
) -> tuple[str, str]:
    """Run one bounded, filter-free `git diff --check` observation."""

    from workbench_project_intelligence.git_observation import (
        GitObservationError,
        observation_environment,
        safe_git_prefix,
    )

    try:
        prefix = safe_git_prefix(repository, timeout=10)
        with TemporaryFile() as stdout, TemporaryFile() as stderr:
            completed = subprocess.run(
                [
                    *prefix,
                    "-C",
                    str(repository),
                    "diff",
                    "--check",
                    "--no-ext-diff",
                    "--no-textconv",
                    base_ref,
                    head_ref,
                    "--",
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=60,
                env=observation_environment(),
            )
            stdout.seek(0, os.SEEK_END)
            stderr.seek(0, os.SEEK_END)
            size = stdout.tell() + stderr.tell()
            if size > _PR_REVIEW_HYGIENE_BYTES:
                return "unavailable", "diff --check output exceeded its byte bound"
            stdout.seek(0)
            stderr.seek(0)
            stdout_detail = stdout.read().decode("utf-8", "replace").strip()
            stderr_detail = stderr.read().decode("utf-8", "replace").strip()
            detail = "\n".join(
                value for value in (stdout_detail, stderr_detail) if value
            )
    except (GitObservationError, OSError, subprocess.TimeoutExpired) as exc:
        return "unavailable", f"diff --check could not be observed: {exc}"
    if completed.returncode == 0:
        return "clean", "no whitespace or conflict-marker errors reported"
    # Git versions differ on whether diff --check findings exit 1 or 2. A
    # bounded diagnostic on stdout is the evidence of a review finding; an
    # otherwise-empty fatal invocation remains unavailable instead.
    if stdout_detail:
        return "attention", detail or "Git reported diff --check findings"
    return "unavailable", detail or f"Git diff --check exited {completed.returncode}"

def _render_pr_review_hygiene(state: str, detail: str) -> str:
    if state == "clean":
        outcome = "clean · no diff --check findings"
    elif state == "attention":
        outcome = "attention · findings make --strict-all exit 1"
    else:
        outcome = "unavailable · does not change exit status"
    rows = ["", "Git hygiene", f"  diff --check    {outcome}"]
    if detail and state != "clean":
        rows.append(f"  Detail          {_review_terminal_text(detail[:4_096])}")
    return "\n".join(rows) + "\n"

def _incomplete_recipe_keys(program: object) -> set[str]:
    if not isinstance(program, dict):
        return set()
    result: set[str] = set()
    for effect in program.get("effects", []):
        if not isinstance(effect, dict) or effect.get("kind") != "machine-recipe":
            continue
        recipe = effect.get("recipe")
        if not isinstance(recipe, dict) or recipe.get("complete") is not False:
            continue
        key = effect.get("semantic_key")
        if isinstance(key, str):
            result.add(key)
    return result

def _collision_keys(program: object) -> set[tuple[str, str]]:
    if not isinstance(program, dict):
        return set()
    result: set[tuple[str, str]] = set()
    for collision in program.get("collisions", []):
        if not isinstance(collision, dict):
            continue
        policy = collision.get("policy_id")
        value = collision.get("value")
        if isinstance(policy, str) and value is not None:
            result.add((policy, json.dumps(value, ensure_ascii=False, sort_keys=True)))
    return result

def _pr_attention_partition(report: dict[str, object]) -> dict[str, int]:
    """Separate candidate signals introduced by this PR from retained signals."""

    baseline = report.get("baseline")
    candidate = report.get("candidate")
    baseline_collisions = _collision_keys(baseline)
    candidate_collisions = _collision_keys(candidate)
    baseline_incomplete = _incomplete_recipe_keys(baseline)
    candidate_incomplete = _incomplete_recipe_keys(candidate)
    introduced = (candidate_collisions - baseline_collisions) | {
        ("incomplete", key) for key in candidate_incomplete - baseline_incomplete
    }
    preexisting = (candidate_collisions & baseline_collisions) | {
        ("incomplete", key) for key in candidate_incomplete & baseline_incomplete
    }
    return {
        "introduced": len(introduced),
        "preexisting": len(preexisting),
        "candidate_total": len(candidate_collisions) + len(candidate_incomplete),
    }

def _render_pr_attention_partition(
    partition: dict[str, int], *, runtime_attention: bool
) -> str:
    introduced = partition["introduced"]
    preexisting = partition["preexisting"]
    strict_attention = bool(introduced or runtime_attention)
    return "\n".join(
        [
            "",
            "PR/delta attention",
            (
                f"  Introduced     {introduced} collision/incomplete-builder "
                "signal(s) relative to the retained baseline"
            ),
            (
                "  --strict       "
                + (
                    "exits 1 for introduced PR signals or supplied runtime attention"
                    if strict_attention
                    else "does not change exit status"
                )
            ),
            (
                "  Runtime        "
                + (
                    "supplied attention also makes --strict exit 1"
                    if runtime_attention
                    else "no supplied runtime attention"
                )
            ),
            "",
            "Baseline/candidate-wide attention",
            (
                f"  Pre-existing   {preexisting} retained baseline/candidate "
                "signal(s)"
            ),
            (
                f"  Candidate      {partition['candidate_total']} total "
                "collision/incomplete-builder signal(s)"
            ),
            "  --strict-all   includes these candidate-wide signals, source configuration warnings, and observed Git hygiene findings.",
            "",
        ]
    )

def _supplied_runtime_attention(report: dict[str, object]) -> bool:
    runtime = report.get("runtime_evidence")
    return isinstance(runtime, dict) and runtime.get("state") == "attention"

def _pr_review_selection_context(
    selection: dict[str, object],
    report: dict[str, object],
    *,
    git_hygiene: dict[str, str] | None,
) -> dict[str, object]:
    """Add bounded reviewer context to the open-ended V2 selection record."""

    partition = _pr_attention_partition(report)
    runtime_attention = _supplied_runtime_attention(report)
    enriched = dict(selection)
    enriched["attention_scope"] = {
        "introduced_static_signals": partition["introduced"],
        "preexisting_static_signals": partition["preexisting"],
        "candidate_static_signal_total": partition["candidate_total"],
        "supplied_runtime_attention": runtime_attention,
        "pr_strict": (
            "introduced static signals and supplied runtime attention"
        ),
        "strict_all": (
            "candidate-wide static signals, supplied runtime attention, "
            "source configuration warnings, and Git hygiene attention"
        ),
    }
    hygiene = git_hygiene or {
        "state": "unavailable",
        "detail": "Git hygiene was not observed for this review route",
    }
    enriched["git_hygiene"] = {
        "state": hygiene["state"],
        "detail": _review_terminal_text(hygiene["detail"][:4_096]),
    }
    return enriched

def _relabel_pr_review_attention(rendered: str) -> str:
    """Keep the legacy owner renderer truthful when PR strictness is narrower."""

    return rendered.replace(
        "These report-level signals drive --strict and are not necessarily "
        "introduced by this recipe diff.",
        "Candidate-wide report signals; they do not drive PR --strict. "
        "--strict-all includes them.",
    ).replace(
        "Informational in V1; these warnings do not drive --strict.",
        "Informational in V1; they do not drive PR --strict but do drive "
        "--strict-all.",
    )

def _run_recipe_owner_review(
    arguments: list[str],
    *,
    selection: dict[str, object],
    human_selection: str | None = None,
    candidate_git_binding: dict[str, object] | None = None,
    baseline_git_binding: dict[str, object] | None = None,
    strict_all_extra_attention: bool = False,
    pr_delta_strict: bool = False,
    pr_attention_partition: bool = False,
    git_hygiene: dict[str, str] | None = None,
) -> int:
    """Run the V1 owner once and publish either its full record or compact V2."""

    from workbench_pack_program_studio.cli import run as groovy_run
    from workbench_pack_program_studio.review import build_recipe_review_v2

    json_output = _recipe_review_flag(arguments, "--json")
    full_v1 = _recipe_review_flag(arguments, "--full-json-v1")
    strict_all = _recipe_review_flag(arguments, "--strict-all")
    owner_arguments = _recipe_review_without_flag(arguments, "--pr-delta-strict")
    owner_arguments = _recipe_review_without_flag(
        owner_arguments, "--pr-review-attention"
    )
    if json_output and full_v1:
        raise ValueError("--json and --full-json-v1 are mutually exclusive")
    output_value = _review_single_option(arguments, "--output")
    owner_arguments = _recipe_review_without_flag(owner_arguments, "--full-json-v1")
    owner_arguments = _recipe_review_without_flag(owner_arguments, "--strict-all")

    if full_v1:
        owner_arguments.append("--json")
        # Full V1 can be hundreds of MiB. The owner streams the unchanged
        # record directly so the router does not retain a second StringIO copy.
        captured: list[dict[str, object]] = []
        code = groovy_run(
            ["dev", "--recipe-review", *owner_arguments],
            root=ROOT,
            output=sys.stdout,
            error=sys.stderr,
            candidate_git_binding=candidate_git_binding,
            baseline_git_binding=baseline_git_binding,
            result_callback=captured.append,
        )
        return _recipe_review_strict_all_exit(
            code,
            captured[0] if captured else None,
            strict_all=strict_all,
            extra_attention=strict_all_extra_attention,
            pr_delta_strict=pr_delta_strict,
        )

    output = StringIO()
    error = StringIO()
    owner_arguments = _recipe_review_without_flag(owner_arguments, "--json")
    owner_arguments = _review_without_option(owner_arguments, "--output")
    captured: list[dict[str, object]] = []
    code = groovy_run(
        ["dev", "--recipe-review", *owner_arguments],
        root=ROOT,
        output=output,
        error=error,
        candidate_git_binding=candidate_git_binding,
        baseline_git_binding=baseline_git_binding,
        result_callback=captured.append,
    )
    if error.getvalue():
        sys.stderr.write(error.getvalue())
    if not captured:
        if output.getvalue():
            sys.stdout.write(output.getvalue())
        return code

    compact_selection = (
        _pr_review_selection_context(
            selection,
            captured[0],
            git_hygiene=git_hygiene,
        )
        if pr_attention_partition
        else selection
    )
    compact = build_recipe_review_v2(captured[0], selection=compact_selection)
    written = None
    if output_value is not None:
        written = _write_recipe_review_report(Path(output_value), compact)
    if json_output:
        sys.stdout.write(
            json.dumps(
                compact,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
            + "\n"
        )
    else:
        rendered = output.getvalue()
        if human_selection is not None:
            rendered = _compose_recipe_git_review(rendered, human_selection)
        if pr_attention_partition:
            rendered = _relabel_pr_review_attention(rendered)
            rendered += _render_pr_attention_partition(
                _pr_attention_partition(captured[0]),
                runtime_attention=_supplied_runtime_attention(captured[0]),
            )
        sys.stdout.write(rendered)
        if written is not None:
            sys.stdout.write(f"Report written: {written}\n")
    return _recipe_review_strict_all_exit(
        code,
        captured[0],
        strict_all=strict_all,
        extra_attention=strict_all_extra_attention,
        pr_delta_strict=pr_delta_strict,
    )

def _recipe_review_strict_all_exit(
    code: int,
    report: dict[str, object] | None,
    *,
    strict_all: bool,
    extra_attention: bool,
    pr_delta_strict: bool,
) -> int:
    """Extend CLI-only strictness without rewriting owner V1/V2 identity."""

    if code:
        return code
    if report is None:
        return code
    if pr_delta_strict and (
        _pr_attention_partition(report)["introduced"]
        or _supplied_runtime_attention(report)
    ):
        return 1
    if not strict_all:
        return code
    candidate = report.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    run_config = candidate.get("run_config")
    run_config = run_config if isinstance(run_config, dict) else {}
    warnings = run_config.get("warnings")
    has_configuration_warning = isinstance(warnings, list) and bool(warnings)
    summary = report.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    has_report_attention = summary.get("status") == "attention"
    return 1 if has_report_attention or has_configuration_warning or extra_attention else 0

def _render_pr_review_plan(plan: dict[str, object]) -> str:
    merged = bool(plan.get("pull_request_merged"))
    state = "merged" if merged else str(plan.get("pull_request_state", "unknown"))
    return "\n".join(
        [
            f"PR review: #{plan['pull_request']} · {state}",
            "Decision   provider-bound plan is ready; no refs were changed.",
            (
                "Delta      original provider-recorded base → head · "
                f"{plan['base_oid']} → {plan['head_oid']}"
            ),
            f"Pull request {plan['pull_request_url']}",
            f"Plan       {plan['plan_id']}",
            (
                "Next       rerun with --yes to authorize the network/ref write, "
                "or --apply PLAN_ID for a reviewed-plan apply."
            ),
            "",
        ]
    )

def _review_pr_main(argv: list[str]) -> int:
    from workbench_project_intelligence.pr_preparation import (
        PullRequestPreparationError,
    )

    if not argv or argv == ["--help"] or argv == ["-h"]:
        print(_PR_REVIEW_HELP, end="")
        return 0
    parser = argparse.ArgumentParser(
        prog="workbench review pr",
        add_help=False,
    )
    parser.add_argument("pull_request", type=int)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--channel")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--git-executable")
    parser.add_argument("--network-timeout", type=float, default=300.0)
    parser.add_argument("--prepared-receipt", type=Path)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--apply")
    parser.add_argument("--yes", action="store_true")
    try:
        arguments, recipe_arguments = parser.parse_known_args(argv)
        if any(
            token in {"--pr-delta-strict", "--pr-review-attention"}
            for token in recipe_arguments
        ):
            raise ValueError(
                "--pr-delta-strict and --pr-review-attention are internal review-pr routing flags"
            )
        if "--help" in recipe_arguments or "-h" in recipe_arguments:
            print(_PR_REVIEW_HELP, end="")
            return 0
        if arguments.plan and arguments.apply is not None:
            raise ValueError("--plan and --apply are mutually exclusive")
        if arguments.plan and arguments.yes:
            raise ValueError("--plan and --yes are mutually exclusive")
        if arguments.prepared_receipt is not None and (
            arguments.plan or arguments.apply is not None or arguments.yes
        ):
            raise ValueError(
                "--prepared-receipt cannot be combined with --plan, --apply, or --yes"
            )
        if arguments.profile != "supersymmetry":
            raise ValueError("--profile supersymmetry is required; pack selection is never implicit")

        if arguments.prepared_receipt is not None:
            from workbench_project_intelligence.pr_preparation import (
                load_pr_preparation_receipt_v2,
            )

            receipt = load_pr_preparation_receipt_v2(arguments.prepared_receipt)
            if receipt.get("project_id") != arguments.profile:
                raise ValueError("prepared receipt does not belong to the selected profile")
            if receipt.get("pull_request") != arguments.pull_request:
                raise ValueError("prepared receipt does not identify the requested pull request")
            routed_recipe_arguments = [
                token for token in recipe_arguments if token != "--strict"
            ]
            routed_recipe_arguments.append("--pr-review-attention")
            if "--strict" in recipe_arguments:
                routed_recipe_arguments.append("--pr-delta-strict")
            return _review_main(
                [
                    "recipes",
                    "--profile",
                    arguments.profile,
                    "--source",
                    str(arguments.source),
                    "--prepared-receipt",
                    str(arguments.prepared_receipt),
                    *routed_recipe_arguments,
                ],
                internal_pr_route=True,
            )

        from workbench_project_intelligence.pr_preparation import (
            apply_pr_preparation_plan_v2,
            build_pr_preparation_plan_v2,
            load_pull_request_provider_profile,
        )
        from workbench_project_intelligence.project_acquisition import (
            load_acquisition_profile,
        )
        from workbench_api.state_paths import default_runtime_state_root

        acquisition = load_acquisition_profile(
            ROOT / "profiles/packs/supersymmetry/acquisition-v1.json"
        )
        provider = load_pull_request_provider_profile(
            ROOT / "profiles/packs/supersymmetry/github-pr-provider-v1.json"
        )
        state_root = (
            default_runtime_state_root(ROOT)
            if arguments.state_root is None
            else arguments.state_root
        )
        plan = build_pr_preparation_plan_v2(
            acquisition,
            provider,
            pull_request=arguments.pull_request,
            repository=arguments.source,
            state_root=state_root,
            channel_name=arguments.channel,
            git_executable=arguments.git_executable,
            network_timeout=arguments.network_timeout,
        )
        if arguments.plan:
            if _recipe_review_flag(recipe_arguments, "--json"):
                sys.stdout.write(
                    json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                )
            else:
                sys.stdout.write(_render_pr_review_plan(plan))
            return 0
        apply_authorized = arguments.yes or arguments.apply is not None
        if not apply_authorized:
            interactive = bool(getattr(sys.stdin, "isatty", lambda: False)()) and bool(
                getattr(sys.stdout, "isatty", lambda: False)()
            )
            if not interactive:
                sys.stdout.write(_render_pr_review_plan(plan))
                return 0
            sys.stdout.write(_render_pr_review_plan(plan))
            sys.stdout.write("Prepare exact refs and begin this review? [y/N] ")
            sys.stdout.flush()
            if sys.stdin.readline().strip().casefold() != "y":
                sys.stdout.write("PR review preparation cancelled; no refs were changed.\n")
                return 0
        if arguments.apply is not None and arguments.apply != plan["plan_id"]:
            raise PullRequestPreparationError(
                "--apply does not match the freshly observed provider-bound plan"
            )
        result = apply_pr_preparation_plan_v2(
            acquisition,
            provider,
            plan,
            network_timeout=arguments.network_timeout,
        )
        routed_recipe_arguments = [
            token for token in recipe_arguments if token != "--strict"
        ]
        routed_recipe_arguments.append("--pr-review-attention")
        if "--strict" in recipe_arguments:
            routed_recipe_arguments.append("--pr-delta-strict")
        return _review_main(
            [
                "recipes",
                "--profile",
                arguments.profile,
                "--source",
                str(arguments.source),
                "--prepared-receipt",
                str(result["receipt_path"]),
                *routed_recipe_arguments,
            ],
            internal_pr_route=True,
        )
    except (OSError, ValueError, PullRequestPreparationError) as exc:
        print(f"workbench review pr: {_review_terminal_text(exc)}", file=sys.stderr)
        return 2

def _review_main(argv: list[str], *, internal_pr_route: bool = False) -> int:
    if not argv or argv == ["--help"] or argv == ["-h"]:
        print(_REVIEW_HELP, end="")
        return 0
    if argv[0] == "pr":
        return _review_pr_main(argv[1:])
    if argv[0] == "prepare-pr":
        from workbench_project_intelligence.pr_preparation import (
            main as preparation_main,
        )
        from workbench_api.state_paths import default_runtime_state_root

        return preparation_main(
            argv[1:],
            acquisition_profiles={
                "supersymmetry": (
                    ROOT / "profiles/packs/supersymmetry/acquisition-v1.json"
                )
            },
            provider_profiles={
                "supersymmetry": (
                    ROOT / "profiles/packs/supersymmetry/github-pr-provider-v1.json"
                )
            },
            default_state_root=default_runtime_state_root(ROOT),
        )
    if argv[0] != "recipes":
        print(
            f"workbench review: unknown review target {argv[0]!r}; "
            "available targets: pr, prepare-pr, recipes",
            file=sys.stderr,
        )
        return 2

    forwarded = argv[1:]
    if "--help" in forwarded or "-h" in forwarded:
        print(_RECIPE_REVIEW_HELP, end="")
        return 0
    try:
        profile = _review_single_option(forwarded, "--profile")
        source_value = _review_single_option(forwarded, "--source")
        baseline = _review_single_option(forwarded, "--baseline")
        baseline_ref = _review_single_option(forwarded, "--baseline-ref")
        pr_base = _review_single_option(forwarded, "--pr-base")
        prepared_receipt = _review_single_option(forwarded, "--prepared-receipt")
        _recipe_review_flag(forwarded, "--json")
        _recipe_review_flag(forwarded, "--full-json-v1")
        pr_delta_strict = _recipe_review_flag(forwarded, "--pr-delta-strict")
        pr_attention_partition = _recipe_review_flag(
            forwarded, "--pr-review-attention"
        )
        if (pr_delta_strict or pr_attention_partition) and not internal_pr_route:
            raise ValueError(
                "--pr-delta-strict and --pr-review-attention are internal review-pr routing flags"
            )
        if "--json" in forwarded and "--full-json-v1" in forwarded:
            raise ValueError("--json and --full-json-v1 are mutually exclusive")
    except ValueError as exc:
        print(f"workbench review recipes: {exc}", file=sys.stderr)
        return 2
    if profile != "supersymmetry":
        print(
            "workbench review recipes: --profile supersymmetry is required; "
            "pack selection is never implicit",
            file=sys.stderr,
        )
        return 2
    baseline_selectors = [
        value
        for value in (baseline, baseline_ref, pr_base, prepared_receipt)
        if value is not None
    ]
    if not baseline_selectors:
        print(
            "workbench review recipes: choose exactly one baseline: "
            "--baseline PATH, --baseline-ref REV, --pr-base REV, or "
            "--prepared-receipt PATH",
            file=sys.stderr,
        )
        return 2
    if len(baseline_selectors) > 1:
        print(
            "workbench review recipes: --baseline, --baseline-ref, --pr-base, "
            "and --prepared-receipt are mutually exclusive",
            file=sys.stderr,
        )
        return 2
    if source_value is None:
        print(
            "workbench review recipes: --source is required so the candidate "
            "directory tree is explicit",
            file=sys.stderr,
        )
        return 2

    if baseline_ref is None and pr_base is None and prepared_receipt is None:
        try:
            return _run_recipe_owner_review(
                forwarded,
                selection={
                    "kind": "directory",
                    "baseline_path": str(Path(baseline).expanduser().resolve()),
                    "candidate_path": str(Path(source_value).expanduser().resolve()),
                },
                pr_delta_strict=pr_delta_strict,
                pr_attention_partition=pr_attention_partition,
            )
        except (OSError, ValueError) as exc:
            print(
                f"workbench review recipes: {_review_terminal_text(exc)}",
                file=sys.stderr,
            )
            return 2

    from workbench_pack_program_studio.profile import load_profile, resolve_named_profile
    from workbench_project_intelligence.git_tree import (
        GitTreeMaterializationError,
        materialize_git_merge_base_subtree,
        materialize_git_subtree,
    )
    from workbench_project_intelligence.pr_preparation import (
        PullRequestPreparationError,
        load_pr_preparation_receipt_v2,
        load_pull_request_provider_profile,
        verify_pr_preparation_receipt_v2,
    )
    from workbench_project_intelligence.project_acquisition import (
        load_acquisition_profile,
    )

    try:
        loaded_profile = load_profile(resolve_named_profile(ROOT, profile))
        layout = dict(loaded_profile.value["source_layout"])
        source = Path(source_value).expanduser()
        selected_relative = _recipe_git_source_layout(source, layout)
        if prepared_receipt is not None:
            receipt = load_pr_preparation_receipt_v2(prepared_receipt)
            acquisition_profile = load_acquisition_profile(
                ROOT / "profiles/packs/supersymmetry/acquisition-v1.json"
            )
            provider_profile = load_pull_request_provider_profile(
                ROOT / "profiles/packs/supersymmetry/github-pr-provider-v1.json"
            )
            verified = verify_pr_preparation_receipt_v2(
                receipt,
                repository=source,
                acquisition_profile=acquisition_profile,
                provider_profile=provider_profile,
            )
            baseline_ref = str(receipt["base_ref"])
            baseline_oid = str(receipt["base_oid"])
            prepared_selection = {
                "kind": "prepared-provider-pull-request",
                "project_id": receipt["project_id"],
                "pull_request": receipt["pull_request"],
                "pull_request_url": receipt["pull_request_url"],
                "pull_request_state": receipt["pull_request_state"],
                "pull_request_merged": receipt["pull_request_merged"],
                "provider_kind": receipt["provider_kind"],
                "provider_profile_id": receipt["provider_profile_id"],
                "remote_url": receipt["remote_url"],
                "channel_id": receipt["channel_id"],
                "repository_root": receipt["repository_root"],
                "delta_kind": receipt["delta_kind"],
                "base": {
                    "repository": receipt["base_repository"],
                    "name": receipt["base_name"],
                    "remote_ref": receipt["base_remote_ref"],
                    "immutable_ref": receipt["base_ref"],
                    "oid": receipt["base_oid"],
                },
                "head": {
                    "repository": receipt["head_repository"],
                    "name": receipt["head_name"],
                    "remote_ref": receipt["head_remote_ref"],
                    "immutable_ref": receipt["head_ref"],
                    "oid": receipt["head_oid"],
                },
                "provider_merge": {
                    "remote_ref": receipt["provider_merge_remote_ref"],
                    "immutable_ref": receipt["provider_merge_ref"],
                    "oid": receipt["provider_merge_oid"],
                },
                "receipt_id": receipt["receipt_id"],
                "prepared_at": receipt["prepared_at"],
            }
            repository_paths = tuple(verified["repository_changed_paths"])
            selected_paths = _recipe_review_selected_paths(
                source=source,
                repository_root=Path(str(verified["repository_root"])),
                selected_relative=selected_relative,
                repository_paths=repository_paths,
            )
            without_receipt = _review_without_option(
                forwarded, "--prepared-receipt"
            )
            without_source = _review_without_option(without_receipt, "--source")
            materialization_options = {
                "source": source,
                "selected_relative": selected_relative,
                "destination_relative": selected_relative,
                "max_files": int(layout["max_files"]),
                "max_file_bytes": int(layout["max_file_bytes"]),
                "max_total_bytes": int(layout["max_total_bytes"]),
            }
            with materialize_git_subtree(
                ref=baseline_ref,
                **materialization_options,
            ) as prepared_baseline, materialize_git_subtree(
                ref=str(receipt["head_ref"]),
                **materialization_options,
            ) as prepared_candidate:
                if (
                    prepared_baseline.commit_oid != baseline_oid
                    or prepared_candidate.commit_oid != receipt["head_oid"]
                ):
                    raise PullRequestPreparationError(
                        "prepared immutable refs changed during review"
                    )
                hygiene_state, hygiene_detail = _pr_review_hygiene(
                    Path(str(receipt["repository_root"])),
                    str(receipt["base_ref"]),
                    str(receipt["head_ref"]),
                )
                return _run_recipe_owner_review(
                    [
                        *without_source,
                        "--source",
                        str(prepared_candidate.root),
                        "--baseline",
                        str(prepared_baseline.root),
                    ],
                    selection={
                        **prepared_selection,
                        "committed_scope": _recipe_review_committed_scope(
                            repository_paths, selected_paths
                        ),
                    },
                    human_selection=(
                        _render_prepared_recipe_selection(receipt)
                        + _render_pr_review_hygiene(hygiene_state, hygiene_detail)
                    ),
                    candidate_git_binding={
                        "repository_root": receipt["repository_root"],
                        "revision": receipt["head_oid"],
                        "dirty": False,
                    },
                    baseline_git_binding={
                        "repository_root": receipt["repository_root"],
                        "revision": baseline_oid,
                        "dirty": False,
                    },
                    strict_all_extra_attention=hygiene_state == "attention",
                    pr_delta_strict=pr_delta_strict,
                    pr_attention_partition=pr_attention_partition,
                    git_hygiene={
                        "state": hygiene_state,
                        "detail": hygiene_detail,
                    },
                )
        selector_option = "--pr-base" if pr_base is not None else "--baseline-ref"
        without_ref = _review_without_option(forwarded, selector_option)
        materialization_options = {
            "source": source,
            "selected_relative": selected_relative,
            "destination_relative": selected_relative,
            "max_files": int(layout["max_files"]),
            "max_file_bytes": int(layout["max_file_bytes"]),
            "max_total_bytes": int(layout["max_total_bytes"]),
        }
        if pr_base is not None:
            materialization = materialize_git_merge_base_subtree(
                target_ref=pr_base,
                **materialization_options,
            )
        else:
            materialization = materialize_git_subtree(
                ref=baseline_ref,
                **materialization_options,
            )
        with materialization as materialized:
            selection = {
                "kind": (
                    "pull-request-merge-base"
                    if materialized.selection_kind == "merge-base"
                    else "exact-local-ref"
                ),
                "repository_root": str(materialized.repository_root),
                "requested_ref": materialized.requested_ref,
                "resolved_commit": materialized.commit_oid,
                "selected_root": materialized.repository_relative_path,
                "candidate": materialized.candidate_git_binding,
            }
            if materialized.selection_kind == "merge-base":
                repository_paths = tuple(materialized.repository_changed_paths or ())
                selected_paths = tuple(materialized.selected_changed_paths or ())
                selection.update(
                    {
                        "target_tip": materialized.target_tip_oid,
                        "merge_base": materialized.commit_oid,
                        "repository_changed_file_count": (
                            materialized.repository_changed_file_count
                        ),
                        "selected_changed_file_count": (
                            materialized.selected_changed_file_count
                        ),
                        "committed_scope": _recipe_review_committed_scope(
                            repository_paths, selected_paths
                        ),
                    }
                )
            return _run_recipe_owner_review(
                [
                    *without_ref,
                    "--baseline",
                    str(materialized.root),
                ],
                selection=selection,
                human_selection=_render_recipe_git_selection(materialized),
                candidate_git_binding=materialized.candidate_git_binding,
                baseline_git_binding={
                    "repository_root": str(materialized.repository_root),
                    "revision": materialized.commit_oid,
                    "dirty": False,
                },
                pr_delta_strict=pr_delta_strict,
                pr_attention_partition=pr_attention_partition,
            )
    except (
        GitTreeMaterializationError,
        PullRequestPreparationError,
        OSError,
        ValueError,
    ) as exc:
        print(f"workbench review recipes: {_review_terminal_text(exc)}", file=sys.stderr)
        guidance = _recipe_review_git_error_guidance(exc)
        if guidance is not None:
            print(guidance, file=sys.stderr)
        return 2
