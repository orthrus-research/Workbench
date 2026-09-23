"use strict";
const { invokeCoreJson } = require("./coreCommandClient");

// An IDE preference pointing to the existing Work Session, not another store.
const sessions = new Map();
function rememberContext(root, session) { sessions.set(root, session); }
async function selectContext(vscode, executable, root) {
  const current = sessions.get(root);
  const options = [...(current ? ["Use selected Work Session"] : []), "Create context for this workspace", "Use existing Work Session"];
  const mode = await vscode.window.showQuickPick(options, { title: "Developer context" });
  if (!mode) return;
  if (mode === "Use selected Work Session") return current;
  let session;
  if (mode.startsWith("Create")) {
    const pack = await vscode.window.showInputBox({ title: "Pack profile", value: "supersymmetry" }); if (!pack) return;
    const platform = await vscode.window.showInputBox({ title: "Platform profile", value: "cleanroom" }); if (!platform) return;
    const variant = await vscode.window.showInputBox({ title: "Profile variant", value: "cleanroom-provisional" }); if (!variant) return;
    const response = await invokeCoreJson(executable, ["context", "select", root, `--pack-profile=${pack}`, `--platform-profile=${platform}`, `--variant=${variant}`], { cwd: root });
    session = response.session_id;
  } else session = await vscode.window.showInputBox({ title: "Exact Work Session ID" });
  if (session) rememberContext(root, session);
  return session;
}
module.exports = { selectContext, rememberContext };
