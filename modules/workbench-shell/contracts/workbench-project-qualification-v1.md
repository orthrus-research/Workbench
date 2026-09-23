# Workbench project qualification V1

## Purpose and authority

Project qualification associates one existing checkout with one explicitly
selected Workbench pack profile. Project Intelligence supplies the exact
read-only workspace facts. The selected pack and platform documents remain
the identity authority. Workbench Shell owns only consent, private local
binding storage, and CLI presentation.

V1 supports `--profile supersymmetry` and the canonical family
`workbench-pack:supersymmetry`. That support is explicit and does not make
Supersymmetry a universal default.

Qualification does not write the checkout. It does not establish source or
recipe approval, build success, runtime readiness, publication approval,
support, or release qualification. It is intentionally optional and does not
alter or gate Workspace Home.

The selected workspace must also be the Git repository root. Qualification
does not run repository-wide status or source fingerprinting from a nested
pack path, because that would cross the selected workspace boundary and could
expose sibling paths or bytes as provenance.

## Public command

```text
workbench project qualify WORKSPACE --profile supersymmetry [--status | --plan | --apply PLAN_ID] [--state-root PATH] [--json]
```

`--status` and `--plan` are read-only and emit their complete contract even
when the workspace is incompatible. A structured mismatch therefore remains
available to IDEs; callers inspect `state`, `can_apply`, and `qualified`
rather than treating missing stdout as evidence. An explicit apply re-runs the
inspection and accepts only the exact current `plan_id`.

With no phase, a non-interactive call or any `--json` call emits one plan and
does not apply it. A plain interactive terminal shows that plan and asks
`Apply this qualification? [y/N]`. EOF, `n`, or Ctrl+C leaves both the checkout
and binding unchanged when cancellation precedes the atomic binding commit. If
Ctrl+C arrives after the exact binding has been atomically published,
Workbench reports that qualification committed before cancellation, identifies
the binding revision, and directs the developer to the read-only status flow
with the exact effective `--state-root`;
it never claims that nothing changed after a verified commit.

## Qualification states

- `ready`: every family check is ready.
- `attention`: family conformance is established, but a separately important
  condition such as Packwiz index integrity needs attention. The plan remains
  applicable.
- `incompatible`: exact family conformance failed. `can_apply` is false,
  `actions` is empty, consent values are null, and apply is rejected.

A dirty working tree is retained and displayed as provenance. Ordinary source
or recipe changes do not stale family qualification. The qualification
inspection identity binds the selected profile documents, required marker
semantics, Packwiz manifest identity, loader/platform declarations, and index
evidence. A relevant change produces a different inspection identity and a
stale binding.

## V1 records

The status format is `workbench-project-qualification-status-v1`. Its exact
top-level fields are:

```text
format, schema_version, operation_class, state, can_apply, project_id,
profile, workspace, inspection_id, binding, checks, limitations, qualified
```

The plan format is `workbench-project-qualification-plan-v1`. Its exact
top-level fields are:

```text
format, schema_version, operation_class, plan_id, state, can_apply,
project_id, profile, workspace, inspection_id, binding, checks, limitations,
actions, consent
```

The plan identity is canonical compact, sorted UTF-8 JSON over the project ID,
profile selection, workspace root, inspection ID, qualification state,
current binding state/identity/path/revision, and the single action or null.
Git revision and dirty provenance are deliberately excluded: accepting family
identity must not also approve or freeze ordinary pack edits.

The result format is `workbench-project-qualification-result-v1`. Its exact
top-level fields are:

```text
format, schema_version, outcome, applied_plan_id, binding, qualification,
next_commands
```

Outcomes are `qualified`, `requalified`, or `reused`. The nested qualification
contains `qualified`, `state`, `project_id`, `profile`, `workspace`,
`inspection_id`, `checks`, and `limitations`. Its workspace provenance is the
fresh apply-time observation and may differ from the reviewed plan after an
ordinary source edit; its root and every qualification-relevant identity must
still match the applied plan.

V1 returns one exact next command: `workbench project qualify WORKSPACE
--profile supersymmetry --status --state-root EFFECTIVE_STATE_ROOT`. It verifies
the retained association in the same private store without implying that setup
or Workspace Home was changed.

## Private storage

Bindings live under
`STATE_ROOT/project-qualification-v1/bindings/<binding-digest>.json`. The state
router defaults `STATE_ROOT` through `default_product_spine_state_root`, shared
by Workbench's CLI and IDE-facing product-spine conventions; `--state-root`
remains an explicit override. The state root must remain outside the target
checkout by lexical and physical identity. Workbench resolves a non-existing
state root through its nearest real existing parent, rejects symlink/reparse,
same-file, and same-Git-directory aliases, and repeats the physical check after
creating state directories but before creating a lock, staging file, or binding.
Workbench creates owner-private
directories and an owner-private regular file, rejects symlink or special-file
substitution, writes through an exclusive temporary file, atomically replaces
the destination, and flushes the containing directory. The binding is
content-addressed by its exact body and is revalidated before reuse.

Workspace and state paths must not contain terminal controls. Plain terminal
rendering escapes C0, C1, line/paragraph, and Unicode format controls from Git
diagnostics, inspection details, retained checks, paths, and other untrusted
text before displaying it, so evidence cannot forge prompts or terminal state.

An IDE is a strict client of this same status/plan/result flow. It may make the
action convenient and optional, but it must not synthesize profile authority,
skip the plan, weaken incompatible evidence, or add another approval path.
