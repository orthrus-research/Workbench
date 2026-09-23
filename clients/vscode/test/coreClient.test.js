"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { discoverExecutable, scrubbedEnvironment } = require("../coreClient");

test("discovers one explicitly configured executable", () => {
  assert.equal(
    discoverExecutable("/opt/workbench", { WORKBENCH_EXECUTABLE: "/env/workbench" }),
    "/opt/workbench",
  );
  assert.equal(
    discoverExecutable("", { WORKBENCH_EXECUTABLE: "/env/workbench" }),
    "/env/workbench",
  );
  assert.equal(discoverExecutable("", {}), "workbench");
});

test("scrubs interpreter injection from developer command environments", () => {
  assert.deepEqual(scrubbedEnvironment({
    HOME: "/home/me",
    NODE_OPTIONS: "--require=/evil",
    PATH: "/bin",
    PYTHONPATH: "/evil",
    WORKBENCH_CLEANROOM_FIXTURE_GRADLEW: "/opt/gradle/bin/gradle",
    WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME: "/opt/jdk",
    WORKBENCH_STATE_ROOT: "/var/lib/workbench-state",
  }), {
    PATH: "/bin",
    HOME: "/home/me",
    WORKBENCH_CLEANROOM_FIXTURE_GRADLEW: "/opt/gradle/bin/gradle",
    WORKBENCH_CLEANROOM_FIXTURE_JAVA_HOME: "/opt/jdk",
    WORKBENCH_STATE_ROOT: "/var/lib/workbench-state",
  });
});
