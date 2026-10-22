# Missing cables are found by enabling the UNIs for a moment

A **Missing cable** (see `CONTEXT.md`, #57) is a Port whose link is down. The Inspection could not simply read that: a UNI without a Link is disabled on its backbone (`api.links`, and `populate_ports` disables them again after discovery), so every Port of a Switch nobody holds is down, cable or no cable. On 10.69.144.130 (2026-10-20), 219 ports were disabled.

So an Inspection of a Switch nobody holds enables, for a few seconds, the disabled UNIs of its Ports without a Link that read down. It reads the Switch's links again every 2 seconds, for up to 15, then disables those UNIs again. A Port still down is a Missing cable. The Quarantine it causes names nobody, since a link can fail on its own. The code is in `api/missing_cables.py`.

## Why it is safe

- **A UNI enabled outside any SAP carries nothing.** VLAN 1 is disabled on the backbone (`vlan 1 admin-state disable`, 10.69.144.130), and a disabled UNI belongs to VLAN 1 only. `populate_ports` already enables every port of a backbone at once to read LLDP.
- **A Connect never loses its UNI to it.** Only a Port without a Link has its UNI enabled. Each UNI is disabled again under the SVLAN lock, and only if its Port still has no Link. A Connect takes that lock to allocate its SVLAN, and enables its UNIs only afterwards, so a Connect made meanwhile keeps its UNI enabled. Nothing is held during the wait, so Connects elsewhere in the lab never stall behind the check.
- **The Link worker isn't misled.** It records a UNI state only after seeing it on two Reconciles in a row, minutes apart, and the check lasts seconds.

## Reserved Switches

Nothing is enabled on a Switch someone holds: its user may be watching its ports. Only the Ports carrying a Link are judged, since their UNIs are enabled, and a Missing cable there is only shown, never a failure. The Inspection lists it as a warning. The Link worker also reads `show interfaces alias` on each backbone at every Reconcile, and records a Link whose enabled UNI is down the way it records a Ghost Link (`ghost_reason`). Its Topology then shows "Carries no traffic" with the Missing cable as the reason, and no migration is needed.

## Rollout: `BLAB_MISSING_CABLE_MODE`

Pre-prod drives the real Switches and backbones within a minute of a push to `dev` (ADR 0002). Enabling UNIs on the backbones is a new kind of automatic write there, and the lab may hold Port records whose cables went long ago: on 10.69.144.130, 17 enabled ports had no link. So the mode has three steps:

- `off`, the default: no UNI is ever enabled. A Switch nobody holds isn't checked for Missing cables. Missing cables on Links are still shown, since that only reads.
- `report`: the check runs, and its Missing cables are warnings of the Inspection, on the Lab status page. Nothing is Quarantined.
- `enforce`: they fail the Inspection, and the Switch is Quarantined naming nobody.

Titouan sets `report` in pre-prod's `.env` (the stage whose Switch worker holds the lock), reads what it finds, has the dead Ports removed or re-cabled, and then sets `enforce` in both `.env` files.

## Considered options

- **Keep every UNI enabled at rest.** Rejected: a Switch nobody holds would sit bridged to the backbone all the time, and enabled UNIs cost the Link worker's checks their meaning (a disabled UNI is how a Ghost Link shows).
- **Judge only the Ports carrying a Link.** Read-only, but a Switch nobody holds has none, so its Inspection would never find a Missing cable. The issue asks for exactly that case.
- **Read the SFP presence on the UNI.** A copper or DAC port tells nothing that way.

## Consequences

- With the mode on, the Inspection is no longer read-only towards the backbone. It still only reads the Switch.
- An Inspection of a Switch with a Missing cable takes about 15 seconds longer, and so does a Sweep over it.
- A Port whose Switch port `show interfaces` doesn't list (a module that is gone, a Port recorded with a typo) is `unreadable`, not a Missing cable. The Quarantine names nobody either way, and an admin fixes the record.
- A Link left on a Switch nobody holds is a teardown under way or a Ghost Link. It is left to the Link worker, not judged as a Missing cable.
- A UNI that can't be disabled again is a warning of the Inspection. It stays enabled, carrying nothing, until the next Connect and Disconnect of its Port, and the Link worker records it as enabled meanwhile.
- Until `main` runs this code, the Link worker on `main` doesn't record Missing cables on Links, and production's Inspections don't look for them.

## Contribute a cable (#58) checks the same way

When a user contributes a cable, BLab must check it joins the proposed Switch port and Backbone port before recording the Port. The issue asked for LLDP on the Backbone to see the right Switch port. But LLDP is disabled on every Backbone port (10.69.144.130, 2026-10-20): a UNI must carry the users' own LLDP through to the other end of their Link, not consume it. Enabling it per port would be one more write to restore exactly.

So BLab checks by link instead (`api/contribute.py`). It enables the proposed Backbone port: the proposed Switch port must come up, and so must the Backbone port. It disables it again: the Switch port must go down. That proves the cable joins exactly those two ports. A cable in another Switch port is named ("the link came up on 1/1/23, not on 1/1/24"). Unlike the Inspection's check, this one doesn't wait on `BLAB_MISSING_CABLE_MODE`: a user asks for it about one cable they have just plugged, and the issue asks for the check.
