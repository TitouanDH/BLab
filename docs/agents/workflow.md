# Workflow: issue → worktree → dev (pre-prod) → main (production)

Three environments, always by these names:

- **Local dev**: native Django + Vite on a laptop or in a worktree, with fake devices and SQLite or a prod snapshot. This is where agents test.
- **Pre-prod**: branch `dev`, deployed on the server at `https://10.69.144.180:8443`. It uses production's database and the real switches. Titouan uses it day to day.
- **Production**: branch `main`, deployed on the server at `https://10.69.144.180`, used by everyone.

Pushing a branch redeploys it: a cron poller on the server pulls within a minute, runs `docker compose up -d --build`, and posts the GitHub commit status `deploy/preprod` or `deploy/production`. Merge into `dev` freely, with no confirmation needed. Titouan only merges the `dev` → `main` PR. Why pre-prod shares the database, and the migration rule that follows, are in `docs/adr/0002-preprod-shares-production-database.md`.

## Picking work

- Work on issues labelled `ready-for-agent`.
- Triage `needs-triage` issues yourself: promote clear ones to `ready-for-agent`. If one needs Titouan, comment the specific question on the issue and add `needs-info`.
- Independent issues may run in parallel, each in its own worktree. Issues that change models go strictly one after another, because migration numbers would collide.

## Per issue

1. **Worktree**: `git worktree add .claude/worktrees/<n>-<slug> -b issue/<n>-<slug> dev`, created from an up-to-date `dev`. Reuse the main checkout's `.venv`, and run `npm install` in the worktree's `frontend/`.
2. **Implement** test-first where it fits (`tdd` skill). Model changes: commit the migration with the change, and keep it **additive** (ADR 0002). Anything destructive is split into steps across `main` releases.
3. **Gate**: all of these must pass in the worktree. Everything that reaches `dev` runs against real switches and real data within a minute, so this gate is the last guard.
   - `cd api; $env:DB_ENGINE="sqlite3"; python manage.py test api`
   - `python manage.py makemigrations --check --dry-run` and `python manage.py check_shared_db_migrations`
   - `cd frontend; npm run build`
   - `code-review` skill against the issue, with its findings fixed
   - for UI or API flow changes: run the app in local dev and exercise the change
4. **Merge**: rebase onto the latest `dev`, rerun the gate, then squash-merge into `dev` as one commit titled `<title> (#<n>)`, and push.
5. **Check the deploy**: wait for the `deploy/preprod` status on the pushed commit (`gh api repos/TitouanDH/BLab/commits/<sha>/status`). If it fails, fix it right away through `dev`.
6. **Close**: comment on the issue with what changed, how it was tested, and what to try on pre-prod. Close the issue, then remove the worktree and delete the branch.
7. **Standing PR**: keep exactly one open PR from `dev` to `main`. Create it if none is open. Update its description to list every issue it contains (`#<n> <title>`), plus anything needed at deploy time.

## Review findings

If Titouan finds a bug on pre-prod, or asks to reopen an issue, reopen or create the issue and fix it through a new worktree.

## After Titouan merges to main

Production redeploys itself. Check `deploy/production` on the merge commit. Then bring the merge commit back into `dev` (`git merge origin/main`) so the branches don't drift.

## Hotfix (emergencies only)

When production is broken and `dev` holds unreleased work: branch `hotfix/<n>-<slug>` from `main`, run the gate, and open a PR straight to `main` for Titouan. Once it's merged, merge `main` into `dev`.

## Rollback

`git revert` the bad commit on the branch (`dev` directly; a PR for `main`), and the poller redeploys. Migrations stay applied, which is safe because they are additive.

## Server

- **Layout**: `titouan@10.69.144.180`, key-based SSH from Titouan's laptop.
  - `/home/titouan/BLab` is the production checkout, on `main`.
  - `/home/titouan/BLab-preprod` is the pre-prod checkout, on `dev`.
  - `/home/titouan/blab-tls` holds the TLS certificate and key.
- **Config**: each checkout has a `.env` (see `.env.example`). The real files only exist on the server, because the repo is public.
- **Pollers**: run from the user crontab (no sudo), one line per checkout with `scripts/deploy.sh`. Logs go to `~/deploy-production.log` and `~/deploy-preprod.log`.
- **Access**: only touch the server to fix a failed deploy or the deploy setup itself. Never edit files in the checkouts by hand: the next fast-forward would fail.
