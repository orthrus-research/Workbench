"use strict";

const path = require("node:path");

const MAX_PATH_BYTES = 32 * 1024;
const DISTRIBUTION = /^[A-Za-z0-9._-]+$/;

function configuredValue(value, label) {
  if (typeof value !== "string" || !value.trim() || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > MAX_PATH_BYTES) {
    throw new Error(`${label} is invalid`);
  }
  return value.trim();
}

function exactValue(value, label, allowEmpty = false) {
  if (typeof value !== "string" || (!allowEmpty && !value) || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > MAX_PATH_BYTES) {
    throw new Error(`${label} is invalid`);
  }
  return value;
}

function linuxPath(value, label) {
  const normalized = exactValue(value, label).replaceAll("\\", "/");
  if (!normalized.startsWith("/") || normalized.startsWith("//")
      || normalized.endsWith("/")
      || normalized.split("/").some((part, index) => index > 0 && (!part || part === "." || part === ".."))) {
    throw new Error(`${label} is not one normalized absolute Linux path`);
  }
  return normalized;
}

function parseWslUncPath(value) {
  if (typeof value !== "string" || value.includes("\0")
      || Buffer.byteLength(value, "utf8") > MAX_PATH_BYTES) {
    return undefined;
  }
  const normalized = value.replaceAll("\\", "/");
  const match = /^\/\/(?:wsl(?:\.localhost)?|wsl\$)\/([^/]+)(\/.*)$/i.exec(normalized);
  if (!match || !DISTRIBUTION.test(match[1])) return undefined;
  return Object.freeze({
    distribution: match[1],
    linuxPath: linuxPath(match[2], "WSL path"),
  });
}

function looksLikeWslUncPath(value) {
  return typeof value === "string"
    && /^\/\/(?:wsl(?:\.localhost)?|wsl\$)\//i.test(value.replaceAll("\\", "/"));
}

function windowsSystemRoot(environment) {
  const root = configuredValue(environment.SystemRoot || environment.WINDIR || "", "Windows system root");
  if (!path.win32.isAbsolute(root) || !/^[A-Za-z]:[\\/]/.test(root)) {
    throw new Error("Windows system root is not one absolute local path");
  }
  const normalized = root.replaceAll("/", "\\").replace(/\\+$/, "");
  const components = normalized.slice(3).split("\\");
  if (!components.length || components.some((part) => !part || part === "." || part === "..")) {
    throw new Error("Windows system root is not one normalized absolute local path");
  }
  return normalized;
}

function resolveCoreLaunch(configured, options = {}) {
  const selected = configuredValue(configured, "core executable");
  const platform = options.platform || process.platform;
  const environment = options.environment || process.env;
  const wsl = platform === "win32" ? parseWslUncPath(selected) : undefined;
  if (platform === "win32" && !wsl && looksLikeWslUncPath(selected)) {
    throw new Error("configured WSL core path is invalid");
  }
  if (!wsl) {
    return Object.freeze({
      configured: selected,
      coreExecutable: selected,
      distribution: null,
      executable: selected,
      host: "native",
      prefixArguments: Object.freeze([]),
    });
  }
  const parent = wsl.linuxPath.slice(0, wsl.linuxPath.lastIndexOf("/")) || "/";
  return Object.freeze({
    configured: selected,
    coreExecutable: wsl.linuxPath,
    distribution: wsl.distribution,
    executable: path.win32.join(windowsSystemRoot(environment), "System32", "wsl.exe"),
    host: "windows-wsl",
    prefixArguments: Object.freeze([
      "--distribution", wsl.distribution,
      "--cd", parent,
      "--exec", wsl.linuxPath,
    ]),
  });
}

function pathForCoreLaunch(value, launch, label) {
  const selected = exactValue(value, label);
  if (launch.host === "native") return selected;
  const parsed = parseWslUncPath(selected);
  if (!parsed || parsed.distribution.toLowerCase() !== launch.distribution.toLowerCase()) {
    throw new Error(`${label} does not belong to the configured WSL distribution`);
  }
  return parsed.linuxPath;
}

function pathForCatalogLaunch(value, launch, label) {
  const selected = exactValue(value, label);
  if (launch.host === "native") return selected;
  const parsed = parseWslUncPath(selected);
  if (parsed) return pathForCoreLaunch(selected, launch, label);
  const components = selected.split("/");
  const relative = selected === "." || (
    !selected.startsWith("/")
    && !selected.includes("\\")
    && !/^[A-Za-z]:/.test(selected)
    && components.every((part) => part && part !== "." && part !== "..")
  );
  if (!relative) {
    throw new Error(
      `${label} must be a same-distribution WSL path or one normalized core-relative path`,
    );
  }
  return selected;
}

function commandForCoreLaunch(launch, arguments_) {
  if (!Array.isArray(arguments_)) {
    throw new Error("core arguments are invalid");
  }
  return [
    ...launch.prefixArguments,
    ...arguments_.map((value) => exactValue(value, "core argument", true)),
  ];
}

function installedWindowsWslMappingV1(launch, workspacePath) {
  if (!launch || launch.host !== "windows-wsl") {
    throw new Error("installed parity host observation requires the Windows/WSL adapter");
  }
  return Object.freeze({
    launch_kind: "windows-wsl",
    distribution: launch.distribution,
    workspace_input: exactValue(workspacePath, "installed parity workspace"),
    workspace_argument: pathForCoreLaunch(
      workspacePath, launch, "installed parity workspace",
    ),
  });
}

module.exports = {
  commandForCoreLaunch,
  installedWindowsWslMappingV1,
  parseWslUncPath,
  pathForCatalogLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
};
