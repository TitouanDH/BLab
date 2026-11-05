# A VC member is split at its Cleanup, by a chassis reload from its master

Users build VCs during their Reservations: souf joined 10.69.144.138 and .139, and .131 was found in a VC with 10.69.144.176, which isn't a BLab Switch. Titouan's rule (2026-11-04): a reserved Switch isn't checked, so a VC built during a Reservation is fine. At and after the Release, the Cleanup brings each member back to a **standalone** Switch: Golden VC setup, no auto-VFL port. Until #120 the Cleanup did the opposite: it kept a VC's `init/vcsetup.cfg` (`VC_SETUP_KEPT`, #109), so the VC survived the Release.

## What AOS does

Tested on .138/.139 (OS6900-X24C2, AOS 8.11.162.R01 and 8.9.221.R03, 2026-11-05, #112):

- Every member answers on its own management IP and keeps its own `/flash`: the master doesn't sync over what is written to a slave's `working/` or `certified/`.
- A slave refuses every reload (*You must run this command from the master chassis primary CMM*, *Command not allowed on slave*). BLab's session sees that refusal as a closed socket, the same as a reload that starts, so the role is read first (`show virtual-chassis topology`), never inferred from a reload.
- On the master, `reload chassis-id N all` reloads chassis N alone, the master's own included (a slave takes over). It takes no directory, and the member boots its `certified/`. With the Golden VC setup there, it comes up standalone at once.
- `reload from working` on a VC's master reloads every member, reserved ones included.

## Decision

A Cleanup whose Switch's topology lists more than one chassis (and whose model has a Golden VC setup) splits it:

1. Its own flash is prepared through its own IP, as for any Switch (local users, Unwanted files, Golden init and Golden VC setup, the #117 reinstall), then `init/` is copied into `working/` **and** `certified/`, since the chassis reload boots `certified/`.
2. Its VC's master reloads it alone: `reload chassis-id <its chassis> all`, through the master's session, even when the member is the master itself. The master is found by its MAC: the member's topology names it, and a BLab Switch of the same model whose own topology says it is master with that MAC is it. Nothing is stored: the candidates are read live at each split, which is rare.
3. A member whose master isn't a BLab Switch is not reloaded, and nothing is sent to that master. Its Cleanup fails with `not split from its VC, for an admin to do`, and its Quarantine names nobody.
4. Once back, standing alone and running `certified/`, it is reloaded from working/ (now its own master) and waited for again. Then the usual Inspection and `certify`. One that came back still in a VC isn't reloaded again: its Inspection says it is in a VC.
5. The last member left is a VC of one and takes the normal path. The Golden VC setup makes it chassis 1, so its `2/1/x` ports become `1/1/x` at that reload, and the Inspection reads them afterwards.
6. One split at a time, whichever VC: a member's Cleanup waits, unstarted, while another split is under way (until that Cleanup is done), and a lock in the Switch worker keeps two threads from starting splits together. Otherwise a slave's chassis reload could be sent to a master that its own Cleanup is reloading, and AOS may still list a member going down for a while.

The split's steps are notes in the Cleanup's entry of the history (`Split from its VC: ...`, `After the split: ...`), which is how the worker knows where a split is after a redeploy: no migration.

## Considered options

- **Keep the VC's setup and let an admin split it** (`VC_SETUP_KEPT`, #109). Against the rule: nothing of a Reservation stays.
- **`reload from working` on the member.** A slave refuses it, and on a master it reloads every member, reserved ones included.
- **Store each Switch's MAC.** A model change to save reading a few Switches of the same model at a rare split. A master of another model in a mixed VC isn't found, and its member is Quarantined for an admin.
- **Serialise only the members of the same VC.** It needs every member's MAC before deciding, at each cycle a split waits. Splits are rare, so one at a time across the lab costs little.

## Consequences

- Tested with the Switch worker's own code on .138/.139 (2026-11-05 afternoon), rebuilding souf's VC each time: the slave .139 split through its master .138, which stayed up; then the master .138 split by its own chassis reload, and .139 stayed up and took over; then .139, a VC of one on chassis 2, took the normal path and came back as chassis 1, its ports read as `1/1/x`. Each ended standalone, static mode, no auto-VFL port, running `working/`.

- With `BLAB_CLEAN_STATE_MODE=report`, `init/vcsetup.cfg` isn't rewritten: a member whose `init/` holds its VC setup boots back into the VC at the chassis reload, and its Inspection says it is in a VC.
- AOS 6 Switches, chassis and Management switches have no Golden VC setup and are never split.
- A Restore or a member's AOS install still reloads from working/: a VC built during a Reservation is the holder's to deal with until the Release.
