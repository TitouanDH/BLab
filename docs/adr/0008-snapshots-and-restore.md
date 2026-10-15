# Snapshots hold the saved config, and the Switch worker Restores them

A Project's work ends with its Reservations, since every Release Cleans the Switch up (ADR 0006). A **Snapshot** (see `CONTEXT.md`) keeps that work, and **Restore** sets it up again on the same Switches (#45). Both sit behind `BLAB_PROJECTS`, because a Snapshot belongs to a Project. With the flag off, a Release takes no Snapshot and the endpoints answer 404.

## Decisions

- **What is kept.** For each Switch, BLab keeps `working/vcboot.cfg`, `working/vcsetup.cfg` when there is one, and the release of the image in `working/` (`show microcode`), all read over SSH as `admin`. When the Snapshot has no `vcsetup.cfg`, Restore removes the one in `working/`, so the Switch boots as it did then. It also keeps the Project's Links, port to port, and its Topology layout. The config is the one the Switch boots, not the running one, so a user saves theirs with `write memory` first. The UI says so. The configs are only ever loaded back: the API never gives them out (a config viewer is #44).
- **Before a Release, one Snapshot per wind-down.** A Snapshot of the whole Project before each Release would be taken N times when a Project of N Switches is released, and the last 10 would soon hold only those. So a Release adds its Switch, its Links and its position to the newest Snapshot, if that one was taken before a Release and nothing has been reserved into the Project since. Otherwise it starts a new one. Each Switch is then kept as it was right before its own Release. The read happens in the Release, before the Links are torn down, and a Switch that can't be read is recorded with the reason. A Snapshot never stops a Release.
- **The last 10 per Project**, Saves and Releases together. A Snapshot that a Restore under way reads isn't pruned until that Restore is done.
- **Restore** reserves the chosen free Switches into the Project, for the member who asks, for the default 7 days. A Switch already in the Project has its config replaced. A busy Switch (held by someone else, being Cleaned up, in Quarantine, Out of service) is left out, and a held one shows when it will be free. The confirm dialog reads each Switch's release and warns when it differs from the Snapshot's; the config is loaded anyway (#49 will install the right release). A user named in a Quarantine can't Restore, just as they can't Reserve.
- **The Switch worker carries it out**, under its Cleanup lock. For each Switch it brings the Switch accounts and the banner in step, writes the config into `working/`, and reloads from working. `init/` and `certified/` are left alone, so the next Cleanup brings the Switch back to its Golden init. It then waits for the Switch to answer again (20 minutes at most, as for a Cleanup). Once every Switch is settled, it builds the Snapshot's Links between the Switches it Restored. Each step is saved on a `RestoreSwitch` row, which is also the progress the UI shows, so a redeploy only delays a Restore. Only one Restore per Project runs at a time, and Save waits for it.

## Considered options

- **Snapshot the running config (`show configuration snapshot`).** Rejected: the issue asks for the files, and loading a file back is what AOS boots from. A running config that was never saved would also be a surprise on the Switch after a reload.
- **Restore inside the request.** Rejected for the reasons ADR 0005 gives: reloads take minutes, and a redeploy would cut them off.
- **Load into `init/` too.** Rejected: init must stay the Golden init (ADR 0006).

## Consequences

- The Switch worker still works through the Switches one after another (ADR 0005): each cycle moves every Restored Switch that is due one step on, in turn, as it does Cleanups. `RestoreWorker.advance()` touches one Switch only, so #39 can run several at once.
- Until `main` runs this code, a worker on `main` that holds the Cleanup lock doesn't carry Restores out. Both stages get it at the maintenance window that turns `BLAB_PROJECTS` on (ADR 0007).
- Saving reads every Switch of the Project over SSH inside the request, which takes a few seconds per Switch.
