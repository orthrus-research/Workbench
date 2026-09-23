# Managed Prism and Packwiz tooling

Status: Core provisioning is implemented for Linux x64 and Windows x64.
Windows native execution still requires qualification before a Windows release
claim.

`workbench tooling` is the Core-owned acquisition stage that `workbench init`
will call. It does not select a project, configure Java, create a Prism data
root, choose an account, install a pack, or launch Minecraft. Its
`initialized` result means both managed Prism and Packwiz executables are
present and their retained bytes match the pinned policy. Java and profile
readiness are separate inputs to the eventual whole-suite init result.

## Sources and custody

| Tool | Locked input | Recovery when online retrieval is unavailable |
| --- | --- | --- |
| Prism Launcher | Official 11.1.0 Linux Qt6 portable or Windows x64 MinGW portable release archive, with exact size and SHA-256 from the [upstream release](https://github.com/PrismLauncher/PrismLauncher/releases/tag/11.1.0). | Reuse the verified Core cache or pass `--seed-dir` containing the exact official archive. |
| Packwiz CLI | Upstream [commit `ef87d964`](https://github.com/packwiz/packwiz/commit/ef87d964f8cbd52b3b13ea42453ef322290e2b9e), its Git tree, and its vendored Go dependencies are shipped as two pinned text resources that reconstruct a 2,551,418-byte source archive with upstream and dependency licenses. Core builds it locally and checks the resulting platform binary against a pinned size and SHA-256. | The source and dependencies are in the Workbench package. A verified managed binary is reused; a changed one is retained in quarantine and rebuilt. No GitHub Actions artifact, nightly link, module proxy or `go install @latest` is used during init. |
| Go compiler for the Packwiz build | Official Go 1.25.14 Linux/Windows x64 archive, exact size and SHA-256 from [go.dev downloads](https://go.dev/dl/). | Reuse Core's verified download cache, pass the exact archive through `--seed-dir`, or select an already installed exact compiler with `--go-executable`. |

The default URLs are fixed in Core policy. A developer cannot supply a remote
URL to redirect installation. A seed file must have the exact official name,
size and digest; a selected Go compiler must report the exact version, and
the Packwiz build must match the pinned output bytes. Go builds with the
bundled vendor tree and network/module/VCS fetching disabled. Release updates
therefore require an explicit policy, source-bundle and expected-output change.

Core defaults to the platform user state root and accepts an explicit
`--state-root` override. It publishes a receipt only after verification,
rechecks retained tool contents before reuse, and moves changed installations
to a private quarantine before repair. A missing official download does not
permit an unverified substitute: the command reports the exact seed filename
needed. The archive seed can be included as a separate object in a Workbench
binary kit without changing the Core command or its trust policy.

Prism's upstream Windows MinGW portable archive avoids requiring a separately
installed Visual C++ runtime; upstream describes MinGW builds as less tested
than MSVC builds on its [Windows download page](https://prismlauncher.org/download/windows/).
That tradeoff remains subject to Windows native qualification. Launcher account
setup is advisory for tooling initialization. Runtime Launch reports its own
Quick Setup or offline-profile recovery if a later launch is intercepted.

## Commands

```text
workbench tooling --check --json
workbench tooling --plan --json
workbench tooling --apply PLAN_ID --json

# Offline seed example: use the same options for plan and apply.
workbench tooling --plan --seed-dir /path/to/official-archives --json
workbench tooling --apply PLAN_ID --seed-dir /path/to/official-archives --json
```

`--state-root` selects an explicit user state location. `--go-executable`
selects an existing exact Go 1.25.14 compiler. These selections are bound into
the plan ID. `--check` is read-only and returns a nonzero exit status until both
managed tools are ready. `--plan` is read-only; `--apply` requires its current
plan ID. The executable paths in the result are Core's handoff to profile and
launcher adapters, not permanent paths to encode in project configuration.

Maintainers can reproduce the bundled Packwiz source archive with
`tools/rebuild_packwiz_bundle.py` from the exact clean upstream commit after
running `go mod vendor` with the pinned Go compiler. The script refuses a
different Git tree, changed module manifests, missing vendored licenses, or a
bundle whose bytes differ from the checked-in authority. Each checked-in text
part is below the public source file-size limit; Core verifies the parts and
reconstructed archive before extracting them.
