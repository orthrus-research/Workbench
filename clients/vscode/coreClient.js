"use strict";

function discoverExecutable(configured, environment = process.env) {
  for (const candidate of [configured, environment.WORKBENCH_EXECUTABLE, "workbench"]) {
    if (typeof candidate === "string" && candidate.trim() && !candidate.includes("\0")) {
      return candidate.trim();
    }
  }
  return "workbench";
}

function scrubbedEnvironment(environment = process.env) {
  const allowed = [
    "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR",
    "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
    "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
    "WSLENV", "WSL_DISTRO_NAME", "WSL_INTEROP",
    "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "WORKBENCH_STATE_ROOT",
    "WORKBENCH_CLEANROOM_FIXTURE_GRADLEW",
    "WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME",
  ];
  const result = {};
  for (const key of allowed) {
    if (typeof environment[key] === "string") {
      result[key] = environment[key];
    }
  }
  return result;
}

module.exports = {
  discoverExecutable,
  scrubbedEnvironment,
};
