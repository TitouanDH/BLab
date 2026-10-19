# A Switch worker, separate from the Link worker, run by both stacks

Every Release now Cleans up, and the Cleanup is checked: BLab restores init, reloads the Switch, waits for it to come back, Inspects it, and Quarantines it in the last holder's name if it isn't clean. That takes minutes, so it can't happen inside the request. `POST release/` tears the Links down, deletes the Reservation, and records a `PendingCleanup` in the same transaction, which keeps the Switch from being reserved until the Cleanup is done. The **Switch worker**, the `switch_worker` compose service, carries it out. Each step is saved in the `PendingCleanup` before the next one starts, so a redeploy only delays a Cleanup.

Like the Link worker (ADR 0003), both stacks run one, and a Postgres advisory lock lets only one work at a time. The Switch worker has its own lock key, so the two workers never wait on each other.

## When a Switch is back from its reload (#50, #51)

On AOS 8.10, a Switch "verifies and copies images" for minutes after `reload from working` before it goes down, and answers with its old state meanwhile. So the worker doesn't Inspect a Switch merely because it answers once `RELOAD_GRACE` has passed. It counts as back only once it has really rebooted: its uptime (`show system`) is shorter than the time since the reload started, or the worker saw it unreachable since (`api.reboot`). It must also be ready: for a while after it first answers, AOS refuses some commands with "system has not reached the ready state", so `show interfaces` must read. Until `give_up_at` it keeps looking. A Switch that still hasn't rebooted then, never says how long it has been up, or still isn't ready, is Inspected as it is and fails with that reason first; its Quarantine names nobody, since BLab's reload failed.

That the Switch went down is kept only in the worker's memory: a restart, or the lock passing to the other stage's worker, forgets it. Its uptime then decides alone, so forgetting only makes the Cleanup wait, or fail naming nobody; it never names the holder.

An Inspection after a Cleanup that couldn't read something (an `unreadable: ...` reason) names nobody either: BLab couldn't read the Switch, which is never the holder's doing. A Restore waits for its Switches the same way.

A Cleanup that couldn't reload at all (init/ couldn't be copied into working/, say) never ends clean either (#65): init never reached the Switch, so the Inspection made at once fails with that reason first, and the Quarantine names nobody.

## Considered options

- **Make it part of the Link worker.** Rejected. Production runs `main`, whose Link worker knows nothing about Cleanups, and it usually holds the Link worker's lock. Pre-prod's Releases would then wait for the next `main` release. A reload that takes minutes would also hold up disconnects queued behind it.
- **Only production runs it, like the expiry job.** Rejected for the same reason: pre-prod's Releases would wait for `main`.
- **Wait for the reload inside a thread after the response.** Rejected: a redeploy kills it halfway, as ADR 0003 explains.

## Consequences

- Until `main` runs this code, production's Releases and expiry still Clean up the old way: a reload with no Inspection after it. Production's reserve also ignores `PendingCleanup`, `Quarantine` and Out of service: an Out of service or Quarantined Switch can still be reserved through production, and for a few minutes a Switch being Cleaned up can be reserved through production. The worker then skips the Cleanup, or skips the Quarantine if the reservation comes after the reload. These gaps close once `main` runs this code.
- Local dev with fake devices needs `python manage.py switch_worker` running for Releases to Clean up.
- The nightly Sweep (#22) belongs in the same worker.
- A banner that couldn't be written (by a Reserve, a Cleanup or a Restore, or a request killed during the write) stays marked out of step on its Switch (`banner_tried_at`, set before each write and cleared once one succeeds). The worker writes it again every 5 minutes until it is, in the same pass and under the same lock as the Switch accounts (#67). A Switch being Cleaned up is left to its Cleanup, which writes the banner itself. Until `main` runs this code, production's Reserve doesn't mark the banner, so a failed write there isn't retried.

## How the Sweep is done (#22)

- **Where.** The Sweep runs in the Switch worker's cycle, under its lock, so only one Sweep runs at a time across production and pre-prod, and the Cleanups it asks for are the worker's ordinary `PendingCleanup`s. A `Sweep` row records its progress, so a redeploy only delays it; a unique constraint keeps a second one from being under way at once. It looks at a few Switches per cycle, so the Cleanups of Releases keep moving meanwhile.
- **When.** At 02:00 Europe/Paris, or as soon after as a worker holds the lock, until 06:00. A worker that is down all night skips that night rather than reloading Switches in the morning, and a Sweep still going four hours after it started reports the Switches it didn't reach. `manage.py sweep` starts one at once, for the worker to carry out.
- **Which Switches.** Every Switch not reserved, not Out of service, not being Cleaned up, and with a management IP. Before reading one, it brings its Switch accounts in step, as a Cleanup does.
- **Changed since its last Cleanup** means its config differs from init, or its log shows a login other than BLab's since it last booted. AOS logs each login in `/flash/swlog_chassis<n>` (`n` is the chassis ID: 3 on a VC member that is chassis 3) and starts that file afresh at boot; the rotated files can't be read as `admin`. Every Cleanup reboots the Switch, so the logins in that file are the ones since the last Cleanup (or a later reboot). BLab's own are `admin` from `BLAB_SOURCE_ADDRESSES` (the server's address, `10.69.144.180` by default); any other, a Switch account or `admin` from elsewhere, counts. Both stacks run on the server, so they log in from the same address. A log BLab can't read (a Switch that isn't AOS 8, say) is no reason to Clean up by itself: the Switch is judged on its config.
- **Quarantine.** A Switch that changed is Cleaned up and Inspected once back; any other is Inspected at once. One that isn't clean is Quarantined naming nobody, since it has no holder: an admin clears it. A Quarantine already open stays as it is, still naming whom it named. One that is clean has its Quarantine lifted.
- **Mode.** `BLAB_SWEEP_MODE` in `.env`: `report` (the default) or `enforce`. Pre-prod writes production's database and acts on the real Switches, so the Sweep starts in `report` mode: it Inspects and reads the logins as above, and records the Inspections, but asks for no Cleanup, opens no Quarantine and lifts none. Its report says what it would have done instead: "Would Clean up", "Would Quarantine", "Would lift the Quarantine of". Titouan reads a night of that report on the Lab status page, then sets `enforce` in pre-prod's `.env`, the stage whose worker holds the lock.
- **Report.** Once the Sweep's Cleanups are done, it lists what is wrong: each Switch it looked at that is in Quarantine, what it would have done in `report` mode, and whatever it couldn't do. The Lab status page shows the last Sweep's report only when that list isn't empty.
