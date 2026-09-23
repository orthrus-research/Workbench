"use strict";

const { invokeAtlasSearch } = require("./developerToolsClient");
const { invokeAtlasImpact } = require("./atlasRecipeImpactClient");
const { invokeCompleteAtlasImpact } = require("./atlasCompleteRecipeImpactClient");
const { resolveCoreLaunch } = require("./coreLaunch");
const { impactCautions } = require("./atlasRecipeImpactTree");

/** Existing native journey, with UI supplied explicitly for host acceptance. */
function createAtlasRecipeImpactFlow(vscode, impact, { selectedCore, currentWorkingDirectory }) {
  async function runImpactUi(prefix, callback) {
    if (!vscode.workspace.isTrusted) {
      void vscode.window.showWarningMessage("Workbench will not query an Atlas graph from an untrusted workspace.");
      return undefined;
    }
    try {
      return await callback();
    } catch (error) {
      void vscode.window.showErrorMessage(
        `${prefix}: ${error instanceof Error ? error.message : String(error)}`,
      );
      return undefined;
    }
  }

  async function impactBound(title, prompt, value, minimum, maximum) {
    const selected = await vscode.window.showInputBox({
      title,
      prompt,
      value: String(value),
      ignoreFocusOut: true,
      validateInput: (candidate) => {
        if (!/^[0-9]+$/.test(candidate)) return "Enter one integer.";
        const parsed = Number(candidate);
        return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum
          ? undefined : `Enter an integer from ${minimum} through ${maximum}.`;
      },
    });
    return selected === undefined ? undefined : Number(selected);
  }

  async function analyzeRecipeImpact(impact) {
    return runImpactUi("Atlas recipe impact analysis failed", async () => {
      const selectedRoots = await vscode.window.showOpenDialog({
        title: "Select Explicit Atlas Categorical Graph",
        defaultUri: vscode.workspace.workspaceFolders?.length === 1
          && vscode.workspace.workspaceFolders[0].uri.scheme === "file"
          ? vscode.workspace.workspaceFolders[0].uri : undefined,
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Use This Graph",
      });
      if (!selectedRoots || selectedRoots.length !== 1) return undefined;
      if (selectedRoots[0].scheme !== "file") {
        throw new Error("Select one local filesystem or WSL UNC graph directory");
      }
      const executable = selectedCore();
      const launch = resolveCoreLaunch(executable);
      const root = selectedRoots[0].fsPath;
      const selectionMode = await vscode.window.showQuickPick(
        [
          {
            label: "Search the verified graph",
            description: "recommended · verifies once for search and again for impact",
            mode: "search",
          },
          {
            label: "Use an exact gt-recipe selection ID",
            description: "skips search · the impact call still verifies the graph and selection",
            mode: "exact",
          },
        ],
        {
          title: "Select an Observed GT Recipe",
          placeHolder: "Choose discovery or paste an existing exact Atlas selection",
        },
      );
      if (!selectionMode) return undefined;
      let selectionId;
      let priorSearch;
      let priorSelection;
      if (selectionMode.mode === "exact") {
        const exact = await vscode.window.showInputBox({
          title: "Exact Observed GT Recipe Selection",
          prompt: "Exact gt-recipe selection_id copied from this Atlas graph",
          ignoreFocusOut: true,
          validateInput: (value) => value.trim() && !/[\r\n\0]/.test(value)
            && Buffer.byteLength(value.trim(), "utf8") <= 256 * 1024
            ? undefined : "Enter one bounded, non-empty selection ID.",
        });
        if (exact === undefined) return undefined;
        selectionId = exact.trim();
      } else {
        const query = await vscode.window.showInputBox({
          title: "Find One Observed GT Recipe",
          prompt: "Recipe map, resource, or exact semantic text in the selected graph",
          ignoreFocusOut: true,
          validateInput: (value) => value.trim() && !/[\r\n\0]/.test(value)
            && Buffer.byteLength(value.trim(), "utf8") <= 16 * 1024
            ? undefined : "Enter one bounded, non-empty search line.",
        });
        if (query === undefined) return undefined;
        const search = await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: "Verifying the explicit Atlas graph and searching observed recipes",
            cancellable: false,
          },
          () => invokeAtlasSearch(executable, root, query.trim(), 200, {
            cwd: currentWorkingDirectory(),
            launch,
            timeoutMs: 20 * 60 * 1000,
          }),
        );
        if (search.context.context_type !== "categorical-graph-v2") {
          throw new Error("Recipe impact requires an explicit categorical graph; a source checkout cannot support this analysis");
        }
        const recipes = search.results.filter((result) => result.kind === "gt-recipe");
        if (!recipes.length) {
          void vscode.window.showInformationMessage(
            `The verified graph returned no observed GT recipes for ${JSON.stringify(query.trim())}.`,
          );
          return undefined;
        }
        const selected = await vscode.window.showQuickPick(
          recipes.map((recipe) => ({
            label: recipe.semantic_key,
            description: String(recipe.properties.recipe_map || "gt-recipe"),
            detail: recipe.selection_id,
            recipe,
          })),
          {
            title: "Observed GT Recipe",
            placeHolder: "Choose one exact graph node for the removal scenario",
            matchOnDescription: true,
            matchOnDetail: true,
          },
        );
        if (!selected) return undefined;
        selectionId = selected.recipe.selection_id;
        priorSearch = search;
        priorSelection = selected.recipe;
      }
      const exploration = await vscode.window.showQuickPick([
        { label: "Bounded exploration", exploration: "bounded", description: "Choose depth and node limits; traversal frontiers remain visible" },
        { label: "Complete observed finite exploration", exploration: "complete-finite", description: "Explore all admitted finite dependencies; evidence gaps and viability remain unknown" },
      ], { title: "Recipe Impact Exploration", placeHolder: "Choose the scope of this removal scenario" });
      if (!exploration) return undefined;
      const complete = exploration.exploration === "complete-finite";
      let maxDepth, maxNodes;
      if (!complete) {
        maxDepth = await impactBound(
          "Recipe Impact Propagation Bound",
          "Maximum finite-recipe and quest-prerequisite depth",
          4,
          1,
          12,
        );
        if (maxDepth === undefined) return undefined;
        maxNodes = await impactBound(
          "Recipe Impact Node Bound",
          "Maximum observed graph nodes inspected by the bounded analysis",
          500,
          10,
          2000,
        );
        if (maxNodes === undefined) return undefined;
      }

      const cancellation = new AbortController();
      const report = await vscode.window.withProgress(
        {
          location: vscode.ProgressLocation.Notification,
          title: "Verifying the Atlas graph and deriving recipe impact candidates",
          cancellable: complete,
        },
        async (_progress, token) => {
          const subscription = complete ? token?.onCancellationRequested(() => cancellation.abort()) : undefined;
          if (complete && token?.isCancellationRequested) cancellation.abort();
          try {
            if (complete) return await invokeCompleteAtlasImpact(executable, root, selectionId, {
              cwd: currentWorkingDirectory(), launch, priorSearch, priorSelection, signal: cancellation.signal,
            });
            return await invokeAtlasImpact(
              executable,
              root,
              selectionId,
              maxDepth,
              maxNodes,
              {
                cwd: currentWorkingDirectory(),
                launch,
                priorSearch,
                priorSelection,
                timeoutMs: 20 * 60 * 1000,
              },
            );
          } catch (error) {
            if (!cancellation.signal.aborted) throw error;
            return undefined;
          } finally { subscription?.dispose(); }
        },
      );
      if (cancellation.signal.aborted || !report) {
        void vscode.window.showInformationMessage("Atlas complete exploration cancelled. The previous report remains available.");
        return undefined;
      }
      impact.setReport(report);
      void vscode.commands.executeCommand("workbench.recipeImpact.focus").then(
        undefined,
        () => undefined,
      );
      const counts = report.summary;
      const cautions = impactCautions(report);
      const message = `Atlas found ${counts.at_risk_resource_candidate_count} resource and ${counts.at_risk_recipe_candidate_count} downstream recipe candidates. ${cautions.text}. Alternative viability is not established.`;
      if (cautions.warning) {
        void vscode.window.showWarningMessage(message);
      } else {
        void vscode.window.showInformationMessage(message);
      }
      return report;
    });
  }

  return {
    analyze: () => analyzeRecipeImpact(impact),
    openReport: () => runImpactUi("Could not open the Atlas recipe impact report", () => impact.openReport()),
  };
}

module.exports = { createAtlasRecipeImpactFlow };
