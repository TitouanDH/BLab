# BLab

A lab booking tool: users reserve lab switches and wire them together through the backbone, without touching cables.

## Language

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
A user's time-bounded hold on one Switch. A Switch has at most one Reservation at a time; others get to work on it through a shared Topology, never through a second Reservation.
_Avoid_: booking, lease

**Topology**:
A user's Switches (the ones they hold Reservations on) and every Link with an end on one of them. A Link whose other end is on a Switch outside the Topology still belongs to it. A user can share their Topology with others, who may then see it and work on it as if it were theirs.
_Avoid_: lab, view, setup

**Release**:
Ending a Reservation: every Link with an end on the Switch is torn down, the banner is updated, and the Switch may be Cleaned up. If any Link cannot be torn down, the Reservation stays. Expiry is a Release that BLab triggers itself when the end date passes, and it always Cleans up.
_Avoid_: free, unreserve, delete

**Cleanup**:
Restoring a Switch to its init config and rebooting it.
_Avoid_: reset, wipe

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
The process that carries out the disconnects users ask for, tearing each Link down after the request has returned, and that Reconciles every few minutes, removing BLab's own Orphans and recording the real UNI states. A Link being disconnected is hidden from its Topology; if its teardown keeps failing, it shows again with the reason. Only one Link worker works at a time across production and pre-prod.
_Avoid_: daemon, background job
