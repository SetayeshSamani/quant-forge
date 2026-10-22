# Repository instructions

## QuantForge development workflow

- The project owner is the sole maintainer.
- Default all future development to `dev/quantforge` unless the owner says otherwise.
  Create it from clean, synchronized main only if absent; preserve existing work and rules.
- Commit completed tasks directly and push `dev/quantforge` to GitHub.
- Do not create PRs for routine tasks or merge into main automatically.
  Merge to main only on an explicit stable-milestone request.
- Reuse existing code and tests; avoid duplicate work and unnecessary refactoring.
- Add only essential regression tests, grouping related cases with subtests.
- Run targeted tests during development and the full suite at milestones.
  Use Python's `-B` option and quiet test output; avoid repeated full runs.
- Keep reports short and minimize token usage.
- Never enable live trading, use real credentials, or perform account writes
  without separate explicit authorization.
