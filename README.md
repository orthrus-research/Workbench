<img src="assets/brand/orthrus-research-avatar.png" width="64" alt="Orthrus Research mark">

# Workbench

*An [Orthrus Research](https://github.com/orthrus-research) project*

Make a pack change with the evidence in view. Workbench brings together local
project inspection, captured recipe relationships, saved-source review, and
explicit native checks for Minecraft 1.12.2 development.

**Current download:** [Linux x64 Supersymmetry client 0.1.2](https://github.com/orthrus-research/Workbench/releases/tag/linux-x64-mvp-0.1.2).
It includes Core, Atlas, Axiom, Shell, Textual, the pack profiles, and the
matching Axiom engine. CleanroomMC and Supersymmetry are the first platform and
pack integrations. See the release notes for its exact scope and limitations.

## What would you like to do?

### Where does this item come from?

Open a completed scan of your pack's recipes and search for an item or fluid.
Follow the recipes in that scan that make or use it, with amounts, energy, and
time where recorded. Atlas highlights possible dead ends there, such as an
ingredient with no known recipe or an output with no known use. A trusted shared
scan opens without starting Minecraft.
[Trace a recipe chain →](docs/guides/atlas-mvp.md)

### What changed in my pack scripts?

Changed a recipe, material, or quest script? Compare saved files with an earlier
Git commit inside VS Code or IntelliJ Community. See exact line changes and any
issues Workbench can point to in the source. Nothing is committed or rewritten.
[Review saved pack edits →](docs/architecture/LOCAL-SOURCE-REVIEW.md)

### Will my new recipe register?

Save a Supersymmetry recipe or material edit, then run Axiom. It checks the
relevant mod setup code without launching Minecraft and points to the saved
line when something fails. That covers material, item, and fluid setup, plus
recipes you add, change, or remove. It does not test machines in a world.
[Check an edit with Axiom →](modules/axiom/README.md)

## Get started

On Linux x64 with GNU libc 2.28 or newer, run:

```sh
curl -fsSL https://susy.work/install.sh | bash
```

This also works in a compatible WSL2 Linux shell. Keep Workbench's state on the
Linux filesystem. The installer verifies a pinned Python runtime and the
matching release bundle, installs in your user directory, and prints the full
path to `workbench-tui` for guided setup. It does not require sudo or a source
checkout. After a successful install, it makes `workbench` and `workbench-tui`
available through `~/.local/bin` in new Bash or zsh sessions when those startup
files can be updated safely. Existing conflicting files are left alone and
reported. Open a new terminal, or run `export PATH="$HOME/.local/bin:$PATH"` to
use the commands in your current shell. To inspect the current script first,
download `workbench-install-linux-x64-path-v2.sh` and verify it against
`SHA256SUMS-path-v2` on the [release page](https://github.com/orthrus-research/Workbench/releases/tag/linux-x64-mvp-0.1.2).
The original 0.1.2 installer and bundle remain available there.

In Textual, open one project and choose a workflow. Workbench does not provide
the external game files: you can import a complete Prism instance ZIP, while
fresh CurseForge file acquisition awaits a Workbench API key. The
[getting started guide](docs/guides/getting-started.md) covers setup, alternate
installation, IDEs, and runtime requirements.

## Explore the repository

- **Learn:** [Documentation index](docs/README.md) ·
  [Product boundaries](docs/product/README.md)
- **Use an IDE:** [VS Code](clients/vscode/README.md) ·
  [IntelliJ Community](clients/intellij-community/README.md)
- **Build and contribute:** [Module development](docs/guides/module-development.md) ·
  [Validation](docs/architecture/VALIDATION-AND-TESTING.md)
- **Reference:** [Architecture topology](docs/architecture/TOPOLOGY.md) ·
  [Component releases](packaging/release/README.md)

[Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Support](SUPPORT.md) ·
[Security](SECURITY.md) · [Governance](GOVERNANCE.md) · [License](LICENSE)
