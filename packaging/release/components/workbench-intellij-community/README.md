# Workbench for IntelliJ IDEA Community

[build.gradle.kts](../../../../clients/intellij-community/build.gradle.kts)
owns the plugin version. Gradle generates the packaged plugin descriptor;
source `plugin.xml` does not duplicate it. The candidate is
`workbench-intellij-community-{version}.zip`, tagged
`workbench-intellij-community/v{version}`.
