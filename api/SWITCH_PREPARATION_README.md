# Switch Preparation Management Command

This document explains how to use the `prepare_switches` Django management command to prepare newly installed switches with a clean configuration.

## Overview

The `prepare_switches` command performs the following steps on each switch:

1. **Cleanup**: Removes old log and configuration files
2. **Init Setup**: Creates the `init` folder and copies necessary files
3. **Configuration**: Creates `vcboot.cfg` with basic switch configuration
4. **Optional Cleanup**: Can perform final cleanup and reload (similar to Switch.cleanup)

## Usage

### Method 1: Command Line IP List

```bash
python manage.py prepare_switches --ips "10.69.144.131,10.69.144.132"
```

### Method 2: IP Address File

```bash
python manage.py prepare_switches --file switch_ips.txt
```

### Command Options

- `--ips`: Comma-separated list of IP addresses
- `--file`: Path to file containing IP addresses (one per line)
- `--username`: SSH username (default: admin)
- `--password`: SSH password (default: switch)
- `--skip-cleanup`: Skip the initial cleanup (rm commands)
- `--skip-init`: Skip creating init folder and copying files
- `--skip-config`: Skip creating vcboot.cfg configuration
- `--reload`: Apply configuration and reload switch (required for LLDP to work)

### Examples

```bash
# Basic preparation (creates config but doesn't apply it)
python manage.py prepare_switches --file switch_ips.txt

# Prepare and apply configuration (recommended for LLDP)
python manage.py prepare_switches --file switch_ips.txt --reload

# Skip cleanup phase
python manage.py prepare_switches --ips "10.69.144.131" --skip-cleanup

# Only create configuration (skip cleanup and init setup)
python manage.py prepare_switches --ips "10.69.144.131" --skip-cleanup --skip-init

# Custom credentials with reload
python manage.py prepare_switches --file switches.txt --username myuser --password mypass --reload
```

## What the Command Does

### Step 1: Cleanup Phase
Removes old files that might interfere with configuration:
```bash
rm swlog*
rm vcboot.cfg*
rm ovng*
```

### Step 2: Init Folder Setup
Creates a clean slate configuration:
```bash
mkdir init
cp working/*.img init/
cp -r working/pkg init/
```

Expected result:
```
init/
├── Yos.img
├── pkg/
└── vcboot.cfg (created in next step)
```

### Step 3: Configuration Creation
Creates `init/vcboot.cfg` with:
- System name (derived from switch model or IP)
- Session prompt and timeout
- Authentication settings
- LLDP configuration
- Static routes for lab network
- Auto-fabric and MVRP settings
- Command logging

Sample configuration:
```
system name "OS6900-V48C8"
session prompt default "OS6900-V48C8"
session cli timeout 5555

health threshold memory 80

aaa authentication console "local" 
aaa authentication ssh "local" 

lldp nearest-bridge chassis tlv management port-description enable system-name enable system-description enable 
lldp nearest-bridge chassis tlv management management-address enable 
lldp nearest-bridge chassis tlv dot3 mac-phy enable 

ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1
ip static-route 10.69.0.0/16 gateway 10.69.144.129 metric 1
ip static-route 135.118.225.0/24 gateway 10.69.144.129 metric 1

auto-fabric admin-state disable 
mvrp disable
command-log enable
```

### Step 4: Configuration Application (if --reload is used)
Applies the configuration and reboots the switch:
```bash
cp init/vcboot.cfg working/
reload from working no rollback-timeout
```

**Important**: The LLDP configuration requires a reboot to take effect. Without `--reload`, the configuration is prepared but not active.

## Integration with Switch Models

The command automatically:
- Tries to get the switch model from the database (if populated with `populate_switches`)
- Falls back to generating a name based on IP if model not found
- Uses the model name for system name and prompt configuration

## Workflow Integration

### Recommended workflow for new switches:

1. **First**: Run `populate_switches` to get hardware info
   ```bash
   python manage.py populate_switches --file switch_ips.txt
   ```

2. **Then**: Run `prepare_switches` to configure them **with reload for LLDP**
   ```bash
   python manage.py prepare_switches --file switch_ips.txt --reload
   ```

3. **Then**: Run `populate_ports` to discover connections (requires LLDP to be active)
   ```bash
   python manage.py populate_ports
   ```

4. **Finally**: Switches are ready for reservation and use

## Output Example

```
Preparing 2 switch(es)...

Preparing switch: 10.69.144.131
  Connecting to 10.69.144.131...
  Performing cleanup...
    Executing: rm swlog*
    Executing: rm vcboot.cfg*
    Executing: rm ovng*
  Setting up init folder...
    Executing: mkdir -p init
    Executing: cp working/*.img init/
    Executing: cp -r working/pkg init/
  Creating configuration...
    Creating vcboot.cfg with model: OS6900-V48C8
    Configuration file created successfully
  Applying configuration and reloading...
    Copying configuration to working directory...
    Initiating reload...
    ✓ Configuration applied and reload initiated
    ⚠ Switch will reboot - LLDP configuration will be active after restart
✓ Successfully prepared switch 10.69.144.131

==================================================
Summary:
  Successfully prepared: 2
  Failed: 0
==================================================
```

## Important Notes

- **LLDP Configuration**: The LLDP settings in vcboot.cfg are essential for port discovery
- **Reload Required**: Use `--reload` to apply the configuration and activate LLDP
- **Switch Reboot**: When using `--reload`, switches will restart to apply configuration
- **Port Discovery**: After reload, switches are ready for `populate_ports` command

## Error Handling

The command handles:
- SSH connection failures
- Missing files during copy operations (logs warnings but continues)
- SFTP errors during configuration file creation
- Network timeouts

## Prerequisites

- Switches must be accessible via SSH
- Default credentials: username=admin, password=switch
- Switches should have a `working` directory with necessary files
- Network connectivity to switch management interfaces

## After Preparation

Once prepared, each switch will have:
- Clean `init` folder with necessary files
- Basic configuration in `vcboot.cfg`
- Ready for the cleanup process used in reservations
- Consistent naming and network configuration
