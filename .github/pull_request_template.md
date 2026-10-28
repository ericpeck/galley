## What this does

Closes #___ / Part of #___

One line a user would understand:

## Test first

- Test(s): `tests/...`
- Fails on the parent SHA `_______` with: 
- Passes on the head SHA `_______`

## Checks at this head

- [ ] All six required checks are green at this head: `Lint`, `Secret scan`, `Test (macOS arm64)`, `Test (Windows)`, `perf`, `perf (Windows)`
- [ ] 0 behind `main` (syncs use merge commits only; no rebase or force-push)
- [ ] Perf: for each perf test touched, worst macOS and Windows CI time vs its budget. Flag anything at 90% or more of budget for infra review.

The nightly YAML parser comparison is not a required check; don't wait on it for a PR.

| Test | macOS | Windows | Budget |
| --- | --- | --- | --- |
|  |  |  |  |

## Scope and safety

- Files changed:
- [ ] UI or wording changed? If yes, screenshots attached and UI review requested.
- [ ] No secrets, personal paths, or email addresses; no `eval`/`exec`; fixtures use fake values only.

## Authorship

- [ ] Commits use the noreply author only
- [ ] No `Co-Authored-By` trailer and no tool attribution anywhere

## Reviewers

- Code review: approves the exact head SHA
- UI review: when UI or wording changed
- Infra lane check: CI, budgets, and checks

## Deferred follow-ups

Tracking issue #___, or "none".
