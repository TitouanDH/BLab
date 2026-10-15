# Pre-prod shares production's database and real switches

Titouan uses pre-prod (`dev`, port 8443 on the production server) as his daily interface, so that bugs surface through real use before `main` is updated for everyone. That only works if pre-prod shows his real reservations and topologies. So pre-prod's Django connects to production's Postgres, over a dedicated `blab_db` network that only the database and pre-prod's Django join, and drives the real switches (`BLAB_DEVICES=real`).

Real devices follow from the shared database: with fake devices, pre-prod would record links as connected without configuring anything, and production would show tunnels that don't exist. A separate database with real devices would hand out the same switches twice.

## Rules this forces

- **Migrations not on `main` must keep `main`'s code working.** Pre-prod migrates the shared database first, and production keeps running the old code against it, possibly for weeks. Allowed: new tables, nullable columns or columns with `db_default`, indexes. Removing, renaming or changing a column is split up: first ship code that no longer uses it (through `main`), then drop it in a later change. CI enforces this with `manage.py check_shared_db_migrations` (`api/api/migration_safety.py`). A hand-checked migration can opt out with `shared_db_safe = True` and a comment.
- **Rollback is a git revert.** The poller redeploys the previous code. Migrations aren't undone, which is safe because of the rule above.
- **Only production runs expiry** (the `expiry` service, `manage.py expire_reservations`).
- **Pre-prod shows a banner** with its commit, since both UIs act on real switches.

## Considered options

- **A separate pre-prod database (a copy) with fake devices.** It's safe, but it isn't where Titouan's real reservations live, so it wouldn't get used day to day and wouldn't surface bugs.
- **Local-only validation of `dev`.** This was the previous setup. Bugs reached everyone at the same time as they reached Titouan.

## Consequences

- A bug on `dev` can misconfigure real switches or write bad data that production then reads. Worktree tests (fake devices, SQLite) are the guard before anything reaches `dev`.
- Every model change has to be thought through as two-step whenever it isn't purely additive.
