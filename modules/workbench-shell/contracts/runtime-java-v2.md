# Workbench Java runtime provision V2

Status: experimental executable contract

## Cleanroom policy

Cleanroom documents Java 25 as its latest tested and recommended Java major.
It recommends Zulu or Eclipse Temurin, but does not publish a supported
`25.0.x` patch matrix.

The provisional Workbench profile therefore makes two separate claims:

- Cleanroom compatibility policy requires Java 25; and
- the current reproducible Workbench candidate is exact Eclipse Temurin
  `25.0.4+7` GA.

Changing the candidate is an explicit profile edit. Provisioning never follows
an unrecorded `latest` result.

## Command and runtime selection

```bash
python3 -m workbench_shell runtime-java
```

The command is an explicit local mutation. Read-only inspection and
`runtime/plan` never download Java.

An external candidate may be nominated only by the active configuration's
`java_candidate_home` binding (normally imported from
`WORKBENCH_JAVA_HOME`). An explicit internal API candidate uses the same
validation path. Managed execution does not implicitly adopt `JAVA_HOME`,
`JDK_HOME`, or `java` from `PATH`; ambient discovery remains available only
to read-only host diagnostics.

A candidate is reusable only when executing it proves the exact
profile-configured Temurin runtime, Eclipse Adoptium vendor, Java architecture,
and successful exit. An older Java, another Java 25 patch, or another
distribution is rejected as incompatible. A selected external runtime returns
`workbench-java-runtime-result-v1`, schema version `1`; that result identifies
external discovery and is not a managed-runtime receipt version.

## Managed provisioning

If no exact external candidate exists, Workbench:

1. maps the current OS and architecture to Adoptium identifiers;
2. queries the official Adoptium GA release API;
3. finds the exact profile-selected release rather than accepting the first
   response;
4. retains the returned release, source, package URL, byte size, and SHA-256;
5. downloads into the shared content-addressed artifact cache;
6. safely extracts the platform archive into staging;
7. locates and executes the extracted Java;
8. verifies version, vendor, architecture, and reported Java home;
9. applies any receipt-bound portability transform;
10. computes a deterministic digest over files, directories, safe internal
    symlinks, permissions, and bytes; and
11. atomically publishes the runtime and receipt.

The managed result format is `workbench-java-runtime-result-v2`, schema
version `2`. Its receipt is `workbench-java-runtime-receipt-v2`, schema version
`2`, stored at `receipts/java-runtime-v2.json`:

```text
.workbench/
  artifacts/sha256/<archive-sha256>
  jdks/eclipse-temurin/<version>/<os>-<arch>/<policy-prefix>/
    extracted/
    receipts/java-runtime-v2.json
```

The receipt binds the platform profile and Java policy, resolved host, exact
Adoptium metadata, archive identity, verified Java properties, extracted-tree
identity, target URIs, execution paths, and portability state. The runtime ID
is SHA-256 over the receipt format, policy, host, exact asset, tree digest, and
portability record.

## Custody and execution paths

`target.root_uri`, `target.custody_java_home_uri`, and
`target.custody_java_uri` name the exact tree retained under Workbench state.
`target.java_home_uri` and `target.java_uri` name the lexical path that must be
handed to the process. The `execution` record repeats and classifies those
execution URIs.

On hosts where custody paths are directly executable, `execution.kind` is
`canonical-path`. On Windows, Workbench uses `windows-short-path` when the
nearest existing target ancestor has an exact ASCII DOS alias. This works
around a Temurin launcher failure that otherwise reports a missing `java.dll`
when its own path contains characters outside the active Windows code page.
Consumers must not resolve the execution URI before spawning Java.

If Windows exposes no usable ASCII DOS short path, the read-only setup check
fails where the existing path permits that fact to be observed. Workbench does
not create an external junction or mutate an alias root. The error requires an
ASCII `--state-root`; provisioning also repeats this preflight before it
downloads or extracts Java.

Temurin's vendor-supplied Windows class-data-sharing (CDS) archives can retain
the canonical Unicode module path even when Java is launched through the ASCII
DOS alias. On an ANSI console this makes an otherwise usable JVM emit a missing
`lib/modules` warning on every launch, and regenerating the archive fails for
the same reason. When and only when execution is `windows-short-path`,
Workbench therefore removes the vendor `bin/server/classes*.jsa` archives in
staging. Java's default `-Xshare:auto` behavior then runs without CDS or a
warning. The exact removed archive paths, sizes, and SHA-256 digests are stored
under `portability.cds`, together with the stable transform ID
`workbench-java-portability:windows-unicode-default-cds-removal-v1`; the
transformed tree and portability record are both bound into `runtime_id`.
Canonical-path runtimes retain the vendor archives and record no transform ID.

The same transform ID is present in the reviewed setup plan. The plan explains
the optional startup-cache deletion in plain language, and apply rejects a
runtime receipt whose transform differs from the reviewed action.

## Reuse and stale state

Reuse re-probes Java, rehashes the canonical tree, and verifies the runtime ID
before trusting an execution alias. The alias must resolve to the receipt's
exact custody home and executable. The probe uses the lexical execution URI
with `JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`, and `JDK_JAVA_OPTIONS` removed
case-insensitively from the child environment. Required Windows environment
state is otherwise retained.

Reuse also verifies that the CDS portability state matches the execution kind,
that removal records are canonical and confined to the selected Java home's
`bin/server` directory, and that removed archives have not reappeared. A Java
probe that reports a CDS warning is a hard failure. A modified receipt,
executable, file, permission, symlink, portability record, or unrecorded
top-level entry is also a hard failure.

V2 is the only managed-runtime receipt Workbench reads. An existing managed
target without its exact V2 receipt is stale state and must be removed and
provisioned again; Workbench does not relabel or migrate retained runtime bytes.
Saved managed-Java selections reopen the V2 receipt and tree. They are not
reclassified as external Java candidates merely because the execution home is
exported through `WORKBENCH_JAVA_HOME`.

Workbench does not modify system `JAVA_HOME`, system `PATH`, a package manager,
Prism, or MultiMC. Selecting the retained Java path in a launcher is a later
runtime integration step.

## Primary references

- [Cleanroom JVM guidance](https://cleanroommc.com/wiki/end-user-guide/args)
- [Cleanroom client installation](https://cleanroommc.com/wiki/end-user-guide/installation/install-client)
- [Adoptium API for CI/CD](https://adoptium.net/installation/ci-scripts)
- [Adoptium archive installation](https://adoptium.net/installation/archives)
- [Temurin releases](https://adoptium.net/temurin/releases)
