# BLab

A lab booking tool: users reserve lab switches and wire them together through the backbone, without touching cables.

## Language

### People

**Account**:
A user's BLab login: a username, which never changes, and an email, the user's Rainbow login, where BLab messages them. An Account without its email can do nothing in BLab until it is set.
_Avoid_: profile; a Switch account is the login BLab makes on a Switch

### Equipment

**Switch**:
A lab switch that users reserve and work on.
_Avoid_: device, equipment (both also cover backbones)

**Backbone**:
A switch that the lab switches are cabled to and that carries the traffic between them. There can be several; Titouan configures the trunks between backbones by hand.
_Avoid_: core, fabric

**UNI** (User Network Interface):
A backbone port facing a lab switch port. A Port record is the pairing of a lab switch port with its UNI.
_Avoid_: access port, backbone port

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

**Reservation**:
A user's time-bounded hold on one Switch, for at most two weeks. A Switch has at most one Reservation at a time; others get to work on it through a shared Topology, never through a second Reservation.
_Avoid_: booking, lease

**Renewal**:
Pushing a Reservation's end date back by one week. A Reservation can be Renewed twice, so it lasts four weeks at most.
_Avoid_: extension, prolongation

**Topology**:
A user's Switches (the ones they hold Reservations on) and every Link with an end on one of them. A Link whose other end is on a Switch outside the Topology still belongs to it. A user can share their Topology with others, who may then see it and work on it as if it were theirs.
_Avoid_: lab, view, setup

**Topology layout**:
Where each Switch is drawn on a Topology, saved per Topology owner so that everyone viewing it sees the same picture. Whoever may work on the Topology arranges it; Re-arrange forgets it, and the Switches are laid out tidily again.
_Avoid_: positions, arrangement

**Release**:
Ending a Reservation: every Link with an end on the Switch is torn down, the banner is updated, and the Switch is Cleaned up. If any Link cannot be torn down, the Reservation stays. Expiry is a Release that BLab triggers itself when the end date passes.
_Avoid_: free, unreserve, delete

**Cleanup**:
Restoring a Switch to its init config and rebooting it.
_Avoid_: reset, wipe

**Inspection**:
Reading a Switch to decide whether it is clean: BLab can log in to it, it stands alone rather than in a VC, it has no Unwanted cable, and no Switch account is left on it once it is not reserved. It only reads.
_Avoid_: health check, audit (audit is the Link Reconcile)

**Unwanted cable**:
A port whose link is up on a Switch that is not reserved, other than the ports paired with a UNI, the management port, and ports an admin has marked as permanently cabled. VC cables count.
_Avoid_: foreign cable, extra cable

**Quarantine**:
Taking a Switch out of reservation because an Inspection after Cleanup found what a Cleanup cannot undo, such as an Unwanted cable. A Quarantine is announced to the whole lab. It names the last holder, who must clear it, unless the Cleanup itself failed or the Switch never came back from it; when it names nobody, an admin clears it. While named in a Quarantine, a user cannot make new Reservations. The Quarantine is lifted when an Inspection finds the Switch clean, whether a user asked for it or the Sweep ran it.
_Avoid_: lock, disable, maintenance

**Re-check**:
An Inspection that anyone may ask for on a Quarantined Switch, to lift the Quarantine once the Switch is clean.
_Avoid_: retry, re-inspect

**Out of service**:
A Switch an admin has taken out of reservation for a fault no user can fix, such as broken hardware, with a reason. Only an admin puts it back; Inspections and the Sweep leave it alone.
_Avoid_: broken, disabled, Quarantine (which a user clears)

**Switch account**:
A login BLab creates on a Switch for one user while they may work on it: the holder, and each user the Topology is shared with. It is named after the user and has a random password that only they see in BLab. BLab removes it at Release, before the Cleanup, or when the Topology stops being shared with them. The `admin` login is BLab's and the admins' alone; users never get it.
_Avoid_: credentials, user/password

**Sweep**:
BLab's nightly pass over every Switch that is not reserved: it Cleans up the ones that changed since their last Cleanup, Inspects all of them, Quarantines or lifts Quarantines accordingly, and reports only if something is wrong.
_Avoid_: nightly cleanup, cron

**Switch worker**:
The process that carries out the Cleanup after each Release: it reloads the Switch, waits for it to come back, Inspects it, and Quarantines it if it isn't clean. Until it is done, the Switch cannot be reserved. Only one Switch worker works at a time across production and pre-prod.
_Avoid_: cleanup job, reload daemon

### Drift

**Drift**:
Any difference between what the database records (Links as Ports sharing an SVLAN, the state of each UNI) and what the backbones hold. The database is the truth for Links; the backbone is the truth for UNI states.
_Avoid_: desync, mismatch

**Orphan**:
Ethernet-service config on a backbone, an SVLAN alone or a whole Service, for an SVLAN that no Port on that backbone records. An SVLAN bound to a trunk between backbones is not an Orphan: Titouan configures those by hand. The Link worker removes the Orphans BLab made (a bare SVLAN, or a Service named `blab_<svlan>` or `<user>_<svlan>`); any other is only reported.
_Avoid_: leftover, stale service

**Ghost Link**:
A Link the database records that a backbone it touches doesn't fully carry: its Service is missing or incomplete, or one of its UNIs is disabled. The user sees a Link that carries no traffic.
_Avoid_: broken link, dead link

**Reconcile**:
Comparing the database with every backbone and listing the Drifts, along with what it couldn't compare (an unreachable backbone, a line it doesn't understand, an SVLAN held by other than two Ports). It only reads; repairing is a separate step that builds Ghost Links again and records the real UNI states.
_Avoid_: sync, audit (audit_links is the command that runs it)

**Link worker**:
The process that carries out the disconnects users ask for, tearing each Link down after the request has returned, and that Reconciles every few minutes, removing BLab's own Orphans, recording the real UNI states, and recording on each Link whether it is a Ghost Link and why, which its Topology shows until a Reconcile finds it carried. A Link being disconnected shows as such on its Topology until it is torn down; if its teardown keeps failing, it shows again with the reason. Only one Link worker works at a time across production and pre-prod.
_Avoid_: daemon, background job
