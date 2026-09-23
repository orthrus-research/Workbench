"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  commandForCoreLaunch,
  installedWindowsWslMappingV1,
  parseWslUncPath,
  pathForCatalogLaunch,
  pathForCoreLaunch,
  resolveCoreLaunch,
} = require("../coreLaunch");

const environment = { SystemRoot: "C:\\Windows" };

test("Windows WSL core uses one exact no-shell transport argv", () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workbench\\workbench",
    { platform: "win32", environment },
  );
  assert.equal(launch.host, "windows-wsl");
  assert.equal(launch.executable, "C:\\Windows\\System32\\wsl.exe");
  assert.deepEqual(launch.prefixArguments, [
    "--distribution", "Ubuntu",
    "--cd", "/home/developer/workbench",
    "--exec", "/home/developer/workbench/workbench",
  ]);
  assert.deepEqual(commandForCoreLaunch(launch, ["--version", "--json"]), [
    ...launch.prefixArguments, "--version", "--json",
  ]);
});

test("installed parity observes the same Windows WSL path mapping", () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workbench\\workbench",
    { platform: "win32", environment },
  );
  assert.deepEqual(installedWindowsWslMappingV1(
    launch,
    "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace",
  ), {
    launch_kind: "windows-wsl",
    distribution: "Ubuntu",
    workspace_input: "\\\\wsl.localhost\\Ubuntu\\home\\developer\\workspace",
    workspace_argument: "/home/developer/workspace",
  });
  assert.throws(() => installedWindowsWslMappingV1(
    launch,
    "\\\\wsl.localhost\\Debian\\home\\developer\\workspace",
  ), /distribution/);
});

test("WSL path mapping rejects foreign distributions and local Windows paths", () => {
  const launch = resolveCoreLaunch(
    "//wsl.localhost/Ubuntu/home/developer/workbench/workbench",
    { platform: "win32", environment },
  );
  assert.equal(
    pathForCoreLaunch(
      "\\\\wsl.localhost\\Ubuntu\\home\\developer\\records\\plan.json",
      launch,
      "plan",
    ),
    "/home/developer/records/plan.json",
  );
  assert.throws(
    () => pathForCoreLaunch("//wsl.localhost/Debian/home/developer/plan.json", launch, "plan"),
    /configured WSL distribution/,
  );
  assert.throws(() => pathForCoreLaunch("C:\\records\\plan.json", launch, "plan"), /configured WSL distribution/);
  assert.throws(
    () => pathForCoreLaunch(" //wsl.localhost/Ubuntu/home/developer/plan.json", launch, "plan"),
    /configured WSL distribution/,
  );
});

test("catalog paths preserve normalized core-relative values through WSL", () => {
  const launch = resolveCoreLaunch(
    "//wsl.localhost/Ubuntu/home/developer/workbench/workbench",
    { platform: "win32", environment },
  );
  assert.equal(pathForCatalogLaunch(".", launch, "source"), ".");
  assert.equal(
    pathForCatalogLaunch("groovy/postInit/chemistry/Probe.groovy", launch, "changed"),
    "groovy/postInit/chemistry/Probe.groovy",
  );
  assert.equal(
    pathForCatalogLaunch("//wsl.localhost/Ubuntu/home/developer/source", launch, "source"),
    "/home/developer/source",
  );
  for (const value of ["../outside", "groovy/../outside", "./groovy", "/etc/passwd", "C:\\source", "groovy\\Probe.groovy", "groovy//Probe.groovy"]) {
    assert.throws(() => pathForCatalogLaunch(value, launch, "source"), /normalized core-relative path/);
  }
});

test("UNC parsing rejects traversal and unsafe distributions", () => {
  assert.equal(parseWslUncPath("//wsl.localhost/Ubuntu/home/dev/core").linuxPath, "/home/dev/core");
  assert.throws(() => parseWslUncPath("//wsl.localhost/Ubuntu/home/../core"), /normalized/);
  assert.equal(parseWslUncPath("//wsl.localhost/Ubuntu;whoami/home/dev/core"), undefined);
  assert.throws(
    () => resolveCoreLaunch("//wsl.localhost/Ubuntu;whoami/home/dev/core", { platform: "win32", environment }),
    /configured WSL core path/,
  );
});

test("core arguments preserve exact values", () => {
  const launch = resolveCoreLaunch("C:\\Workbench\\workbench.exe", { platform: "win32", environment });
  assert.deepEqual(commandForCoreLaunch(launch, ["", " leading", "trailing "]), [
    "", " leading", "trailing ",
  ]);
});

test("Windows WSL transport rejects a substitutable system root", () => {
  for (const SystemRoot of ["C:\\Windows\\..\\hijack", "C:\\Windows\\\\nested", "\\\\server\\share"] ) {
    assert.throws(
      () => resolveCoreLaunch("//wsl.localhost/Ubuntu/home/dev/core", {
        platform: "win32",
        environment: { SystemRoot },
      }),
      /Windows system root/,
    );
  }
});
