# Repository instructions

## QuantForge development workflow

- The project owner is the sole maintainer.
- `main` is stable; `dev/quantforge` is the integration branch.
- Never develop or commit directly on `main` or `dev/quantforge`.
- Start each independent feature or fix on its own branch from the latest
  fetched `origin/dev/quantforge`; inspect and preserve existing work first.
- Make multiple meaningful, logically separated commits; never pad commit counts.
- Push the feature branch and open a PR targeting `dev/quantforge`.
  Do not merge automatically; merge to main only on an explicit stable-milestone request.
- Never rewrite history or force-push. Keep personal names out of new branch
  names and commit messages.
- Reuse existing code and tests; avoid duplicate work and unnecessary refactoring.
- Add only essential regression tests, grouping related cases with subtests.
- Run targeted tests during development and the full suite at milestones.
  Use Python's `-B` option and quiet test output; avoid repeated full runs.
- Keep reports short and minimize token usage.
- Never enable live trading, use real credentials, or perform account writes
  without separate explicit authorization.
