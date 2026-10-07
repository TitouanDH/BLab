# Users get their own Switch accounts; `admin` stays secret

The lab got messy because anyone could log in to any Switch with the default `admin/switch`, so nobody reserved, and nobody could be held to cleaning up. We now keep the lab Switches' `admin` password secret, known only to BLab and the admins, and stored in deployment secrets rather than in the code. Users reach a Switch only through a **Switch account**: a local login named after them, with a random password. BLab creates it on Reserve for the holder and for each user the Topology is shared with, and removes it at Release before the Cleanup. Backbones keep their current credentials for now.

## Considered options

- **RADIUS/TACACS behind BLab.** Rejected: too much infrastructure for a lab, and BLab's own access would depend on it.
- **BLab sets a new `admin` password for each Reservation and gives it to the holder.** Rejected: a holder who changes it locks BLab out, so the Switch can no longer be Cleaned up, and the command log shows `admin` instead of a person.

## Consequences

- Cleanup doesn't touch the local user table, because it lives outside `working/`. Removing Switch accounts is a separate step of Release, and an Inspection fails if a Switch account is still there once the Switch isn't reserved.
- A holder can still lock BLab out by changing `admin` or removing SSH authentication. The Inspection after Cleanup then fails, the Switch is Quarantined in their name, and an admin recovers it at the console. There is no console server.
- The `admin` password changes on the devices only after BLab has shipped Switch accounts, so holders always have a way in.
- Scripts that hard-code `admin/switch` stop working on lab Switches.
