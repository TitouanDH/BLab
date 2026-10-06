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
