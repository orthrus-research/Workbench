"use strict";

const { invokeCoreJson } = require("./coreCommandClient");

const ACTIONS = new Set(["open", "test", "apply", "verify", "rollback", "recover"]);
const FORMAT = "workbench-current-context-feature-action-v1";

function currentContextFeatureArguments(action) {
  if (typeof action !== "string" || !ACTIONS.has(action)) {
    throw new Error("current Work Session feature action is unsupported");
  }
  return Object.freeze([
    "change", "material-fluid-recipe", action, "--json",
  ]);
}

/** Invoke the selected Work Session context without accepting owner IDs or paths. */
async function invokeCurrentContextFeatureAction(executable, action, options = {}) {
  const arguments_ = currentContextFeatureArguments(action);
  let invocation = null;
  const outcome = await invokeCoreJson(executable, arguments_, {
    ...options,
    label: `Workbench current-context ${action}`,
    observeInvocation: (value) => { invocation = value; },
  });
  if (invocation === null) {
    throw new Error("current Work Session feature action lacks process custody");
  }
  return Object.freeze({
    action,
    arguments: arguments_,
    format: FORMAT,
    invocation,
    outcome,
    schema_version: 1,
  });
}

module.exports = {
  ACTIONS,
  FORMAT,
  currentContextFeatureArguments,
  invokeCurrentContextFeatureAction,
};
