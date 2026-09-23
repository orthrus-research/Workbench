# Workbench support

## Workbench CLI not found

Open the Command Palette and run **Workbench: Check CLI Installation**. The
extension first uses the path in `workbench.coreExecutable`, then
`WORKBENCH_EXECUTABLE`, then `workbench` on `PATH`. Use **Workbench: Configure
Core Executable** when the CLI is installed somewhere else.

## Setup or dependency problem

Run **Workbench: Set Up or Repair Environment**. Setup checks the selected
environment and asks before installing anything. Copy the complete terminal
error when reporting a problem; do not include credentials or private project
content.

## Recipe Review problem

For **Review Pull Request Recipes**, confirm that the source is a Supersymmetry
checkout and that the provider observation can reach GitHub. Planning is
read-only. Only after **Prepare and Review** consent does Workbench fetch the
exact provider-recorded base and head into Workbench-owned refs; it does not
switch branches or update the candidate checkout, index, or user-owned refs.

The advanced folder, exact-ref, and local PR-target comparisons use only local
folders and Git objects. They never fetch a missing or stale branch, so confirm
that the selected target or ref exists locally.

## Reporting a bug

Use the [Workbench issue
tracker](https://github.com/orthrus-research/workbench/issues). Include the
Workbench version, VS Code version, operating system, exact command that failed,
and a redacted error message. Use the repository's private
vulnerability-reporting route for suspected security issues; never include
vulnerability details in a public issue.
