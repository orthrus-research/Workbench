# Checks on developer-authored saved changes

Edit in your IDE, save, review the source change, then explicitly run a check.
No Blueprint, generated construction plan, source application or rollback is
required. The existing Work Session supplies the selected checkout and profiles.
Unsaved buffers are never silently saved or treated as executed source.

Both native IDE clients expose **Workbench: Run Checks on Saved Changes**.
Choose a runtime image, inspect the exact candidate and side effects, and confirm
execution. Results provide source-bound findings, read-only logs and a separate
cleanup state. You can continue editing during execution: the run stays bound to
its captured candidate, and historical findings do not decorate newer bytes.
Reopen an exact attempt to inspect retained evidence or explicitly recover its
interrupted processes and disposable files. No new dashboard is required.

## First check and its limits

`supersymmetry.client-cold-start` supports the `cleanroom-provisional` variant on
native Linux. It projects saved `groovy/`, `config/`, `resources/` and `scripts/`
files into a disposable client, including additions, deletions and executable
modes. It instruments only that copy with a nonce-bound postInit observation,
then stops the owned process group after the probe and FML loaded checkpoint,
or after a profile-confirmed compiler, environment or startup failure. A client
left open on a crash/error screen is not presumed to be still starting.
The observation probe serializes with the platform's pinned Gson; it does not
require the optional `groovy-json` module absent from the GroovyScript runtime.

The result separates client-startup observations, Groovy compiler/log findings,
item/fluid registration totals, process closure and recoverable cleanup.
`completed` means the declared checkpoint was observed with complete bounded
logs, no blocking failures and no unresolved error diagnostics. It does **not** prove every source file ran, that a
specific edited recipe registered correctly, gameplay, hot reload, dedicated
server parity, constituent-mod builds or release qualification. Missing or
over-bound evidence is inconclusive unless available evidence already proves a
failure; cancellation and timeout are not passes.

## Startup observations and actionable diagnostics

Profiles return the owner-neutral `workbench-check-observation-v1` contract from
`observe`: `running`, `checkpoint` or `failure`, a named stage, a concise summary
and exact log/line evidence. Core validates references against captured logs,
retains bounded request-bound progress records, and closes the owned process
group for terminal observations. `failure-observed` is distinct from user
cancellation. Crucible derives the final outcome from execution and final
classified evidence; a transient success observation cannot hide a later failure.

The current result metadata format is `workbench-saved-check-result-v4`, with
`workbench-saved-check-request-v4` requests, including an explicit sealed
attachment (or `null` for snapshot-only checks).
Superseded formats and success-only provider interfaces have no compatibility
adapter. New retained results can be reopened without their profile installed;
progress, cancellation and recovery likewise require no profile import. The
existing Work Session remains the only session-selection owner.

Both IDEs poll lightweight retained progress without recapturing source or
granting execution authority. Loss of progress transport does not cancel the
independently supervised check. A finished progress response is not a pass;
clients obtain the sealed result separately.

Diagnostics separate compiler, runtime, environment, informational and
unclassified events. Only confirmed blocking failures stop the run early.
Unknown error events remain visible and make acceptance inconclusive; they do
not become compiler failures. The exact Mouse Tweaks successful-initialization
message emitted at FATAL level has an explained nonblocking rule, not a blanket
logger exemption. Mirrored events retain all evidence references; genuinely
repeated occurrences remain separate. Truncated diagnostic lists cannot pass.

The profile declares required logs plus bounded optional `crash-reports/*.txt`
and `logs/cleanmix.log` evidence. Core copies those reports before recoverable
cleanup. File-pattern collection is limited to one declared directory, rejects
links and traversal, and admits at most 32 files per pattern. Each captured file
has the existing 16 MiB limit. Missing optional reports are normal; present but
unreadable, oversized or unclassified reports cannot silently grant acceptance.

Compiler navigation uses exact captured relative paths or absolute paths/file
URLs within the disposable projection; basename guesses are rejected. The
compiler's reported line/column are retained separately from a verified line
range or insertion point. Empty files, blank lines and EOF are valid zero-width
locations. An implicit compiler line after a file without a final newline is
explicitly identified as an EOF anchor, not an invented source line. Byte hashes
and one-based UTF-16 coordinates protect navigation across spaces, Unicode and
CRLF. Neither IDE attaches historical diagnostics to changed or unsaved bytes.

Implementation tests use a real supervised native fixture process. They are not
a successful Minecraft run. A real native Supersymmetry client validation remains
required before advertising this runtime path as qualified.

## Environment provenance and diagnostic groups

Check confirmation and retained results identify the captured pack version,
Git revision and saved working-tree changes, exact dependency filenames/hashes,
selected platform and observed JDK release, image identity, host and policy.
Local tags are explicitly unverified labels, not proof of an official release
or the latest upstream version. No network update or Git checkout is implicit.
The host and admitted environment are rechecked before launch; runtime graphics
observations are retained separately. Missing hardware/network equivalence is
not silently asserted.

Both IDEs show diagnostic groups with occurrence counts, guidance and every
original finding/evidence reference. Grouping does not suppress repetitions or
alter severity. The profile identifies exact ForgeMicroBlockCBE duplicate
microblock-material events separately from GregTech materials, and FML recipe
JSON loading failures by recipe identifier and exception. A reporting logger or
recipe namespace alone does not prove the responsible source/dependency.
Unknown ownership and unresolved error outcomes remain explicit.

## Compare two retained runs

Choose **Compare with a retained reference run** from a check result in either
IDE. Select a reference from bounded history or enter its exact attempt ID.
This is an observation reference, not an accepted/qualified baseline. Inspection
and source/log navigation remain in the existing check UI.

Crucible creates a separate `workbench-check-comparison-v2` record referencing
both sealed results; Shell links it to the existing Work Session. Comparison
reopens and verifies both saved candidates, results and retained evidence. It
requires no installed profile and never launches, changes source, rewrites a
result or grants qualification. Repeating the same comparison reopens the same
record. Changed evidence is rejected rather than silently reinterpreted.

Comparison requires matching selection, image/dependencies/toolchain, provider
policy, check definition, timeout, host/admitted environment and observed
runtime environment. Cancelled, timed-out, truncated, incomplete or unclosed
runs are not comparable. Source bytes and Git revisions may differ: they are
the developer's candidate change, not environment identity.

Groups report `newly-observed`, `persistent`, `occurrence-count-changed`,
`no-longer-observed` or `not-comparable`. A finding can be called absent only if
the side lacking it reached the full checkpoint. Changed counts require both
checkpoints. An early failed run cannot make later reference findings appear
resolved; equally observed findings may still be compared in a partial result.
Identities normalize only the disposable runtime root, not arbitrary numbers,
identifiers, exceptions or source positions. Conservative identity changes may
appear as separate groups; this is not causal attribution.

The comparison states `compared`, `partial` and `not-comparable` are **not pass
states**. Both original outcomes remain visible. Unchanged baseline errors stay
errors and unknowns stay inconclusive; no global allowlist or accepted-error
ledger is introduced. Graphics/host observations are bounded, not exhaustive
hardware or network equivalence. No diagnostic delta proves recipe correctness.

Older request/result formats have no adapter or automatic migration. Their files
remain intact as historical evidence; create new runs for current comparisons.
History reports excluded older formats and any results omitted by its display
bound. Exact attempt selection remains available.

## Recipe-specific expectations on saved edits

Choose **Run a recipe check on saved changes** in the existing IDE action, or
use `checks recipes --path groovy/postInit/chemistry/Probe.groovy` to inspect
literal source candidates. Select the exact returned recipe ID when preparing:

```bash
workbench context run SESSION_ID -- checks recipes --path groovy/postInit/chemistry/Probe.groovy
workbench context run SESSION_ID -- checks prepare --image IMAGE_ID --recipe RECIPE_ID --timeout 600
workbench context run SESSION_ID -- checks execute ATTEMPT_ID --confirm REQUEST_ID
```

The first family is ordinary `MIXER` / `Recipemaps.MIXER` builder chains with
literal item, metaitem, ore or fluid inputs; fixed item/fluid outputs; explicit
duration and positive EU/t (including known `VA[tier]` constants). Arbitrary
Groovy is never evaluated on the host. Conditional or generated registrations,
closures, local declarations, NBT, wildcard stacks, circuits, nonconsumables,
chance outputs and custom builder semantics remain explicitly unsupported.
Unsupported expectations can be inspected but cannot launch an assertion run.

Preparation seals the expected recipe, exact source location and saved bytes.
The installed profile adds an observation-only script to the disposable copy.
After FML load completion, it independently reads actual recipe fields,
category membership, active lookup membership and bounded input lookups. It
receives identifiers and input queries, not expected output quantities, duration
or EU/t. Ore representatives are resolved from the runtime and expanded with
explicit bounds. Lookup uses the captured inputs and an explicit maximum-voltage
limit; this is **not** a powered machine or gameplay simulation.

`result.assertions.state` is separate from startup `result.state`:

- `matched`: the complete bounded observation satisfies the expectation.
- `mismatched`: complete evidence disagrees with at least one expected fact.
- `inconclusive`: missing/ambiguous capture, early failure, cancellation, timeout,
  incomplete logs or unverified process closure prevents a conclusion.
- `unsupported`: selected or observed semantics are outside the supported family.

Presence requires one exact registration, no competing accepting registrations,
and the expected recipe winning each bounded lookup. Duplicate occurrences stay
separate; multiple exact registrations are ambiguous, not arbitrarily paired to
source. Missing capture is never proof of absence. Existing unresolved startup
errors remain visible and inconclusive even when an independent recipe assertion
matches; assertions grant no startup or publication qualification.

For an old expected recipe or a removal, explicitly select a retained source:

```bash
workbench context run SESSION_ID -- checks recipes --reference REFERENCE_ATTEMPT_ID --path groovy/postInit/chemistry/Probe.groovy
workbench context run SESSION_ID -- checks prepare --image IMAGE_ID --recipe REFERENCE_RECIPE_ID --recipe-reference REFERENCE_ATTEMPT_ID
workbench context run SESSION_ID -- checks prepare --image IMAGE_ID --recipe REFERENCE_RECIPE_ID --recipe-reference REFERENCE_ATTEMPT_ID --absent
```

The first preparation checks the old expectation against current saved source;
the second asserts the old recipe is absent from both the captured registrations
and effective lookup. Absence does not claim no other recipe accepts those inputs.
Retained source and evidence are verified on reopen without importing the profile.
Source navigation refuses changed or unsaved bytes.

First compare two independently executed unchanged runs (A/A). Then compare the
edited candidate (A/B) using the existing `checks compare` command or IDE action.
Recipe comparisons require the same input selector, resolved identities and
environment, preserve multiset cardinality, and report `unchanged`, `changed` or
`not-comparable`. They do not infer causal source attribution or identify one
duplicate instance across runs. Input-selector changes require a new observation
reference; recipe signatures are semantic descriptions, not durable instance IDs.

## Explaining a recipe observation

Select **Recipe assertion — explain expected and observed** in either IDE.
Read the plain-text explanation, then inspect individual registrations or input
lookups and navigate to byte-verified source candidates or captured logs. Raw
assertion JSON remains available as secondary evidence. The CLI exposes the same
retained structure and rendered text in its ordinary JSON envelope:

```bash
workbench context run SESSION_ID -- checks explain ATTEMPT_ID
```

The Supersymmetry profile explains duration, EU/t and ingredient/output quantity
differences against explicitly observed registrations and selected recipes. It
does not pick the nearest recipe or infer a causal modification. Repeated
ingredient/output quantities and duplicate registrations retain multiplicity.
An absence expectation is distinct from a lookup returning no recipe: another
recipe may still accept those inputs.

The versioned capture `workbench-recipe-capture-v4` supplies observation-local
registration references and each query's accepting references and winner. The
observer validates winner membership and bounds total acceptance links at 16384.
References are never cross-run identities; comparisons use semantic multisets,
including per-query accepting signatures. Old captures are not upgraded.

Crucible validates and retains `workbench-check-explanation-v1` under assertion
observation details. Its structured sections and deterministic text are sealed
together. Reopen verifies evidence and source bytes without importing the
profile; viewing explanations does not execute a runtime or modify source.
The explanation and lifecycle are separately versioned extensions within the
open observation-details contract; current requests explicitly bind attachment custody.

Source links distinguish the selected expected declaration from matching literal
declarations in the checked source. Literal items, ore names and fluid names are
compared as declarations; metaitem aliases require a captured runtime resolution.
Even a unique matching declaration is not a verified runtime origin. Source
inspection is bounded to 8 MiB and 4096 supported postInit declarations. Dynamic,
unsupported and unresolved declarations remain excluded with explicit coverage
notes. No match means unattributed within that inspected subset, not proof that
no source exists. Registration details and source links each have a 64-entry
presentation budget; the 2 MiB explanation bound reports omissions explicitly.
These presentation limits never convert missing evidence into absence.

Mismatch annotations attach only to the selected expectation when it belongs to
the checked source candidate and the editor's exact saved bytes are still current.
Retained expectations are not rebound to changed files. Snapshot-only lookup
evidence cannot establish registration execution, rejection, overwrite or removal
history. Startup, assertion and lifecycle coverage stay separate in every presentation.

## Registration lifecycle and executed source

Recipe checks offer **Registration, actual lookup decisions, and final snapshot** or **Final
snapshot only** in both IDEs. The CLI defaults to lifecycle and accepts
`checks prepare --no-trace` for snapshot-only checks. Unsupported attachment
admission fails preparation with that explicit alternative; it never silently
changes the requested observation scope.

The Supersymmetry profile owns four packaged Java source resources and exact
GTCEu 2.8.10-beta / GroovyScript 1.4.3 artifact and class-definition guards.
Cleanroom admits its 0.6.12-alpha / JDK 25 instrumentation mechanism. Core
compiles a dependency-free attachment using the image's verified JDK, seals
source/compiler/JAR/configuration hashes, and copies it into a reserved directory
of the disposable runtime. A Java 17 bootstrap bridge remains readable to the
game's class tooling; only the agent uses the JDK 25 class-file API. There is no
download, mod replacement, general-purpose JVM-argument escape hatch,
retransformation, canonical pack mutation or weakening of image validation.

The define-time guards cover the actual transformed classes delivered by
Cleanroom. The only normalization is the per-launch MixinMerged session UUID
on the two audited RecipeBuilder accessors: hashing substitutes that annotation
value only, rejects executable string references to it, and retains the raw
definition hash as well. Runtime metadata is not rewritten. All other definition
bytes must match. Missing hooks or changed definitions make lifecycle evidence
incomplete while preserving independently valid snapshot evidence.

For MIXER, `workbench-recipe-lifecycle-v1` records nested registration, build,
validation, add/post-validation, lookup insertion, explicit removal and clear
operations, their normal/exceptional outcomes and bounded operation-time recipe
properties. Object references are local to that process and linked to final
snapshot references by object identity. Successful insertion, category/lookup
membership and the final selected recipe are distinct observations. A false
insertion result alone does not establish an internal collision or rejection
reason. Separately validated decision evidence is described below;
scripted-storage acceptance is not separately hooked.

Executed source links require the exact text presented to Groovy's parser,
matching captured path and SHA-256, an actual compile-unit/class association,
compiled bytecode digest, and a stack frame with a valid source line. A file
reread, filename similarity or static recipe match is never substituted. Cached
or otherwise unbound classes remain unattributed. Coverage means the declared
hooks were installed and observed operations closed, not branch coverage or
proof that a missing declaration never executed. The first unsuccessful stage
is an observed outcome, not automatic root-cause diagnosis.

The collector bounds events (20000), recipe objects (4096), source bindings
(8192), per-source input (1 MiB) and aggregate operation-property text (2 MiB).
Selected source-file/recipe histories retain related nested operations, not all
unrelated registrations. Serialized lifecycle capture is capped at 512 KiB;
overflow or incomplete historical selection cannot support causal links. The
explanation shows at most 64 operations and explicitly reports omissions. An
oversized trace is discarded before applying the existing final-snapshot bound.

Crucible validates that derived events and locations agree with retained raw
evidence. Shell verifies captured source on reopening, including events omitted
from the display. Both IDEs can read a retained executed source line as a
read-only document even after the working copy changes; opening the working
copy still requires identical saved bytes. CLI equivalent:

```bash
workbench context run SESSION_ID -- checks source ATTEMPT_ID --source SECTION_ID:INDEX
```

Use a section/source index from the retained explanation, not an arbitrary file
path. Inspection requires no installed profile or new runtime execution.
Cross-run recipe comparison remains semantic snapshot comparison: local event
IDs are not paired across runs. Observer-on/off runs have different runtime
identities and cannot be presented as ordinary equivalent-environment checks.
Explicit parity experiments are development evidence, not release qualification.

## Actual insertion and lookup decisions

`workbench-recipe-decisions-v1` extends the same retained capture and explanation
workflow. This is execution of the original GTCEu `RecipeMap.findRecipe`, not a
simulation or a second implementation of its matching algorithm. Each explicitly
tagged probe query invokes the original method once. The observer captures its
actual arguments and returned object. The independent acceptance scan is outside
that scope and is never presented as the lookup's candidate-evaluation sequence.

The exact-definition observer admits audited method descriptors and bytecode
branch sites. Original conditionals consume their original operands once;
callbacks observe the branch taken without rerunning predicates, equality checks,
hashing, or tree traversal. Registration decisions distinguish occupied recipe
leaves, terminal subtrees, propagated child failure and cleanup branches. A
subtree conflict does not name an invented unique conflicting recipe, and taking
a cleanup branch is not proof of atomic rollback or successful cleanup.

Lookup evidence records visited ingredient keys, candidate predicate outcomes,
voltage branches and the selected recipe. An accepting recipe not evaluated before
the method returns is labelled **not evaluated**, not rejected. The selection is
the first accepted candidate reached in that traversal; no ranking, specificity,
registration-order or overwrite policy is inferred. Process-local branch, recipe,
operation and query references are never cross-run identities.

The collector bounds decision steps at 40000, at most 64 tagged queries and 4096
steps per query, 8192 branch
identities, 8 MiB aggregate decision text and 512 KiB serialized retained decision
evidence. The final-snapshot probe may expand up to 256 queries; exceeding the
decision layer's lower bound makes decision evidence incomplete, not the snapshot.
Only selected/related registration histories and tagged queries are
retained. Unsupported ingredient-key details remain explicitly unavailable;
unknown, missing, malformed or over-bound decision evidence cannot establish a
rejection reason. Independently sound lifecycle/snapshot evidence remains usable.

Supersymmetry validates audited sites, actual query arguments, single invocation,
closed query intervals, event ordering, registration ownership and agreement with
the final selected recipe. Crucible verifies that retained derived decision data
agrees with raw capture. Source links reuse compiler-bound executed origins;
unattributed competitors are not assigned a source by textual similarity. Both
IDEs render the same owner-produced explanation and use the existing retained
source/open-working-copy and explicit saved-edit rerun workflow. An old expectation
is never silently rebound to an edited declaration.

These checks use bounded concrete input combinations with effectively unlimited
voltage and exact-voltage mode disabled. The result means **GTCEu selected this
recipe under these captured conditions**, not **the implementation is universally
valid** or **a real machine successfully processed it**. Repeatability requires
the same admitted implementation, prepared state, ordered arguments and conditions,
with repeated controls; merely invoking the genuine method is not a determinism
guarantee across different environments.

## Next slice: machine-execution acceptance

The next vertical must establish developer recipe validity through actual,
scenario-bounded execution, not by strengthening the wording of a lookup result.

Expected effects must be declared independently of the implementation under test;
deriving both expected and observed behavior from the same recipe cannot establish
that the developer's intended behavior is correct.

It should bind a saved implementation and that explicit expectation to:

- An exact pack/runtime, machine type/configuration, installed recipe state and
  loading/restart boundary. The runtime that owns authoritative machine state
  must execute the test; a separate matching simulation is insufficient.
- Controlled starting inventories, fluid tanks, power/voltage, output capacity
  and any recipe-specific environmental requirements, all verified before use.
- Observed selection and actual processing through completion, then assertions
  on item/fluid consumption, expected outputs and quantities, energy and timing
  where supported, and unintended residual or duplicate effects.
- Negative controls for insufficient inputs, incorrect selectors and unmet
  machine conditions. Cold-run repetition must precede claims about edit effects.
- Sealed before/after state, selected recipe/source identity, execution evidence,
  timeout/failure handling and verified disposable-world/process cleanup.

Developer-led editing remains the default. Success must mean every declared
acceptance scenario passed with complete evidence. Unsupported conditions,
incomplete execution or missing evidence must prevent acceptance. No finite suite
can honestly guarantee all possible recipes, environments or future changes;
the guaranteed claim must name exactly which scenarios and effects were verified.
Native Linux and publication qualification remain independent gates.

## Prepared runtime images and advanced import

The normal setup path is [native environment preparation](NATIVE-ENVIRONMENT-PREPARATION.md)
with Packwiz and Prism. It resolves dependencies into an immutable, offline-account
image without running Minecraft. Checking that image remains a separate action.

Advanced import is also available in the IDE and CLI. Supply a self-contained game directory, a
complete native JDK, and the exact direct Java argument array. Do not select a
launcher/account directory or retain account credentials.

The Cleanroom platform admits its current pinned JDK release/vendor and
transformer artifacts. Supersymmetry verifies the saved Packwiz client mod hashes
and rejects undeclared or nested mod JARs. The image and copied JDK retain full
content manifests. Files must be independent ordinary files: linked files and
external resource paths are rejected. A launcher installation with shared
libraries must first be materialized as a self-contained native image; importing
a launcher directory does not accomplish this automatically.

Launch arguments use the platform's current entry point,
`top.outlands.foundation.boot.Foundation`, image-relative classpath/native/assets
resources and `--gameDir .`. If present, `--accessToken` must be `0`. There is no
shell interpolation. The exact arguments depend on the installed client; no
generic launch command or implicit dependency selection is invented.

Parsed Packwiz dependencies and conservative auxiliary inputs form the binding.
Derived index hashes and demonstrably unindexed Markdown do not invalidate it.
Saved source-root edits can reuse the image; dependency changes require preparation.
Image import and preparation do not authorize a runtime launch.

```bash
workbench context run SESSION_ID -- checks catalog
workbench context run SESSION_ID -- checks import-image \
  --runtime-root /path/to/self-contained-client --java /path/to/jdk/bin/java \
  --arguments-json "$CLIENT_ARGUMENTS_JSON"
workbench context run SESSION_ID -- checks images
workbench context run SESSION_ID -- checks prepare --image IMAGE_ID --timeout 600
workbench context run SESSION_ID -- checks execute ATTEMPT_ID --confirm REQUEST_ID
workbench context run SESSION_ID -- checks show ATTEMPT_ID
workbench context run SESSION_ID -- checks history
workbench context run SESSION_ID -- checks compare CANDIDATE_ATTEMPT_ID --reference REFERENCE_ATTEMPT_ID
workbench context run SESSION_ID -- checks progress ATTEMPT_ID
workbench context run SESSION_ID -- checks log ATTEMPT_ID --path logs/latest.log
workbench context run SESSION_ID -- checks cancel ATTEMPT_ID
workbench context run SESSION_ID -- checks recover ATTEMPT_ID --confirm REQUEST_ID
```

Use the exact returned IDs and your actual argument array. Preparation binds
saved bytes, context, pack/platform code, image, timeout and probe overlays.
Execution requires that request ID and rechecks source/provider/image freshness
before launch. An attempt executes at most once; retry by preparing a new one.
Check outcomes are carried in `result.state`; a successful command transport is
not a successful check. Concurrent Work Session append conflicts retain the
owner record and report the linking failure rather than overwriting the journal.

## Ownership, storage and recovery

| Responsibility | Owner |
| --- | --- |
| Saved input capture and exact candidate manifest | Project Intelligence |
| Check-provider admission, outcome/evidence and assertion contracts, grouping and comparison | Crucible |
| Game/toolchain admission, probe and interpretation policy | Pack/platform profiles |
| Image/JDK copies, process supervision, cancellation and trash | Core |
| Existing Work Session orchestration and owner references | Shell |
| Explicit consent, progress, diagnostics and navigation | IDE clients |

The native entry-point groups are `workbench.developer_checks` for pack checks
and `workbench.check_platforms` for platform admission. Profiles import neither
Shell nor Core implementation. Core does not interpret game logs or recipes.

State lives outside the checkout under the selected private state root's
`developer-checks/` directory. Saved candidates, separate instrumentation,
projection manifests, retained console sessions and sealed results remain
available after disposable runtime cleanup. Core moves runtimes into existing
same-filesystem recoverable trash, never recursively deletes the source tree.
World-shaped or otherwise protected storage blocks cleanup for explicit review.

Cancellation requests the owner to close its process group and waits for closure;
it does not kill the IDE transport and assume the game stopped. An interrupted
writer produces `needs-attention`, not a pass. Recovery verifies exact recorded
process custody before signaling; uncertain ownership blocks automatic recovery.
A recovery receipt never rewrites or promotes the original result. A recovered
attempt without a sealed check result remains `recovered-incomplete`.

Disposable execution is **not a security sandbox**. Run only trusted projects.
Credentials and JVM-injection environment variables are not inherited, but code
can still access resources available to your OS account. Hard-crash recovery and
owned-process-group closure are not containment against malicious escaping code.

The historical Supersymmetry 0.1.16.12 / SusyCore 0.1.112 / Recurrent Complex
1.4.8.6 control has a named-variable mixin incompatibility with the selected
provisional Cleanroom runtime. This is already fixed upstream:
[Susy-Core #695](https://github.com/SymmetricDevs/Susy-Core/pull/695), released in
0.1.116, changed the requested local name from `arg3` to `flag`;
[Susy-Core #697](https://github.com/SymmetricDevs/Susy-Core/pull/697), released in
0.1.117, replaced name matching with argument slot `3`.
[Supersymmetry 0.1.16.15](https://github.com/SymmetricDevs/Supersymmetry/releases/tag/0.1.16.15)
contains SusyCore 0.1.118 and Recurrent Complex 1.4.8.7.

Select an explicit current pack revision and prepare its own dependency-bound
image. Preparing an older checkout does not update it to the latest release.
Do not substitute old experimental binary patches, replace individual mods
behind the content manifest, or suppress the failure. Upgrading the complete
released pack preserves its paired scripts/configuration and mod dependencies.
An installed fix is not itself proof of startup: establish repeatable unchanged
controls before interpreting recipe deltas, and a native Linux baseline before
claiming platform qualification. WSL observations do not grant that qualification.
Guided generation remains tertiary; none of those improvements should require
developers to stop editing their own source in their IDE.
