# A Switch worker, separate from the Link worker, run by both stacks

Every Release now Cleans up, and the Cleanup is checked: BLab restores init, reloads the Switch, waits for it to come back, Inspects it, and Quarantines it in the last holder's name if it isn't clean. That takes minutes, so it can't happen inside the request. `POST release/` tears the Links down, deletes the Reservation, and records a `PendingCleanup` in the same transaction, which keeps the Switch from being reserved until the Cleanup is done. The **Switch worker**, the `switch_worker` compose service, carries it out. Each step is saved in the `PendingCleanup` before the next one starts, so a redeploy only delays a Cleanup.

Like the Link worker (ADR 0003), both stacks run one, and a Postgres advisory lock lets only one work at a time. The Switch worker has its own lock key, so the two workers never wait on each other.

## Considered options

- **Make it part of the Link worker.** Rejected. Production runs `main`, whose Link worker knows nothing about Cleanups, and it usually holds the Link worker's lock. Pre-prod's Releases would then wait for the next `main` release. A reload that takes minutes would also hold up disconnects queued behind it.
- **Only production runs it, like the expiry job.** Rejected for the same reason: pre-prod's Releases would wait for `main`.
- **Wait for the reload inside a thread after the response.** Rejected: a redeploy kills it halfway, as ADR 0003 explains.

## Consequences

- Until `main` runs this code, production's Releases and expiry still Clean up the old way: a reload with no Inspection after it. Production's reserve also ignores `PendingCleanup`, `Quarantine` and Out of service: an Out of service or Quarantined Switch can still be reserved through production, and for a few minutes a Switch being Cleaned up can be reserved through production. The worker then skips the Cleanup, or skips the Quarantine if the reservation comes after the reload. These gaps close once `main` runs this code.
- Local dev with fake devices needs `python manage.py switch_worker` running for Releases to Clean up.
- The nightly Sweep (#22) belongs in the same worker.
