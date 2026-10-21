# BLab

A lab booking tool: users reserve lab switches and wire them together through the backbone, without touching cables.

## Language

### People

**Account**:
A user's BLab login: a username, which never changes, and an email, the user's Rainbow login, where BLab messages them. An Account without its email can do nothing in BLab until it is set.
_Avoid_: profile; a Switch account is the login BLab makes on a Switch

### Equipment

**Switch**:
A lab switch that users reserve and work on, a chassis as much as a 1U switch. The lab's core switches, the Management switches and the servers are not Switches.
_Avoid_: device, equipment (both also cover backbones)

**Backbone**:
A switch that the lab switches are cabled to and that carries the traffic between them. There can be several; Titouan configures the trunks between backbones by hand.
_Avoid_: core, fabric

**Tool**:
Equipment that is not a Switch but whose ports are cabled to UNIs so that users can make Links to them: a traffic generator such as the IXIA, or a server running virtual AOS (GNS3). It is not reserved, has no Switch accounts, and is never Cleaned up or Inspected. Each of its Ports is taken while it carries a Link and free otherwise, so one Tool serves many Projects at once; the Link, and the hold on the port with it, ends when the Switch at its other end is Released.
_Avoid_: Switch (even though the IXIA was first recorded as one), test device

**UNI** (User Network Interface):
A backbone port facing a lab switch port or a Tool port. A Port record is the pairing of a lab switch port (or Tool port) with its UNI.
_Avoid_: access port, backbone port

**Rack** and **Row**:
The lab is rows of racks, with one Backbone per row and one Management switch per rack. BLab does not record where a device sits: a cable is named by its Switch port and its UNI.
_Avoid_: cabinet, bay

**Management network**:
The lab's management subnets (one or more) and VLAN, which every Switch, Backbone and Management switch is reached through. BLab picks a new device's management IP in it: an address no device records and nothing answers on when scanned. No hand-kept address plan is trusted; what BLab records and what answers are the plan.
_Avoid_: admin network, EMP network (the EMP is only one way onto it)

**Management switch**:
The out-of-band switch at the top of each rack that puts that rack's devices on the Management network. It only bridges; it is not a Switch users reserve.
_Avoid_: OoB, ToR (alone), core

**Management port**:
The port a Switch reaches the Management network through, cabled to its rack's Management switch: its EMP when it has one, otherwise its last front port, given over to management for good. It is never a Link end and never an Unwanted cable.
_Avoid_: EMP (that is only one kind), OoB port

**Contribution**:
A user adding equipment to the lab with BLab guiding them: a new Switch (given its management IP, onboarded and Cleaned up) or a new cable between a Switch and a Backbone, which gives the Switch one more Port. BLab checks each step itself and takes the Contribution in once the checks pass, with no admin approval; a contributed Switch is Out of service until its first Inspection finds it clean, and may go live with no cable. Only an admin removes equipment. The Backbone port a contributed cable goes into is no UNI until BLab records the Port: BLab checks the cable by enabling that port for a moment.
_Avoid_: onboarding (only one step of it), import

### Wiring

**SVLAN**:
The outer VLAN tag (QinQ) that carries one Link's traffic across every backbone. Unique across the whole lab, not per backbone.
_Avoid_: VLAN, service VLAN id

**Link**:
A virtual cable between exactly two Ports, carried by one SVLAN. It may span one backbone or several. More than two Ports sharing an SVLAN is an inconsistency, not a bigger Link.
_Avoid_: connection, tunnel, wire

**Service**:
The ethernet-service configured on each backbone that carries part of a Link: the SVLAN, a service name, and a SAP holding that backbone's UNIs. A Link has one Service per backbone it touches.
_Avoid_: SAP (that is only one part of it)

### Booking

**Project**:
A named piece of work: the Switches reserved into it and the users who are its members. The member who created it owns it, and may renew its Invite link and remove members; every member may work on all its Switches. A Project outlives its Reservations, so its members can reserve into it again; the owner may archive it once it holds none.
_Avoid_: case (a support case is only the Project's optional reference), lab, team

**Member**:
A user who belongs to a Project, by creating it or by joining it through its Invite link. A member may leave; leaving or being removed takes away their Switch accounts on the Project's Switches.
_Avoid_: share, guest

**Invite link**:
The one link of a Project that lets any BLab user join it after confirming. The owner renews it to make the old one stop working.
_Avoid_: share link, token

**Reservation**:
A user's time-bounded hold on one Switch, for at most two weeks, made into one Project. A Switch has at most one Reservation at a time; the other members of its Project work on it through that Reservation, never through a second one. The user who made it stays its holder. Whoever may work on it may move it to another Project the holder is a member of, without a Release, once it has no Link.
_Avoid_: booking, lease

**Renewal**:
Pushing a Reservation's end date back by one week. A Reservation can be Renewed twice, so it lasts four weeks at most.
_Avoid_: extension, prolongation

**Topology**:
The picture of a Project: its Switches and the Links between them. Both ends of a Link are always in the same Project.
_Avoid_: lab, view, setup

**Topology layout**:
Where each Switch is drawn on a Topology, saved per Project so that every member sees the same picture. Any member arranges it; Re-arrange forgets it, and the Switches are laid out tidily again.
_Avoid_: positions, arrangement

**Release**:
Ending a Reservation: every Link with an end on the Switch is torn down, the banner is updated, and the Switch is Cleaned up. If any Link cannot be torn down, the Reservation stays. Expiry is a Release that BLab triggers itself when the end date passes. An admin may Release any Reservation, and may force it: a Link that cannot be torn down is then forgotten, and what it leaves on a backbone is an Orphan. A Reservation never ends any other way.
_Avoid_: free, unreserve, delete

**Cleanup**:
Bringing a Switch back to its clean state and rebooting it: its init is repaired from the Golden init if a user changed it, then copied to working; every file outside the Baseline and every local login other than `admin` and the lab managers' is removed. Once a Reservation ends, nothing of it stays on the Switch. Besides a Release and the Sweep, an admin may ask for one on any Switch that is not reserved.
_Avoid_: reset, wipe

**Inspection**:
Reading a Switch to decide whether it is clean: BLab can log in to it, it stands alone rather than in a VC, it has no Unwanted cable, no Missing cable and no Unwanted file, its init matches its Golden init, and no Switch account, nor any other local login but `admin` and the lab managers', is left on it once it is not reserved. It only reads the Switch; to tell a Missing cable on a Switch nobody holds, it enables that Switch's disabled UNIs for a few seconds and disables them again. An admin may Inspect any Switch but one being Cleaned up; on one that is not reserved and not Out of service, BLab then Quarantines it or lifts its Quarantine as the Sweep would.

**Golden init**:
What a Switch's `init` directory must hold: the lab's config for its model (the same for every Switch but for the system name) and the latest AOS release for its model family, which a Cleanup installs, so the release changes only while nobody holds the Switch. Users cannot be kept from changing `init` on the Switch, so BLab detects it and repairs it from the Golden init at Cleanup.
_Avoid_: backup, reference config

**Baseline**:
The files a clean Switch holds: what AOS itself needs and what the lab managers put there on purpose (with their logins, the extras an admin lists). Logs AOS rewrites on its own are not compared.
_Avoid_: whitelist, allowlist

**Unwanted file**:
A file or directory on a Switch that is not reserved and that is outside the Baseline, such as a script, a history, a crash dump or a config a user left.
_Avoid_: leftover, junk
_Avoid_: health check, audit (audit is the Link Reconcile)

**Unwanted cable**:
A port whose link is up on a Switch that is not reserved, other than the ports paired with a UNI, the management port, and ports an admin has marked as permanently cabled. VC cables count.
_Avoid_: foreign cable, extra cable

**Missing cable**:
A Port whose link is down on the Switch or on its UNI. A UNI without a Link is disabled, so its link is down cable or not: on a Switch nobody holds, the Inspection enables it for a moment to tell (once `BLAB_MISSING_CABLE_MODE` allows it). Unlike an Unwanted cable it may not be anyone's doing (a link can fail on its own), so the Quarantine it causes names nobody. Plugging it back and a Re-check lift the Quarantine; if the cable is meant to go, an admin removes the Port. On a reserved Switch it is only shown: on the Lab status page, and on its Link, which then carries no traffic, like a Ghost Link.
_Avoid_: broken cable, down port

**Quarantine**:
Taking a Switch out of reservation because an Inspection found what a Cleanup cannot undo, such as an Unwanted cable, a Missing cable, or the Switch being unreachable. A Quarantine is announced to the whole lab. It names the last holder, who must clear it, only for an Unwanted cable, Unwanted file or changed init left after a Reservation that an admin did not Release; when it names nobody (a Missing cable, an unreachable Switch, a failed Cleanup, a Switch that never came back from one, or what an admin's Release, Cleanup or Inspection found), an admin clears it. An admin may also un-name the holder, leaving the Quarantine open for an admin to clear. While named in a Quarantine, a user cannot make new Reservations. The Quarantine is lifted when an Inspection finds the Switch clean, whether a user asked for it or the Sweep ran it.
_Avoid_: lock, disable, maintenance

**Re-check**:
An Inspection that anyone may ask for on a Quarantined Switch, to lift the Quarantine once the Switch is clean.
_Avoid_: retry, re-inspect

**Out of service**:
A Switch an admin has taken out of reservation for a fault no user can fix, such as broken hardware, with a reason. Only an admin puts it back; Inspections, the Sweep and the Switch worker's retries of Switch accounts and banners leave it alone.
_Avoid_: broken, disabled, Quarantine (which a user clears)

**Switch account**:
A login BLab creates on a Switch for one user while they may work on it: each member of the Project the Switch is reserved into. It is named after the user and opens with their Switch password. BLab removes it at Release, before the Cleanup, or when the user stops being a member of the Project. The `admin` login is BLab's and the admins' alone; users never get it.
_Avoid_: credentials, user/password

**Switch password**:
The one password a user chooses for all their Switch accounts, set when they first log in to BLab. Changing it changes every Switch account they have. It cannot be the lab's old shared password.
_Avoid_: Switch account password, credentials

**Snapshot**:
What a Project's Switches were set up as at one moment: each Switch's saved config (what it boots, so unsaved changes are not in it) and AOS version, the Project's Links and its Topology layout. BLab takes one before every Release and whenever a member saves, and keeps the last 10 per Project; Releasing a Project's Switches one after another adds each to the same Snapshot. It holds neither files nor Switch accounts.
_Avoid_: backup, save, checkpoint

**Restore**:
Setting a Project's Switches up again from a Snapshot: reserving them, loading each one's config, and building its Links again. A Switch that is not free is left out, or replaced by a free Switch a member picks among those with enough cabled ports; BLab then rewrites the config and the Links for it and shows the difference before loading it.
_Avoid_: rollback, boot (of a Project)

**Sweep**:
BLab's nightly pass over every Switch that is not reserved: it Cleans up the ones that changed since their last Cleanup, Inspects all of them, Quarantines or lifts Quarantines accordingly, and reports only if something is wrong.
_Avoid_: nightly cleanup, cron

**Switch worker**:
The process that carries out the Cleanup after each Release: it reloads the Switch, waits for it to come back, Inspects it, and Quarantines it if it isn't clean. Until it is done, the Switch cannot be reserved. Only one Switch worker works at a time across production and pre-prod; it works on several Switches at once, but never twice on the same one.
_Avoid_: cleanup job, reload daemon

### Drift

**Drift**:
Any difference between what the database records (Links as Ports sharing an SVLAN, the state of each UNI) and what the backbones hold. The database is the truth for Links; the backbone is the truth for UNI states.
_Avoid_: desync, mismatch

**Orphan**:
Ethernet-service config on a backbone, an SVLAN alone or a whole Service, for an SVLAN that no Port on that backbone records. An SVLAN bound to a trunk between backbones is not an Orphan: Titouan configures those by hand. The Link worker removes the Orphans BLab made (a bare SVLAN, or a Service named `blab_<svlan>` or `<user>_<svlan>`); any other is only reported.
_Avoid_: leftover, stale service

**Ghost Link**:
A Link the database records that a backbone it touches doesn't fully carry: its Service is missing or incomplete, or one of its UNIs is disabled. The user sees a Link that carries no traffic. Its Topology shows a Link with a Missing cable the same way.
_Avoid_: broken link, dead link

**Reconcile**:
Comparing the database with every backbone and listing the Drifts, along with what it couldn't compare (an unreachable backbone, a line it doesn't understand, an SVLAN held by other than two Ports). It only reads; repairing is a separate step that builds Ghost Links again and records the real UNI states.
_Avoid_: sync, audit (audit_links is the command that runs it)

**Link worker**:
The process that carries out the disconnects users ask for, tearing each Link down after the request has returned, and that Reconciles every few minutes, removing BLab's own Orphans, recording the real UNI states, and recording on each Link whether it is a Ghost Link and why, which its Topology shows until a Reconcile finds it carried. A Link being disconnected shows as such on its Topology until it is torn down; if its teardown keeps failing, it shows again with the reason. Only one Link worker works at a time across production and pre-prod.
_Avoid_: daemon, background job
