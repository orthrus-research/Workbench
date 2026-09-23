# Workbench for VS Code

[package.json](../../../../clients/vscode/package.json) owns the extension
version. Regenerate its package lock after changing it; the version checker
rejects lock drift. The candidate is `workbench-vscode-{version}.vsix`, tagged
`workbench-vscode/v{version}`. Core and other modules need not release with it.
