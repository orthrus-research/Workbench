"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { resolveCoreLaunch } = require("../coreLaunch");
const { recipeReviewArguments } = require("../recipeReviewClient");

test("builds the direct read-only PR recipe review with an explicit target ref", () => {
  const launch = resolveCoreLaunch("/opt/workbench/bin/workbench", { platform: "linux" });
  assert.deepEqual(recipeReviewArguments({
    baseline: "refs/remotes/origin/pr-target",
    baselineMode: "pr-base",
    source: "/work/Supersymmetry",
  }, launch), [
    "review", "recipes",
    "--profile", "supersymmetry",
    "--pr-base", "refs/remotes/origin/pr-target",
    "--source", "/work/Supersymmetry",
    "--side", "dedicated-server",
  ]);
});

test("maps directory inputs but preserves Git refs across the WSL adapter", () => {
  const launch = resolveCoreLaunch(
    "\\\\wsl.localhost\\Ubuntu\\opt\\workbench\\workbench",
    { platform: "win32", environment: { SystemRoot: "C:\\Windows" } },
  );
  assert.deepEqual(recipeReviewArguments({
    baseline: "feature/base",
    baselineMode: "ref",
    source: "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry",
  }, launch), [
    "review", "recipes",
    "--profile", "supersymmetry",
    "--baseline-ref", "feature/base",
    "--source", "/work/Supersymmetry",
    "--side", "dedicated-server",
  ]);
  assert.throws(() => recipeReviewArguments({
    baseline: "\\\\wsl.localhost\\Debian\\work\\baseline",
    baselineMode: "directory",
    source: "\\\\wsl.localhost\\Ubuntu\\work\\Supersymmetry",
  }, launch), /distribution/);
});

test("rejects unsupported baseline modes and sides", () => {
  const launch = resolveCoreLaunch("workbench");
  assert.throws(() => recipeReviewArguments({
    baseline: "main", baselineMode: "remote", source: "/work/project",
  }, launch), /baseline mode/);
  assert.throws(() => recipeReviewArguments({
    baseline: "main", baselineMode: "ref", side: "both", source: "/work/project",
  }, launch), /side/);
});
