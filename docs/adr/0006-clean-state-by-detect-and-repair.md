# Clean state by detect-and-repair

Users leave things on Switches that a Cleanup didn't undo. On 10.69.144.132 (2026-10-12) there were local users, shell and Python histories, scripts in `/flash/python`, crash dumps, a `running.cfg`, and a `vcsetup.cfg` copied into certified. A read of every lab Switch the same day found the same everywhere: AOS images and tools in user folders, captures, packet dumps, and an init changed by hand. The rule is now that once a Reservation ends, nothing of it stays: only AOS's own files and what the lab managers need.

AOS can't stop a user from writing there. It has no permission per folder, and a user who may configure the Switch may also write to `/flash` and `init/`. Weaker Switch accounts would block the work people reserve Switches for. So BLab **detects and repairs**: the Inspection compares the Switch with its clean state, and the Cleanup brings it back to it.

## The clean state

- **Baseline**: what a clean Switch holds under `/flash`, as patterns in `api/clean_state.py`. It was built from 132 right after its Cleanup and from a read-only listing of every reachable lab Switch (OS6900, OS6860E, OS6860N, OS6870, OS9900; AOS 8.9 to 8.11). There is one list for every family: what one family's AOS writes is simply absent on the others. Logs AOS writes itself (`swlog*`, `*.log`, `tech_support*`, `*.err`...) and license files are in it. Anything we weren't sure AOS writes is in it too: a file left behind costs less than a Switch that won't boot.
- **Only some directories are compared**: `/flash` itself, `.ssh`, `python`, `pmd` and `init`, and only their own entries. AOS's own trees (`switch/`, `system/`, `network/`...) are never looked into. `working/` and `certified/` aren't either, because every Cleanup replaces them from `init/`.
- **Extras**: the lab managers' files and logins are `BaselineExtra` rows that an admin edits on the admin site. A Cleanup leaves them in place, and an Inspection doesn't count them.
- **Local users**: every local user but `admin`, AOS's `default` template and the extra logins is Unwanted. That covers Switch accounts BLab failed to remove, and any account made by hand.
- **`init/` extras**: anything in `init/` besides its AOS images, `pkg/` and `vcboot.cfg` is Unwanted, such as a `vcsetup.cfg`, `boot.md5` or `cloudagent.cfg` copied there (found on 10.69.144.168 and .169): AOS writes those in `working/` at boot, and a `vcsetup.cfg` copied into init is how one from another Switch reached certified on 132.
- **Golden init**: `init/vcboot.cfg` must be the lab template that `prepare_switches` writes, named after the Switch's model. Trailing spaces, blank lines and `!` comments don't count. `init/` must also hold an AOS image (`*.img`) and `pkg/`. Which release that image is stays out of scope until #48. A Switch recorded without a model has no Golden init: its init config is neither compared nor rewritten.
- **SU**: a local user other than `admin` and the extras with `SU allowed` (AOS 8.10) can open a root shell. The Inspection reports it, and as a warning even on a reserved Switch. Switch accounts are created without it (checked on 132, AOS 8.10.86.R04).

## How it is repaired

The Switch worker reads the Switch before each Cleanup and works out what to repair. The Cleanup then does the following, in this order. It removes the Unwanted local users with `no user <name>`, which refuses `admin` and `default`. If init's config isn't the Golden init, it rewrites it, and it doesn't reload if that fails. It removes each Unwanted path with `rm -rf -- '/flash/<path>'`, but only for entries of the compared directories that are outside the Baseline. It checks that `init/` holds what a boot needs before it empties `working/`. Then it restores init and reloads, as before. A path or user it can't remove is a warning in the Cleanup's history, and the Inspection after the Cleanup finds it again.

When all an Inspection after a Cleanup finds is what that Cleanup should have repaired (Unwanted files, local users, a changed init, Switch accounts), BLab failed rather than the holder. So the Quarantine names nobody, and an admin clears it. With `BLAB_SWEEP_MODE=enforce`, the Sweep counts the same findings as a change since the last Cleanup, and Cleans the Switch up rather than Quarantining it.

## Rollout: `BLAB_CLEAN_STATE_MODE`

Pre-prod drives the real Switches (ADR 0002), so this would reach the lab as soon as it is on `dev`. The lab must be told first, and the extras must list the lab managers' logins first (monitoring users such as `observium` or `snmp-user` exist on several Switches). So the mode starts at `report`, the default. In `report`, a Cleanup only notes in the Switch's history what it would remove or rewrite (`Would remove (BLAB_CLEAN_STATE_MODE=report): ...`), and an Inspection lists the findings as warnings. In `enforce`, the Cleanup repairs and the Inspection fails on what is left. Titouan fills the extras, announces it, and then sets `enforce` in both `.env` files.

## Considered options

- **Protect `init/` and `/flash` on the Switch.** AOS has no permission per folder, and read-only Switch accounts would stop users configuring the Switch they reserved.
- **Remove everything not in a short list, with no extras.** That would remove the monitoring logins and the lab managers' tools on every Release.
- **Compare `working/` and `certified/` too.** AOS writes files there that vary by family and release (`vcsetup.cfg`, `imgsha256sum`, `appmon_vcboot.cfg`...), which would bring false Quarantines. The Cleanup replaces both from `init/` anyway.
- **Keep other local users as a warning (ADR 0004).** The issue's rule is that nothing of a Reservation stays. A login made by hand keeps a way in after the Reservation ends.

## Consequences

- This amends ADR 0004: in `enforce` mode, local users other than `admin` and the extras fail the Inspection and are removed at every Cleanup.
- Crash dumps (`pmd/pmd-*`), histories and user folders are gone after a Release. Anyone who needs to keep something on every Switch asks an admin to add it to the extras.
- `show interfaces` on AOS 8.10.86 prints the port after the colon (`Chassis/Slot/Port : 1/1/1`). The Inspection now reads both forms. Before this, every Inspection of 132 since its upgrade was `unreadable: show interfaces`.
- Production runs this only once `main` has it. Until then, a Cleanup through production's worker restores init the old way.
- Each AOS release keeps its local users in a table of its own (`/flash/system/userTable<n>`), and a `user` command writes it at once, with no `write memory` (checked on 132, AOS 8.10.86.R04, 2026-10-20). A Cleanup that installs another release, as the first one after the Golden init's release changes does, boots into that release's older table. Users the Cleanup removed come back (`palani` on 155, #74), and so may an older `admin` password (169). So once the Switch is up, the Switch worker removes the other local users again before the Inspection that ends the Cleanup. BLab never changes `admin`, so `BLAB_SWITCH_ADMIN_PASSWORDS` keeps the old passwords for as long as a Switch's other releases may hold them.
