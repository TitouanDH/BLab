# Port Population Management Command

This document explains how to use the `populate_ports` Django management command to automatically discover and populate port connections between switches and the backbone infrastructure.

## Overview

The `populate_ports` command uses LLDP (Link Layer Discovery Protocol) to discover physical connections between switches and backbone switches. It creates Port database entries with the following information:

- **switch**: Reference to the Switch object
- **port_switch**: Port identifier on the local switch (e.g., "1/1/1")
- **backbone**: IP address of the backbone switch
- **port_backbone**: Port identifier on the backbone switch (e.g., "1/2/1")
- **svlan**: Initially null (used for service connections)
- **status**: Initially "DOWN"

## Architecture

Your lab setup:
- **Backbone Switch**: OS10K_BLAB at 10.69.144.130 (with future expansion capability)
- **Access Switches**: OS6900 series switches connected to the backbone
- **QinQ Tunneling**: Backbone provides transparent Layer 2 connectivity between switches

## Usage

### Method 1: Automatic Discovery (Recommended)

```bash
# Uses all switches from database and default backbone
python manage.py populate_ports

# Specify backbone explicitly
python manage.py populate_ports --backbone-ips "10.69.144.130"
```

### Method 2: Specific Switch List

```bash
# Scan specific switches
python manage.py populate_ports --switch-ips "10.69.144.131,10.69.144.132"

# Use switch file
python manage.py populate_ports --switch-file switch_ips.txt
```

### Command Options

- `--backbone-ips`: Comma-separated list of backbone IP addresses
- `--backbone-file`: Path to file containing backbone IP addresses
- `--switch-ips`: Comma-separated list of switch IP addresses to scan
- `--switch-file`: Path to file containing switch IP addresses
- `--username`: SSH username (default: admin)
- `--password`: SSH password (default: switch)
- `--update`: Update existing ports instead of skipping them
- `--skip-backbone-enable`: Skip enabling/disabling backbone ports (use if manually configured)

### Examples

```bash
# Full automatic discovery
docker-compose exec api python manage.py populate_ports

# Manual backbone and switch specification
docker-compose exec api python manage.py populate_ports \
  --backbone-ips "10.69.144.130" \
  --switch-ips "10.69.144.131,10.69.144.132"

# Update existing ports
docker-compose exec api python manage.py populate_ports --update

# Skip backbone port management (if manually configured)
docker-compose exec api python manage.py populate_ports --skip-backbone-enable
```

## How It Works

### Step 1: Backbone Port Enablement (automatic)
The command automatically enables backbone ports for LLDP discovery:

```bash
# On backbone (10.69.144.130)
lldp nearest-bridge chassis lldpdu tx-and-rx
interfaces 1/1/1-48 admin-state enable
interfaces 1/2/1-32 admin-state enable
interfaces 1/3/1-48 admin-state enable
interfaces 1/4/1-8 admin-state enable
interfaces 1/5/1-32 admin-state enable
interfaces 1/6/1-32 admin-state enable
interfaces 1/7/1-48 admin-state enable
interfaces 1/8/1-8 admin-state enable
```

### Step 2: LLDP Discovery
For each switch, the command:
1. Connects via SSH
2. Executes `show lldp remote-system`
3. Parses the output to find backbone connections

### Step 3: Port Database Population
Creates Port entries mapping:
- Switch port (e.g., "1/1/1") → Backbone port (e.g., "1/2/1")
- Switch IP → Backbone IP (10.69.144.130)

### Step 4: Backbone Port Disablement
After discovery, backbone ports are disabled for transparent operation:

```bash
# On backbone
lldp nearest-bridge chassis lldpdu disable
interfaces 1/1/1-48 admin-state disable
# (all ranges disabled)
```

## Expected LLDP Output

### From Switch (OS6900):
```
OS6900-V48C8 show lldp remote-system
Remote LLDP nearest-bridge Agents on Local Port 1/1/1:

    Chassis 00:e0:b1:fd:f4:03, Port 2001:
      Remote ID                   = 7,
      Chassis Subtype             = 4 (MAC Address),
      Port Subtype                = 7 (Locally assigned),
      Port Description            = Alcatel-Lucent OS10K XNI 1/2/1,
      System Name                 = OS10K_BLAB,
      System Description          = Alcatel-Lucent Enterprise OS10K...,
      Management IP Address       = 0.0.0.0,
      Mau Type                    = 10GigBaseSR
```

### From Backbone (OS10K):
```
OS10K_BLAB show lldp remote-system
Remote LLDP nearest-bridge Agents on Local Port 1/2/1:

    Chassis 94:24:e1:87:e8:b3, Port 1001:
      System Name                 = OS6900-V48C8,
      Port Description            = Alcatel-Lucent OS6900 XNI 1/1/1,
      ...
```

## Database Result

After running the command, the Port table will contain entries like:

| switch | port_switch | backbone | port_backbone | svlan | status |
|--------|-------------|----------|---------------|-------|--------|
| OS6900-V48C8 (10.69.144.131) | 1/1/1 | 10.69.144.130 | 1/2/1 | null | DOWN |
| OS6900-V48C8 (10.69.144.131) | 1/1/2 | 10.69.144.130 | 1/2/2 | null | DOWN |

## Output Example

```
Discovering ports for 6 switch(es) and 1 backbone(s)...
  Enabling backbone ports on 10.69.144.130 for discovery...
  ✓ Backbone ports enabled on 10.69.144.130

Processing switch: 10.69.144.131
  Connecting to 10.69.144.131...
  Executing "show lldp remote-system" command...
  Successfully retrieved LLDP information
  Found connection: 1/1/1 -> OS10K_BLAB:1/2/1
  Found connection: 1/1/2 -> OS10K_BLAB:1/2/2
  ✓ Created port 1/1/1 -> 10.69.144.130:1/2/1
  ✓ Created port 1/1/2 -> 10.69.144.130:1/2/2
✓ Switch 10.69.144.131: 2 ports created, 0 ports updated

  Disabling backbone ports on 10.69.144.130...
  ✓ Backbone ports disabled on 10.69.144.130

==================================================
Summary:
  Successfully processed: 6
  Skipped (already exist): 0
  Failed: 0
==================================================
```

## Integration with Existing System

### Port Usage in Reservations
Once populated, ports are used by the existing system for:
- **Link Creation**: `Port.create_link()` method for QinQ tunnels
- **Port Management**: Up/down operations
- **Service VLANs**: Dynamic SVLAN assignment
- **Cleanup**: Automatic disconnection during reservation cleanup

### Workflow Integration

1. **Switch Discovery**: `populate_switches` to get hardware info
2. **Switch Preparation**: `prepare_switches` to configure basic settings
3. **Port Discovery**: `populate_ports` to map physical connections
4. **Ready for Use**: Switches and ports ready for reservations

## Prerequisites

- Switches must be accessible via SSH
- LLDP must be working on switch uplinks (usually enabled by default)
- Backbone switch must be accessible for temporary port enablement
- Switch database must be populated first (`populate_switches`)

## Error Handling

The command handles:
- SSH connection failures
- LLDP parsing errors
- Missing backbone connections
- Network timeouts
- Partial discovery failures

## Security Note

The command temporarily enables backbone ports only for discovery, then immediately disables them to maintain network security and transparency for the QinQ tunneling operation.
