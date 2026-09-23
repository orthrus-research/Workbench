import java.security.MessageDigest
import java.util.HexFormat

plugins {
    `java-library`
    application
}

group = "research.orthrus.workbench"
version = "0.1.0"

repositories { mavenCentral() }

dependencies {
    // The parser version used by pack-pinned GroovyScript 1.4.3, not Gradle's Groovy.
    implementation("org.apache.groovy:groovy:4.0.30")
    implementation("org.tomlj:tomlj:1.1.1")
    // The selected Cleanroom platform's native collection implementation.
    implementation("it.unimi.dsi:fastutil:8.5.18")
    implementation("org.apache.commons:commons-lang3:3.20.0") { isTransitive = false }
    // Exact selected Cleanroom event libraries, including native listener factories.
    implementation("com.google.guava:guava:33.6.0-jre") { isTransitive = false }
    implementation("com.google.guava:failureaccess:1.0.2") { isTransitive = false }
    implementation("org.jspecify:jspecify:1.0.0") { isTransitive = false }
    implementation("org.ow2.asm:asm:9.10.1") { isTransitive = false }
    implementation("org.ow2.asm:asm-tree:9.10.1") { isTransitive = false }
    implementation("org.apache.logging.log4j:log4j-api:2.26.0") { isTransitive = false }
    implementation("org.apache.logging.log4j:log4j-core:2.26.0") { isTransitive = false }
    compileOnly("com.google.code.findbugs:jsr305:3.0.2")
    testImplementation("org.junit.jupiter:junit-jupiter:5.12.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.12.2")
}

dependencyLocking { lockAllConfigurations() }
java { withSourcesJar() }
check(JavaVersion.current() == JavaVersion.VERSION_25) { "Build Axiom using the profile-selected JDK 25; use tools/build_axiom.py" }
tasks.withType<JavaCompile>().configureEach {
    options.release = 25
    options.encoding = "UTF-8"
}
tasks.withType<AbstractArchiveTask>().configureEach {
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}
tasks.test {
    useJUnitPlatform()
    systemProperty("axiom.test.runtimeClasspath", sourceSets.test.get().runtimeClasspath.asPath)
    mapOf(
        "AXIOM_TEST_SANDBOX_BACKEND" to "axiom.sandbox.backend",
        "AXIOM_TEST_SANDBOX_DOCKER" to "axiom.sandbox.docker",
        "AXIOM_TEST_SANDBOX_DOCKER_HOST" to "axiom.sandbox.dockerHost",
        "AXIOM_TEST_SANDBOX_IMAGE" to "axiom.sandbox.image",
        "AXIOM_TEST_SANDBOX_SESSION" to "axiom.sandbox.session",
        "AXIOM_TEST_SANDBOX_USER" to "axiom.sandbox.user",
        "AXIOM_TEST_SANDBOX_GROUPS" to "axiom.sandbox.groups",
    ).forEach { (environment, property) ->
        providers.environmentVariable(environment).orNull?.let { systemProperty(property, it) }
    }
    providers.environmentVariable("AXIOM_REGISTRY_TEST_ROOT").orNull?.let {
        systemProperty("axiom.test.registryRoot", it)
    }
}
sourceSets.test {
    resources.srcDir("../tests/fixtures")
}
tasks.processTestResources { from("../tests/oracles/PrefixConformance.java") }
tasks.processTestResources { from("../tests/oracles/ForgeRegistryConformance.java") }
tasks.processTestResources { from("../tests/oracles/FluidStackConformance.java") }
// Profile policy is a test input, not an engine-owned packaged runtime default.
tasks.processTestResources { from("../../../profiles/packs/supersymmetry/src/workbench_profile_supersymmetry/axiom-material-admission.json") }
application {
    mainClass = "research.orthrus.axiom.Main"
    applicationName = "axiom"
    // MVP uses the selected JVM's default resource ergonomics.
    applicationDefaultJvmArgs = emptyList()
}
tasks.processResources {
    from("../sources/supersymmetry.lock.json") { into("axiom") }
    from("../sources/rules.json") { into("axiom") }
    from("../sources/native-fluids.lock.json") { into("axiom") }
    from("../sources/cleanroom-events.lock.json") { into("axiom") }
    from("../sources/material-lifecycle.lock.json") { into("axiom") }
    from("../sources/material-construction.lock.json") { into("axiom") }
    from("../sources/native-prefixes.lock.json") { into("axiom") }
    from("../sources/native-forge-registries.lock.json") { into("axiom") }
    from("../sources/native-fluid-stacks.lock.json") { into("axiom") }
    from("../sources/native-identities.lock.json") { into("axiom") }
    from("../sources/material-catalog.lock.json") { into("axiom") }
    from("../sources/native-items.lock.json") { into("axiom") }
    from("../sources/material-items.lock.json") { into("axiom") }
    from("../sources/material-blocks.lock.json") { into("axiom") }
    from("../sources/material-ores.lock.json") { into("axiom") }
    from("../../../profiles/platforms/cleanroom/jvm-runtime.json") { into("axiom") }
    from("../../../profiles/platforms/cleanroom/jvm-runtime-windows-x64.json") { into("axiom") }
    from("../../../profiles/platforms/cleanroom/registry-runtime.json") { into("axiom") }
    from("../../../profiles/platforms/cleanroom/native-identity-runtime.json") { into("axiom") }
    from("src/nativeMaterials/java") { into("axiom/native-materials") }
    from("../LICENSE") { into("META-INF") }
    from("../NOTICE.md") { into("META-INF") }
    from("../sources/NOTICE.md") { into("META-INF/axiom") }
    from("../sources/licenses") { into("META-INF/axiom/licenses") }
    inputs.property("engineVersion", project.version.toString())
    doLast { destinationDir.resolve("axiom/version.txt").writeText(project.version.toString() + "\n") }
}
val engineVersion = project.version.toString()
val runtimeJars = configurations.runtimeClasspath
val engineJar = tasks.jar.flatMap { it.archiveFile }
val installationManifest = layout.buildDirectory.file("installation/engine-manifest.json")
val sealRuntime = tasks.register("sealRuntime") {
    dependsOn(tasks.jar)
    inputs.files(runtimeJars, engineJar)
    inputs.property("engineVersion", engineVersion)
    outputs.file(installationManifest)
    doLast {
        val jars = (runtimeJars.get().files + engineJar.get().asFile).sortedBy { it.name }.associate { file ->
            file.name to HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(file.readBytes()))
        }
        installationManifest.get().asFile.apply {
            parentFile.mkdirs()
            writeText(groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(mapOf(
                "schema" to "axiom.installation.v1", "component" to "workbench-axiom-engine",
                "version" to engineVersion, "mainClass" to "research.orthrus.axiom.Main", "jars" to jars
            ))) + "\n")
        }
    }
}
distributions.main {
    distributionBaseName = "workbench-axiom-engine"
    contents {
        from(sealRuntime)
        from(tasks.named("sourcesJar")) { into("sources") }
        from("../LICENSE", "../NOTICE.md")
        from("../sources/NOTICE.md") { rename { "UPSTREAM-NOTICE.md" } }
        from("../sources/licenses") { into("third-party") }
        from(provider { runtimeJars.get().files.filter { it.name.startsWith("guava-") }.flatMap {
            zipTree(it).matching { include("META-INF/LICENSE") }.files
        } }) { into("third-party/guava-failureaccess-jspecify") }
        for (artifact in listOf("log4j-api", "log4j-core")) {
            from(provider { runtimeJars.get().files.filter { it.name.startsWith("$artifact-") }.flatMap {
                zipTree(it).matching { include("META-INF/LICENSE", "META-INF/NOTICE") }.files
            } }) { into("third-party/$artifact") }
        }
        from(provider { runtimeJars.get().files.filter { it.name.startsWith("commons-lang3-") }.flatMap {
            zipTree(it).matching { include("META-INF/LICENSE.txt", "META-INF/NOTICE.txt") }.files
        } }) { into("third-party/commons-lang3") }
        from(provider { runtimeJars.get().files.filter { it.name.startsWith("groovy-") }.flatMap {
            zipTree(it).matching { include("META-INF/LICENSE", "META-INF/NOTICE") }.files
        } }) {
            into("third-party/groovy-and-tomlj")
        }
        from(provider { runtimeJars.get().files.filter { it.name.startsWith("checker-qual-") }.flatMap {
            zipTree(it).matching { include("META-INF/LICENSE.txt") }.files
        } }) {
            into("third-party/checker-qual")
        }
        from(provider { runtimeJars.get().files.filter { it.name.startsWith("groovy-") }.flatMap {
            zipTree(it).matching { include("META-INF/LICENSE") }.files
        } }) {
            into("third-party/fastutil")
        }
    }
}
