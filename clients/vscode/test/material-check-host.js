"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs/promises"), path = require("node:path");
const vscode = require("vscode");

/** Genuine editor/diagnostic/provider services with scripted dialog answers only. */
async function run(extension) {
  const fixture = JSON.parse(await fs.readFile(process.env.WORKBENCH_TEST_MATERIAL_FIXTURE, "utf8"));
  if (fixture.deliveryMeasurement) return require('./material-delivery-host').run(extension, fixture);
  const diagnosticsOnly = fixture.options.intent === "";
  const singleRerun = fixture.comparisonMode === "single-rerun";
  if (singleRerun) assert.ok(fixture.comparisonOverflowEvidence, "Single rerun requires retained full-pack comparison overflow evidence");
  const root = vscode.workspace.workspaceFolders[0].uri.fsPath;
  assert.equal(root, fixture.pack); assert.equal(process.env.WORKBENCH_STATE_ROOT, fixture.coreState);
  assert.equal(fixture.state, "prepared-not-qualified");
  const { invokeMaterialCheck, materialFindingMessage, findingLabel } = require(path.join(extension.extensionPath, "materialChecksClient"));
  const { registerDeveloperChecks } = require(path.join(extension.extensionPath, "developerChecks"));
  require(path.join(extension.extensionPath, "developerContext")).rememberContext(root, fixture.session);
  const transport = (...args) => invokeMaterialCheck(fixture.executable, root, fixture.session, ...args);
  const snapshot = require(path.join(extension.extensionPath, "materialSnapshotClient"));
  let findingPages = 0, uiPageLoads = 0;
  const call = async (...args) => {
    const result = await transport(...args);
    if (result.format === snapshot.VIEW && result.snapshot_id) while (!result.finding_page.complete) {
      const page = await transport("query", result.attempt_id, snapshot.nextFindingsQuery(result, true));
      assert.equal(page.state, "ready"); findingPages++;
      result.findings.push(...snapshot.findings(page));
      Object.assign(result.presentation.finding_labels, page.presentation.finding_labels); result.finding_page = page;
    }
    return result;
  };
  if (fixture.retainedReaderOnly === true) {
    const { materialSummary } = require(path.join(extension.extensionPath, "materialChecksClient"));
    assert.ok(fixture.retainedAttempts?.length >= 2, "Select explicit historical captures");
    const evidence = [];
    for (const expected of fixture.retainedAttempts) {
      const result = await call("show", expected.attempt);
      assert.equal(result.snapshot_id, expected.snapshot);
      assert.equal(result.native_outcome, expected.nativeOutcome);
      assert.equal(result.coverage, expected.coverage);
      assert.equal(result.interpretation.state, "complete");
      assert.equal(result.findings.length, expected.findings);
      const text = materialSummary(result);
      assert.match(text, /Reader interpretation: complete/);
      const document = await vscode.workspace.openTextDocument({ content: text, language: "plaintext" });
      await vscode.window.showTextDocument(document);
      assert.equal(vscode.window.activeTextEditor.document.getText(), text);
      evidence.push({ attempt: result.attempt_id, snapshot: result.snapshot_id,
        readerBinding: result.reader_binding, findings: result.findings.length });
    }
    const expiredEvidence = [];
    if (fixture.expiredAttempts?.length) {
      const history = await transport("history");
      for (const expected of fixture.expiredAttempts) {
        const row = history.attempts.find(item => item.attempt_id === expected.attempt);
        assert.ok(row, "Expired checks remain discoverable in installed history");
        assert.equal(row.state, "expired"); assert.equal(row.detail_state, "expired");
        assert.equal(row.snapshot_id, expected.snapshot);
        assert.equal(row.original_summary.state, "completed");
        assert.equal(row.original_summary.overview.native.result.nativeOutcome, expected.nativeOutcome);
        await assert.rejects(() => transport("show", expected.attempt), /details are expired/);
        const text = JSON.stringify(row, null, 2);
        const document = await vscode.workspace.openTextDocument({ content: text, language: "json" });
        await vscode.window.showTextDocument(document);
        assert.equal(vscode.window.activeTextEditor.document.getText(), text);
        expiredEvidence.push(row);
      }
    }
    let retentionControls = null;
    if (fixture.retentionControls === true) {
      const { createMaterialChecks } = require(path.join(extension.extensionPath, "materialChecks"));
      let maintenance = false, consents = 0;
      const facade = Object.create(vscode); const window = Object.create(vscode.window);
      Object.defineProperty(facade, "window", { value: window });
      Object.defineProperties(window, {
        showQuickPick: { value: async choices => choices.find(row => row.action === (maintenance ? "maintain" : "configure")) || choices.find(row => row.value === "keep-everything") },
        showInputBox: { value: async options => options.value },
        showWarningMessage: { value: async (message, options, accept) => {
          assert.equal(options.modal, true); assert.match(message, /permanent expiry/); assert.match(message, /User-selected external exports are never deleted/);
          consents++; return accept;
        } },
      });
      const shown = [];
      const flow = createMaterialChecks(facade, {}, () => fixture.executable, { diagnostics: { clear() {} }, generation: () => 0,
        details: async value => { shown.push(value); const doc = await vscode.workspace.openTextDocument({ content: JSON.stringify(value, null, 2), language: "json" }); await vscode.window.showTextDocument(doc); } });
      await flow.storageSettings(root, fixture.session);
      assert.equal(shown[0].state, "configured"); assert.equal(shown[0].policy.settings.mode, "keep-everything");
      maintenance = true; await flow.storageSettings(root, fixture.session);
      assert.equal(shown[1].state, "disabled"); assert.equal(consents, 1);
      const status = await transport("retention", { operation: "status" });
      assert.equal(status.policy.settings.mode, "keep-everything"); assert.ok(status.stores.length > 0);
      assert.ok(vscode.window.activeTextEditor.document.getText().includes('"state": "disabled"'));
      retentionControls = { state: "passed", actualCoreTransport: true, realEditor: true, scriptedDialogs: true, consents,
        policy: status.policy.id, storeCount: status.stores.length, mode: status.policy.settings.mode };
    }
    return { editorHost: "real-vscode", nativeWorkers: 0, qualification: false,
      readerOnly: true, findingPages, evidence, expiredEvidence, retentionControls };
  }
  const errors = [], answers = [], subscriptions = [], values = new Map(), inputPrompts = [];
  let diagnostics, inFlightEdit = false, observedStart = false, consentCount = 0;
  const storageNotice = (await call("retention", { operation: "status" })).before_work_notice;
  let storageContinuations = 0;
  const savedSource = fixture.savedSource;
  assert.ok(savedSource && Number.isInteger(savedSource.line) && savedSource.line > 0);
  const nativeError = (result, side = "candidate") => result.findings.some(finding => finding.side === side && finding.severity === "error"
    && findingLabel(result, finding).includes(savedSource.message));
  const source = vscode.Uri.file(path.join(root, savedSource.path));
  const document = await vscode.workspace.openTextDocument(source);
  const original = document.getText();
  assert.equal(original.split(savedSource.anchor).length, 2, "The saved error anchor must occur exactly once");
  const bad = original.replace(savedSource.anchor, savedSource.replacement);
  const corrected = fixture.correctedSource ? original.replace(fixture.correctedSource.anchor, fixture.correctedSource.replacement) : original;
  const correctedSha = fixture.correctedSource?.sha256 || savedSource.sha256;
  assert.notEqual(bad, original);
  async function edit(text, save = false) {
    const editor = await vscode.window.showTextDocument(document);
    assert.equal(await editor.edit(builder => builder.replace(new vscode.Range(document.positionAt(0), document.positionAt(document.getText().length)), text)), true);
    if (save) assert.equal(await document.save(), true);
  }
  const label = wanted => choices => {
    const selected = choices.find(row => (typeof row === "string" ? row : row.label) === wanted);
    assert.ok(selected, `Missing production choice: ${wanted}`); return selected;
  };
  const selectedContext = choices => {
    const selected = choices.find(choice => choice.row?.id === fixture.options.context);
    assert.ok(selected, `Missing fixture material context: ${fixture.options.context}`); return selected;
  };
  const findingChoice = (side = "candidate") => choices => {
    const selected = choices.find(row => row.finding?.side === side && row.finding.channel === savedSource.channel
      && row.finding.severity === "error" && row.detail.includes(savedSource.message)
      && row.finding.location?.path === savedSource.path && row.finding.location?.start.line === savedSource.line);
    assert.ok(selected, `Missing actual ${side} native exception at ${savedSource.path}:${savedSource.line}`); return selected;
  };
  // Do not enumerate VS Code APIs: some getters require unrelated proposed APIs.
  const override = (api, fields) => Object.defineProperties(Object.create(api), Object.fromEntries(
    Object.entries(fields).map(([key, value]) => [key, { value, enumerable: true }])));
  const facade = override(vscode, {
    // Reuse the shipped controller without colliding with its activated instance.
    // Only fixture command/provider names and dialog answers are substituted.
    Uri: override(vscode.Uri, { parse: value => vscode.Uri.parse(value.replace(/^workbench-saved-check:/, "workbench-material-acceptance:")) }),
    commands: override(vscode.commands, { registerCommand: (_id, callback) => vscode.commands.registerCommand("workbench.test.materialSaved", callback) }),
    languages: override(vscode.languages, { createDiagnosticCollection: () => (diagnostics = vscode.languages.createDiagnosticCollection("workbench-material-acceptance")) }),
    workspace: override(vscode.workspace, { registerTextDocumentContentProvider: (_scheme, provider) => vscode.workspace.registerTextDocumentContentProvider("workbench-material-acceptance", provider) }),
    window: override(vscode.window, {
      showQuickPick: async choices => {
        const loaded = await choices;
        const next = loaded.find(row => row.label === "Load remaining findings");
        if (next) { uiPageLoads++; return next; }
        assert.ok(answers.length, "Unexpected dialog"); return answers.shift()(loaded);
      },
      showInputBox: async ({ title }) => {
        const field = title.startsWith("Complete saved") ? "programRoot" : title.startsWith("Saved material") ? "intent"
          : title.startsWith("Installed Axiom") ? "engineHome" : title.startsWith("Selected local") ? "runtimeHome" : title.startsWith("Selected Cleanroom") ? "java" : null;
        assert.ok(field, `Unexpected input: ${title}`); inputPrompts.push(field); return fixture.options[field];
      },
      showWarningMessage: async (message, _options, ...buttons) => {
        if (storageNotice && message === storageNotice) {
          assert.equal(_options.modal, true);
          assert.deepEqual(buttons, ["Review storage and exports", "Continue with complete capture"]);
          storageContinuations++; return buttons[1];
        }
        assert.match(message, /material-check-request:sha256:[0-9a-f]{64}/);
        assert.match(message, /unsaved edits are excluded/); assert.match(message, /not yet qualified/);
        assert.deepEqual(buttons, ["Run this exact material check"]); consentCount++; return buttons[0];
      },
      showErrorMessage: async message => { errors.push(message); },
      withProgress: (options, work) => vscode.window.withProgress(options, async (progress, token) => {
        const running = work(progress, token);
        if (inFlightEdit && options.title.startsWith("Axiom material preflight")) {
          const attempt = values.get("workbench.lastMaterialAttempt");
          const marker = path.join(fixture.coreState, "product-spine/developer-checks/.workbench/check-attempts", attempt, "started.json");
          let completed = false;
          running.then(() => { completed = true; }, () => { completed = true; });
          while (true) {
            try { await fs.access(marker); break; }
            catch (error) { if (error.code !== "ENOENT" || completed) throw error; }
            await new Promise(resolve => setTimeout(resolve, 50));
          }
          observedStart = true; await edit(bad + "\n// unsaved edit during native execution\n");
        }
        return running;
      }),
    }),
  });
  registerDeveloperChecks(facade, { subscriptions, workspaceState: { get: key => values.get(key), update: async (key, value) => values.set(key, value) } }, () => fixture.executable);
  async function command(mode, ...choices) {
    answers.push(label("Use selected Work Session"), label(mode), ...choices);
    await vscode.commands.executeCommand("workbench.test.materialSaved");
    assert.equal(answers.length, 0, `Production flow did not consume its expected dialogs: ${errors.join("; ")}`);
  }
  async function reopen(attempt, ...choices) {
    await command("Reopen an Axiom material check", rows => rows.find(row => row.attempt === attempt), ...choices);
  }
  const rows = () => diagnostics.get(source) || [];
  const initialAttempts = (await call("history")).attempts.length;
  const setup = await call("setup-status", { context: fixture.options.context });
  assert.equal(setup.state, "ready"); assert.equal(setup.setup_id, fixture.setupId);
  let badAttempt, pairAttempt, baselineView;
  try {
    await edit(bad, true); inFlightEdit = true;
    await command("Run Axiom material preflight", selectedContext, label("Read native outcomes, expectations and limitations"));
    assert.deepEqual(errors, []); assert.equal(observedStart, true); assert.equal(document.isDirty, true); assert.equal(rows().length, 0);
    badAttempt = values.get("workbench.lastMaterialAttempt");
    const badResult = await call("show", badAttempt);
    assert.equal(badResult.native.status, fixture.errorNativeStatus); assert.equal(badResult.sourceCurrent, true);
    assert.equal(badResult.native.result.initialization.status, "native-failed");
    assert.ok(nativeError(badResult));
    assert.ok(vscode.window.activeTextEditor.document.getText().includes(`Candidate native status: ${fixture.errorNativeStatus}`));
    await edit(bad, true); inFlightEdit = false;
    await reopen(badAttempt, findingChoice(), label("Read native diagnostic evidence"));
    assert.ok(vscode.window.activeTextEditor.document.getText().includes(savedSource.message), "Selected original diagnostic must load independently");
    await reopen(badAttempt, findingChoice(), label("Open identical saved working copy"));
    assert.deepEqual(errors, []); assert.ok(rows().some(row => row.range.start.line === savedSource.line - 1 && row.severity === vscode.DiagnosticSeverity.Error));
    assert.equal(vscode.window.activeTextEditor.document.uri.toString(), source.toString());
    assert.equal(vscode.window.activeTextEditor.selection.active.line, savedSource.line - 1);
    await edit(bad + "\n// unsaved editor state\n");
    assert.equal(rows().length, 0);
    await reopen(badAttempt, findingChoice(), label("Open identical saved working copy"));
    assert.equal(rows().length, 0); assert.equal(errors.length, 1); assert.match(errors.pop(), /editor buffer differs/i);
    await reopen(badAttempt, findingChoice(), label("Read exact retained source"));
    const retained = vscode.window.activeTextEditor;
    assert.equal(retained.document.uri.scheme, "workbench-material-acceptance"); assert.equal(retained.document.getText(), bad);
    assert.equal(retained.selection.active.line, savedSource.line - 1);
    // TextEditor.edit is a privileged extension API; native typing is the
    // developer-facing read-only contract for a content-provider document.
    await vscode.commands.executeCommand("type", { text: "must remain read-only" });
    assert.equal(retained.document.getText(), bad); assert.equal(retained.document.isDirty, false);
    await edit(corrected, true);
    assert.equal((await call("show", badAttempt)).sourceCurrent, false);
    await reopen(badAttempt, findingChoice(), label("Read exact retained source"));
    assert.equal(vscode.window.activeTextEditor.document.getText(), bad); assert.equal(rows().length, 0);
    if (singleRerun) await command("Run Axiom material preflight", label("Read native outcomes, expectations and limitations"));
    else await reopen(badAttempt, label("Compare a new saved edit against this program"), selectedContext, label("Read native outcomes, expectations and limitations"));
    pairAttempt = values.get("workbench.lastMaterialAttempt");
    const pair = await call("show", pairAttempt);
    const candidate = singleRerun ? pair.native : pair.native.result.candidate;
    if (!singleRerun) {
      assert.equal(pair.native.result.baseline.status, fixture.errorNativeStatus);
      assert.equal(pair.native.result.baseline.result.initialization.status, "native-failed");
      assert.ok(nativeError(pair, "baseline"));
    }
    assert.equal(candidate.status, fixture.correctedNativeStatus);
    assert.equal(candidate.result.initialization.status, fixture.correctedInitializationStatus || "completed");
    assert.ok(!nativeError(pair));
    assert.equal(candidate.result.expectations.status, diagnosticsOnly ? "not-requested" : "matched");
    if (diagnosticsOnly) assert.equal(candidate.result.expectations.status, "not-requested");
    assert.equal(pair.sourceCurrent, true);
    let recipeObservation = null;
    if (fixture.recipe) {
      const read = async (section, key) => {
        const response = await transport("query", pairAttempt, snapshot.query(pair, "record", section, { record_key: snapshot.identity("key", key) }));
        assert.equal(response.state, "ready"); assert.ok(Object.hasOwn(response.payload, "value")); return response.payload.value;
      };
      const recipe = await read("crafting-recipes", fixture.recipe.key);
      const outputRef = recipe.storedFields["com.cleanroommc.groovyscript.compat.vanilla.CraftingRecipe#output"].nativeValueRef;
      const output = await read("crafting-values", outputRef);
      const item = await read("crafting-values", output.item.nativeValueRef);
      assert.equal(output.type, "net.minecraft.item.ItemStack"); assert.equal(output.count, fixture.recipe.count);
      assert.ok(JSON.stringify(item).includes(fixture.recipe.item));
      recipeObservation = { snapshot: pair.snapshot_id, key: fixture.recipe.key, output, item };
    }
    let machineObservation = null;
    if (fixture.gtRecipe) {
      const expected = fixture.gtRecipe;
      const key = snapshot.identity("key", expected.map);
      const page = await transport("query", pairAttempt, snapshot.query(pair, "record", "gt-recipes", { record_key: key }));
      assert.equal(page.state, "ready");
      // A large map remains a file export; only this acceptance harness parses it.
      let map = page.payload.value, exported = null;
      if (!map) {
        const destination = path.join(process.env.TMPDIR, `${pairAttempt}-${expected.map}.json`);
        exported = await transport("export", pairAttempt, { snapshot_id: pair.snapshot_id, destination,
          section_id: "gt-recipes", record_key: key, sha256: page.payload.content.sha256 });
        const bytes = await fs.readFile(destination);
        assert.equal(require("node:crypto").createHash("sha256").update(bytes).digest("hex"), page.payload.content.sha256);
        map = JSON.parse(bytes);
      }
      assert.equal(map.storedValuesComplete, true); assert.deepEqual(map.affectingGaps, []);
      const entries = Object.entries(map.lookup.entries).filter(([, row]) => JSON.stringify(row.value.inputs).includes(expected.marker));
      assert.equal(entries.length, 1);
      const [identity, entry] = entries[0], recipe = entry.value;
      assert.equal(entry.multiplicity, 1); assert.equal(recipe.groovyRecipe, true);
      assert.ok(map.categories.some(category => Object.hasOwn(category.lookupMembers, identity)));
      assert.deepEqual(recipe.inputs.map(input => ({ item: input.stacks.values[0].item.value,
        amount: Number(input.amount.value), consumable: input.isConsumable })), expected.inputs);
      assert.deepEqual(recipe.fluidInputs.map(input => ({ fluid: input.stack.fluid, amount: Number(input.amount.value) })), expected.fluidInputs);
      assert.equal(recipe.outputs.values[0].item.value, expected.output.item);
      assert.equal(recipe.outputs.values[0].count, expected.output.count);
      assert.equal(Number(recipe.duration.value), expected.duration); assert.equal(Number(recipe.EUt.value), expected.EUt);
      assert.ok(recipe.properties.entries.some(property => property.key === "research"));
      machineObservation = { snapshot: pair.snapshot_id, map: expected.map, identity, recipe, exported };
    }
    assert.ok(rows().every(row => row.range.start.line !== savedSource.line - 1), "Corrected error line retained a marker");
    assert.ok(rows().every(row => !row.message.includes(savedSource.message)), "Corrected native exception retained a marker");
    const currentSourceFindings = pair.findings.filter(finding => finding.side === "candidate" && finding.location?.path === savedSource.path);
    assert.equal(require("node:crypto").createHash("sha256").update(await fs.readFile(source.fsPath)).digest("hex"), correctedSha);
    assert.equal(rows().length, currentSourceFindings.length, "Current native findings must remain visible after correction");
    for (const finding of currentSourceFindings) {
      assert.equal(finding.location.sha256, correctedSha);
      assert.ok(rows().some(row => row.message === findingLabel(pair, finding) && row.range.start.line === finding.location.start.line - 1));
    }
    const preservedWarningMarkers = rows().filter(row => row.severity === vscode.DiagnosticSeverity.Warning).length;
    assert.equal(document.isDirty, false);
    if (!singleRerun) assert.ok(vscode.window.activeTextEditor.document.getText().includes(`Baseline native status: ${fixture.errorNativeStatus}`));
    assert.ok(vscode.window.activeTextEditor.document.getText().includes(`${singleRerun ? "Candidate native status" : "Candidate native status"}: ${fixture.correctedNativeStatus}`));
    await reopen(singleRerun ? badAttempt : pairAttempt, findingChoice(singleRerun ? "candidate" : "baseline"), label("Read exact retained source"));
    baselineView = vscode.window.activeTextEditor.document.getText(); assert.equal(baselineView, bad);
    assert.equal(rows().length, singleRerun ? 0 : currentSourceFindings.length);
    assert.ok(rows().every(row => row.range.start.line !== savedSource.line - 1 && !row.message.includes(savedSource.message)),
      "Baseline errors must not decorate the corrected source");
    if (!singleRerun) for (const finding of currentSourceFindings) assert.ok(
      rows().some(row => row.message === findingLabel(pair, finding) && row.range.start.line === finding.location.start.line - 1),
      "Reading baseline source must preserve current candidate diagnostics");
    const count = (await call("history")).attempts.length;
    assert.equal(count, initialAttempts + 2); assert.equal(consentCount, 2);
    await reopen(pairAttempt, label("Read native outcomes, expectations and limitations"));
    assert.equal((await call("history")).attempts.length, count); assert.equal(consentCount, 2); assert.deepEqual(errors, []);
    assert.deepEqual(inputPrompts, [], "Prepared Core setup must eliminate routine input prompts");
    assert.equal((await call("setup-status", { context: fixture.options.context })).setup_id, fixture.setupId);
    return { cases: ["AMPF-A17", "AMPF-A20", ...(diagnosticsOnly ? ["AMPF-A24"] : [])], editorHost: "real-vscode", dialogs: "scripted-answers",
      nativeWorkers: singleRerun ? 2 : 3, comparisonMode: singleRerun ? "single-rerun" : "paired", completeSavedProgram: true, savedInputFiles: fixture.savedInputFiles,
      inFlightEdit: observedStart, currentLine: savedSource.line, sourcePath: savedSource.path,
      routineSetupReused: true, inputPrompts, storageContinuations,
      preservedWarningMarkers,
      dirtySuppressed: true, retainedReadOnly: true, staleSourceReadable: true, baselineNotDecorated: singleRerun ? null : true,
      expectationChecks: candidate.result.expectations.checks?.length ?? null, findingPages, uiPageLoads,
      expectations: candidate.result.expectations.status, diagnosticsOnly, qualification: false,
      evidence: { recipeObservation, machineObservation, badAttempt, pairAttempt, session: fixture.session, sourceErrorResult: badResult.id, pairedResult: pair.id } };
  } finally {
    await edit(original, true);
    for (const subscription of subscriptions.reverse()) subscription.dispose();
  }
}
module.exports = { run };
