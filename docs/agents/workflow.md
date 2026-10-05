# Workflow: issue → worktree → dev → main → deploy

`dev` is the integration and local validation branch. Merge into it freely, with no confirmation needed. `main` is production: every commit on it must be deployable with `git pull && docker compose up -d --build`. Titouan only reviews and merges the `dev` → `main` PR.

## Picking work

- Work on issues labelled `ready-for-agent`.
- Triage `needs-triage` issues yourself: promote clear ones to `ready-for-agent`. If one needs Titouan, comment the specific question on the issue and add `needs-info`.
- Independent issues may run in parallel, each in its own worktree. Issues that change models go strictly one after another, because migration numbers would collide.

## Per issue

1. **Worktree**: `git worktree add .claude/worktrees/<n>-<slug> -b issue/<n>-<slug> dev`, created from an up-to-date `dev`. Reuse the main checkout's `.venv`, and run `npm install` in the worktree's `frontend/`.
2. **Implement** test-first where it fits (`tdd` skill). When models change, generate the migration in the worktree and commit it with the change (see `docs/adr/0001-tracked-migrations-applied-on-start.md`).
3. **Gate**: all of these must pass before merging:
   - `cd api; $env:DB_ENGINE="sqlite3"; python manage.py test api`
   - `cd frontend; npm run build`
   - `code-review` skill against the issue, with its findings fixed
4. **Merge**: rebase onto the latest `dev`, rerun the gate, then squash-merge into `dev` as one commit titled `<title> (#<n>)`, and push `dev`.
5. **Validate on `dev`**: rerun the tests. When the change touches the UI or API flows, smoke-test the app natively (README, "Local development": fake devices, prod-snapshot DB).
6. **Close**: comment on the issue with what changed and how it was tested, then close it. Remove the worktree and delete the branch.
7. **Standing PR**: keep exactly one open PR from `dev` to `main`. Create it if none is open (for example, right after Titouan merged the last one). Update its description to list every issue it contains (`#<n> <title>`), plus anything that needs attention at deploy time.

## Review findings

If Titouan asks to reopen an issue from the PR review, reopen it and fix it through a new worktree, like any other issue.

## After Titouan merges to main

Bring the merge commit back into `dev` (`git merge origin/main`, a fast-forward when `dev` hasn't moved) so the branches don't drift.

## Hotfix (emergencies only)

When production is broken and `dev` holds unreviewed work: branch `hotfix/<n>-<slug>` from `main`, run the gate, and open a PR straight to `main` for Titouan. Once it's merged, merge `main` into `dev`.

## Rules

- `main` is protected: changes go through a PR, CI must pass, and only merge commits are allowed. Never push to `main`.
- Never touch production. The only exception is the read-only DB snapshot pull (`scripts/pull-prod-db.ps1`).
- CI (`.github/workflows/ci.yml`) runs the same tests and build on PRs. It has no access to the lab network, which is fine because tests use fake devices.
