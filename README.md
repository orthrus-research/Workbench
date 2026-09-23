<img src="assets/brand/orthrus-research-avatar.png" width="64" alt="Orthrus Research mark">

# Workbench

*An [Orthrus Research](https://github.com/orthrus-research) project*

Make a pack change with the evidence in view. Workbench brings together local
project inspection, captured recipe relationships, saved-source review, and
explicit native checks for Minecraft 1.12.2 development.

**Current download:** [Linux x64 preview for Python 3.14](https://github.com/orthrus-research/Workbench/releases/tag/linux-x64-mvp-2026-09-23).
Its release notes list exact components, supported contexts, and qualification.
CleanroomMC and Supersymmetry are the first platform and pack integrations.

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

1. [Download the preview bundle](https://github.com/orthrus-research/Workbench/releases/tag/linux-x64-mvp-2026-09-23),
   verify its checksum, and follow its included `GETTING-STARTED.md`.
2. [Open one project](docs/guides/getting-started.md#2-open-your-project) and
   inspect the installed modules, profiles, and available commands.
3. [Choose a workflow](docs/guides/getting-started.md#3-choose-a-workflow) from the paths above.

The [current source guide](docs/guides/getting-started.md) covers ongoing
development, alternate wheelhouse composition, IDE setup, and runtime
requirements. The preview bundle's guide is pinned to its exact source commit.

## Explore the repository

- **Learn:** [Documentation index](docs/README.md) ·
  [Product boundaries](docs/product/README.md)
- **Use an IDE:** [VS Code](clients/vscode/README.md) ·
  [IntelliJ Community](clients/intellij-community/README.md)
- **Build and contribute:** [Module development](docs/guides/module-development.md) ·
  [Validation](docs/architecture/VALIDATION-AND-TESTING.md)
- **Reference:** [Architecture topology](docs/architecture/TOPOLOGY.md) ·
  [Component releases](packaging/release/README.md)

[Contributing](CONTRIBUTING.md) · [Support](SUPPORT.md) ·
[Security](SECURITY.md) · [Governance](GOVERNANCE.md) · [License](LICENSE)
