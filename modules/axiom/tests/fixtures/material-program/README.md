# Complete material-authoring program

This is an original, complete multi-file authoring fixture, not an excerpt from
Supersymmetry and not a claim that the pack's material files execute. It uses the
pack's normal imports, class helpers, static fields, global log binding, mixed
material/component-stack syntax, and native material/post-material listeners.

The explicitly bounded context is `supersymmetry:material-authoring-gt-base`:
the complete already-qualified GT material catalog, GT registration lifecycle,
and qualified generated forms, with this program as the only pack listener set.
It deliberately does not represent Susy-Core's material listener composition,
FIBER/slurry additions, pack `ChangeFlags`, or custom meta items. No registry is
created for the `supersymmetry` resource namespace; native fallback must retain
the GT storage registry separately from resource names and the script owner.

`expectations.json` declares outcomes to prove, not recorded passing evidence.
Execution must retain every file in `groovy/` and apply native run-config loading;
silently extracting successful declarations does not satisfy this fixture.
The three material examples exercise the mechanisms seen in the pinned pack's
Petalite, Amblygonite and Perovskite declarations, with independent fixture names.
Exact upstream excerpts belong to separately supplied source qualification inputs.

The acceptance driver must run unchanged source first, then exact-base edits:
bad component type (native log and continuation), property constructor failure,
late registration, deferred listener failure, and caught unqualified behavior.
Every edit starts a fresh isolated worker; no reset of this program's static
fields or registry state is a substitute for process isolation.
