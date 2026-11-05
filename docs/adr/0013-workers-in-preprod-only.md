# The Link and Switch workers run in pre-prod only

Supersedes ADR 0003 and ADR 0005 on where the workers run. Everything else those two ADRs say still stands: what each worker does, its advisory lock, the Sweep.

Both stacks used to run a Link worker and a Switch worker against the shared database (ADR 0002), and whichever held a worker's advisory lock did its work. That let production's older code carry out a feature that only existed on `dev`. Production's Switch worker held the lock and onboarded no-EMP Contributions (#81) without knowing about Management ports, which left 10.69.144.148 and .174 unreachable after their onboarding reload (#115). The Link worker had the same hazard: whatever pre-prod writes for a disconnect or a Reconcile, `main`'s worker may act on with code weeks older.

Decided with Titouan (2026-11-05): only pre-prod runs the two workers. The `link_worker` and `switch_worker` services are defined in `docker-compose.preprod.yml`, which only pre-prod's `.env` names in `COMPOSE_FILE`. They take the `django` service's image and settings (`extends`), as they did before. Production's `docker-compose.yml` doesn't define them, so a production deploy (`docker compose up -d --build --remove-orphans`) removes the worker containers it still has, as orphans, and never starts them again. Neither `.env` changes.

Each worker keeps its advisory lock. It now only guards against two pre-prod containers working at once (during a redeploy, or a `docker compose run`), and costs nothing.

## Considered options

- **A compose profile on the services in `docker-compose.yml`, turned on by the pre-prod file.** Rejected: compose doesn't count a service turned off by a profile as an orphan, so production's existing worker containers would keep running their old image after the deploy.
- **An environment variable in each `.env` that makes the worker idle.** Rejected: production's `.env` would have to change, and a container that sits idle is easy to mistake for one that works.
- **Keep both stacks and gate each `dev`-only feature behind a stage setting.** Rejected: every feature the worker carries out would need one, and forgetting one breaks Switches, as #115 did.

## Consequences

- **Production depends on pre-prod being up** for disconnects, Reconciles, Cleanups, Restores, the Sweep, the Switch account and banner retries, the Login check and onboarding. While pre-prod is down or redeploying, these wait in the database. A Release through production still frees nothing until pre-prod's Switch worker has Cleaned the Switch up. A pre-prod deploy that fails leaves them waiting until it is fixed, so a failed `deploy/preprod` is now an outage for production's users too.
- What the workers do always follows `dev`'s code. The "until `main` runs this code" caveats about production's worker in the other ADRs (0004 to 0011) no longer apply once `main` runs this change.
- Settings that only the workers read, such as `BLAB_SWEEP_MODE` and `BLAB_SWITCH_WORKER_THREADS`, take effect from pre-prod's `.env` alone.
- Until `main` runs this change, production's `docker-compose.yml` still defines the workers, and each production deploy starts them again. Titouan stops them by hand meanwhile (`docker compose stop link_worker switch_worker` in the production checkout).
- Restoring a dump stops pre-prod's whole stack, which now includes every worker (README).
