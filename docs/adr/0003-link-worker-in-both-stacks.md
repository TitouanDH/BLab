# One Link worker at a time, run by both production and pre-prod

Disconnecting a Link no longer happens inside the request. `POST disconnect/` records the request on the Ports (`teardown_requested_at`, and `teardown_svlan`, the SVLAN it was made on) and returns at once, and the **Link worker** tears the Link down. The worker is a separate process, the `link_worker` compose service, so that a redeploy (every push to `dev` or `main` restarts the containers) can't cut a teardown in half: the request is in the database, and the next worker picks it up. The same worker reconciles every few minutes. It removes the Orphans BLab made, which are bare SVLANs or Services named `blab_<svlan>` / `<user>_<svlan>`, and records the real admin state of the UNIs.

Both stacks run a worker, but only one works at a time: it holds a Postgres session advisory lock on the shared database, and the other waits as a standby. Production can't be the only one to run it, unlike the expiry job (ADR 0002). Production runs `main`, which may be weeks behind `dev`, and a `main` without the worker would leave pre-prod's disconnects waiting.

## Considered options

- **Only production runs the worker, like the expiry job.** Rejected: pre-prod disconnects would wait for the next `main` release.
- **A thread in the django process after the response.** Rejected: a redeploy kills it halfway, and gunicorn's several processes would each need their own sweeper.
- **Each stack processes only the requests made through it.** Rejected: both stacks act on the same Links, and a stack being redeployed would leave its requests waiting.

## Consequences

- Whichever stack holds the lock carries out the teardowns, possibly with code older or newer than the stack where the request was made. Teardown is `links.disconnect`, which both stacks share, so this matters little. A change to how teardown works is only fully live once it is on `main`.
- A Release and the worker may reach the same Link. Each teardown takes a per-SVLAN advisory lock and first checks that the Link is still recorded with the same Ports, so the second one does nothing, even if the SVLAN already carries a new Link.
- Code from before this change ignores the teardown columns. A request counts only while the Port still holds the SVLAN it was made on, so a Port that older code unlinks or relinks is never torn down because of an old request.
- Removing an Orphan happens under the SVLAN lock, and only after the Orphan has shown up on two reconciles in a row, so a connect in progress is never mistaken for one. Code from before this change connects without that lock and takes the lowest free SVLAN, which can be the Orphan being removed. That window is well under a second. When it happens, the worker logs an error asking for `manage.py audit_links --repair`. It closes once `main` runs this code.
- Local dev with fake devices needs `python manage.py link_worker` running for disconnects to complete.
