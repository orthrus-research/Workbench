# Feature Studio

Status: current experimental product architecture

Feature Studio is Workbench's source-to-runtime workspace for one bounded mod
or pack change. The first implemented family is a Supersymmetry
material-backed fluid and recipe change. It makes the intended source edits
and the result of a fresh disposable run reviewable in one place without
inventing a new authority.

## Authority model

```text
developer intent + selected profile
                │
                ▼
      Blueprints construction plan
                │
                ▼
     Crucible disposable verification
                ▼
       Workbench Shell result
                │
       CLI / VS Code / IntelliJ
```

- Blueprints chooses an admitted standard or labeled experimental pattern and
  owns the exact construction plan.
- Atlas reports only knowledge supported by selected evidence.
- The pack and platform profiles own their exact compatibility and action
  policy.
- Crucible owns disposable execution, observations, and retained receipts.
- Workbench Shell composes those results; it does not create another approval
  or truth source.
- Native clients render the same core records and never implement construction
  or runtime policy locally.

Manuals and explanation views teach and navigate. They do not authorize a
change or upgrade an observation.

## Feature workspace

One workspace keeps the following identities separate:

- the selected source checkout, revision, pack profile, and platform profile;
- normalized developer intent and the Blueprint standard or pattern;
- the exact planned files, anchors, source hashes, and collision checks;
- disposable runtime plan, consent identity, assertions, and retained output;
- before/after projections and recovery or rollback state; and
- limitations and the next safe action.

The initial Materials & Recipes slice plans three profile-owned source edits:
the material declaration, localization, and bounded recipe script. It does not
claim that those edits compiled, loaded, or registered until the corresponding
assertions are observed independently.

## Current command surface

Feature Studio exposes five experimental commands:

| Capability | CLI | Effect |
| --- | --- | --- |
| `feature-studio.inspect` | `workbench studio inspect` | Open a new plan or retained receipt by exact identity. |
| `feature-studio.plan` | `workbench studio plan` | Produce the Blueprint/profile plan without writing source. |
| `feature-studio.verify` | `workbench studio verify` | Revalidate a reviewed plan and run it in fresh ignored state. |
| `feature-studio.explain` | `workbench studio explain` | Present owner-backed state, limitations, and next actions. |
| `feature-studio.export` | `workbench studio export` | Write a reviewed patch and receipt without modifying the checkout. |

Use `workbench capabilities --suite feature-studio` for the exact current
fields, risks, availability, and command templates. The generated capability
catalog is authoritative; this page is explanatory.

The related retained change workflow provides seven lifecycle actions:

```text
workbench change start material-fluid-recipe WORKSPACE ...
workbench change open CHANGE_ID
workbench change test CHANGE_ID
workbench change apply CHANGE_ID --consent-plan-id PLAN_ID
workbench change verify CHANGE_ID
workbench change rollback CHANGE_ID
workbench change recover CHANGE_ID
```

Starting a change retains private state but does not edit the target. Apply
requires the exact reviewed plan identity and fresh source revalidation.
Rollback never overwrites later user edits, and recovery reports the current
transaction boundary instead of guessing.

## Review and execution boundaries

A read-only plan must show:

- every input identity and selected profile;
- the exact files and operations proposed;
- conflicts, missing anchors, unavailable knowledge, and limitations;
- the intended disposable runtime and compatibility projection; and
- the assertions that remain unexecuted.

Execution is allowed only after explicit consent to that exact plan. It stages
under ignored `.workbench/` storage or another selected external store and
uses a fresh Workbench-owned launcher projection. Source or policy drift makes
the plan stale. Arbitrary compatibility patches, existing-world mutation, and
unreviewed checkout writes fail closed.

The current verification path keeps these observations independent:

1. source plan revalidation;
2. staging and patch application;
3. compilation and launcher startup;
4. FML client load;
5. GroovyScript execution;
6. material and fluid registration;
7. localization; and
8. cleanup and retained-record closure.

An earlier success never substitutes for a later observation. Failure,
cancellation, and timeout remain distinct results.

## Native clients

The [VS Code](../../clients/vscode/README.md) and
[IntelliJ IDEA Community](../../clients/intellij-community/README.md) clients
may provide native trees, forms, diffs, progress, diagnostics, and navigation.
Both launch the installed Workbench core directly without a shell and consume
the same capability and result records. Workspace trust and explicit consent
remain mandatory for mutating operations.

## Non-claims

Feature Studio does not by itself establish:

- global recipe reachability, balance, or progression quality;
- compatibility outside the exact selected platform and pack profile;
- safety for an existing world or save migration;
- successful runtime behavior without a closed observation;
- support on an untested host or IDE version; or
- release or publication readiness.

## Implementation and validation

The composition lives in `modules/workbench-shell/src/workbench_shell/` and
uses profile-owned Supersymmetry construction policy. No stable
material-backed-fluid standard is currently admitted. Relevant public
contracts include the
[Feature Studio snapshot](../../modules/workbench-shell/contracts/feature-studio-snapshot-v2.md),
[service protocol](../../modules/workbench-shell/contracts/service-protocol-v3.md),
and Blueprints [module overview](../../modules/blueprints/README.md).

Run the focused source tests with:

```bash
python3 -m unittest discover \
  -s modules/workbench-shell/tests \
  -p 'test_feature_studio*.py'
```

Generated plans, patches, receipts, launcher instances, worlds, logs, and
captures remain ignored local state.
