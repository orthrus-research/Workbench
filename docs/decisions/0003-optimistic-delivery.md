# Decision 0003: competent optimistic delivery

Status: accepted
Date: 2026-07-30

## Decision

Workbench defaults to trying the best available implementation, getting it to
compile and run, and learning from concrete failures. It does not require
formal proof closure before every feature can be attempted.

## Working loop

```text
best-known pattern
  -> reviewable candidate
  -> compile and focused checks
  -> disposable Cleanroom smoke run
  -> experimental use
  -> debug failures
  -> improve pattern, tests, knowledge, and guide
```

A crash is an unambiguous failure. So are a build failure, invalid generated
output, data corruption, or a failed required check. Workbench should make
those failures easy to reproduce and diagnose rather than use process to
pretend they were prevented.

## Proportional rigor

- A reversible local experiment needs a preview, compile, and relevant smoke
  checks.
- An experimental team release needs clear labeling and a rollback path.
- A stable release needs regression coverage for known failure classes and a
  tested Cleanroom profile.
- Destructive world or serialized-data changes need backups and stronger
  compatibility checks.

Atlas may retain deep provenance when it helps answer a hard question.
Blueprints may retain deterministic records when they make debugging or
reproduction easier. Neither becomes a universal ceremony that blocks useful
work.
