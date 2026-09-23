import org.jetbrains.intellij.platform.gradle.IntelliJPlatformType
import org.jetbrains.intellij.platform.gradle.TestFrameworkType

plugins {
    java
    id("org.jetbrains.intellij.platform") version "2.18.1"
}

tasks.withType<org.gradle.api.tasks.bundling.AbstractArchiveTask>().configureEach {
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

group = "dev.cleanroommc.workbench"
version = "0.1.5"

repositories {
    mavenCentral()
    intellijPlatform {
        defaultRepositories()
        intellijDependencies()
    }
}

dependencyLocking {
    lockAllConfigurations()
}

val localIdePath = providers.gradleProperty("intellijPlatformPath").orNull

dependencies {
    intellijPlatform {
        if (localIdePath == null) {
            intellijIdea("2026.2.0.1")
        } else {
            local(localIdePath)
        }
        testFramework(TestFrameworkType.Platform)
    }
    testImplementation("org.junit.jupiter:junit-jupiter:5.13.4")
    testImplementation("junit:junit:4.13.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.13.4")
    testRuntimeOnly("org.junit.vintage:junit-vintage-engine:5.13.4")
}

java {
    sourceCompatibility = JavaVersion.VERSION_21
    targetCompatibility = JavaVersion.VERSION_21
}

intellijPlatform {
    pluginConfiguration {
        ideaVersion {
            sinceBuild = "262"
            untilBuild = "262.*"
        }
    }
    buildSearchableOptions = false
    pluginVerification {
        ides {
            create(IntelliJPlatformType.IntellijIdea, "2026.2.1")
        }
    }
}

tasks.withType<JavaCompile>().configureEach {
    options.encoding = "UTF-8"
    options.release = 21
}

tasks.test {
    useJUnitPlatform()
    // IntelliJ's JUnit3 platform fixtures treat JUnit4 assumptions as failures.
    // Native acceptance is explicit opt-in; keep ordinary platform tests runnable
    // without local Core/JVM/runtime inputs instead of reporting false skips.
    if (!providers.environmentVariable("WORKBENCH_TEST_MATERIAL_ACTION_FIXTURE").isPresent) {
        filter.excludeTestsMatching("*MaterialChecksActionTest")
    }
    if (!providers.environmentVariable("WORKBENCH_TEST_MATERIAL_IDEA_FIXTURE").isPresent) {
        filter.excludeTestsMatching("*MaterialChecksPlatformTest.testInstalledNativeProgramThroughRealPlatformViews")
    }
    if (!providers.environmentVariable("WORKBENCH_TEST_MATERIAL_HISTORY_FIXTURE").isPresent) {
        filter.excludeTestsMatching("*MaterialChecksPlatformTest.testInstalledRetainedHistoryReader")
    }
    if (!providers.environmentVariable("WORKBENCH_TEST_ATLAS_NATIVE_CORPUS").isPresent) {
        filter.excludeTestsMatching("*AtlasRecipeImpactPanelPlatformTest")
    }
}

tasks.processResources {
    from("../../LICENSE") {
        into("META-INF")
    }
    from("../../NOTICE.md") {
        into("META-INF")
    }
}

tasks.register("unitTest") {
    group = "verification"
    description = "Runs the installed-core developer client tests."
    dependsOn(tasks.test)
}

tasks.register<JavaExec>("communityFeatureServiceProbe") {
    group = "verification"
    description = "Loads the packaged Community client and reopens one exact Service V3 job."
    val packagedJar = providers.gradleProperty("communityFeatureServicePackagedJar")
    classpath(files(packagedJar), configurations.runtimeClasspath)
    mainClass = "dev.cleanroommc.workbench.intellij.community.CommunityFeatureServiceProbe"
    providers.gradleProperty("communityFeatureServiceProbeArguments").orNull?.let {
        args(it.split('\u001f'))
    }
}
