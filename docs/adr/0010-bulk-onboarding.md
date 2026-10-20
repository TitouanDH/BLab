# Switches are onboarded in bulk by one rerunnable command

Titouan racks about 120 Switches at once. Onboarding them took three commands run by hand on an IP list: `populate_switches`, `prepare_switches` and `populate_ports`. Nothing scanned a subnet. `onboard_switches` (#55, `api/onboarding.py`) now takes a subnet of the Management network (`BLAB_MANAGEMENT_NETWORKS`, each subnet with its gateway) and does all of it. Each address's step is kept in an `Onboarding` row, so running it again goes on from there. It ends with a report of where each address stopped and why.

## Decisions

- **A dry run unless `--apply`.** Without it, the command scans and logs in, reads `show system` and `show chassis`, and says what it would onboard and what it leaves out. The racks' Management switches (OS2260) run AOS too: onboarding one would install the Golden init and reload it, cutting a whole rack off the Management network. So they are left out by model, and the dry run shows the list before anything is written.
- **What is left out:** the gateway, the Backbones, every device BLab records, anything that doesn't answer SSH or BLab's admin login, anything that doesn't say it runs AOS, the Management switches (OS2260, and OS2360 in case one comes), and VCs. A VC's chassis are onboarded once each stands alone, since a Golden init is for one chassis.
- **The secret admin password first** (amends ADR 0004). Every lab Switch must have the lab's secret `admin` password (Titouan, 2026-10-20). A Switch BLab can only log in to with a factory password (`switch`, `Switch@123`, the later entries of `BLAB_SWITCH_ADMIN_PASSWORDS`) has just been onboarded. So onboarding sets the first entry and saves it. That is the one case where BLab changes `admin`.
- **Out of service while it is onboarded.** The Switch is recorded with the reason "Being onboarded by BLab (onboard_switches)". Nothing reserves it, Sweeps it or makes Switch accounts on it meanwhile. Its first clean Inspection puts it in service. One that isn't clean stays Out of service, with why, and the next run Inspects it again. Only that reason is ever lifted this way: an admin's Out of service stays.
- **Every Switch reloads at once**, from the command, not one after another through the Switch worker (ADR 0005). The worker Cleans up one Switch at a time (#39), which for 120 Switches would take most of a day. The Cleanup itself is the same: the Golden init installed in `init/`, the clean-state repair planned by `switch_worker.plan_repair`, then `restore_init_and_reload`. It then waits for each Switch to have really rebooted and be ready (`api.reboot`), 20 minutes at most. The step is saved before the reload, so a run killed meanwhile doesn't reload the Switch twice. An admin's "Clean up now" is refused on a Switch being onboarded.
- **Ports by LLDP, in one window, on the free Backbone ports only.** A Backbone has LLDP off and its unused ports disabled. As `populate_ports` does, the command turns LLDP on and enables ports, reads each Switch's `show lldp remote-system`, then disables them and turns LLDP off again. Unlike `populate_ports`, it enables only the ports Contribute a cable calls free (`api.contribute`): disabled, no link, no Port, named nowhere in the VLAN config, on a module that holds UNIs. It never touches the UNIs of existing Ports or Links, nor trunks and uplinks. It records Ports for the new Switches only. The ports it enabled are saved in an `LldpWindow` row before it enables them. A run killed during the window leaves the next run what to disable, and that run closes it first. The Backbones are those BLab's Ports name, plus any given with `--backbones` (a new row's Backbone).

## Considered options

- **Hand the Cleanups to the Switch worker.** Rejected for now: one at a time (#39).
- **Run `populate_ports` as it is.** Rejected: it enables every port of the Backbones, including the UNIs of users' Links and the trunks, and rewrites the Ports of every Switch.
- **No dry run.** Rejected: one Management switch onboarded by mistake takes a rack off the network.

## Consequences

- During the LLDP window, the Backbones send LLDP on every port, the UNIs of Links included, for about 40 seconds, as `populate_ports` did.
- The command runs against real devices only when Titouan runs it. Agents never run it against the lab without his go.
- Contribute a Switch (#59) goes through the same steps for one Switch.
