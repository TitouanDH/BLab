# Switch Population Management Command

This document explains how to use the `populate_switches` Django management command to automatically populate your switch database.

## Overview

The `populate_switches` command connects to network switches via SSH, executes the `show chassis` command, and extracts hardware information to populate the database with the following fields:

- **Management IP**: The IP address used to connect to the switch
- **Model**: Extracted from the "Model Name" field in `show chassis` output
- **Console**: Set to "TODO" as a placeholder (as requested)
- **Part Number**: Extracted from the "Part Number" field
- **Hardware Revision**: Extracted from the "Hardware Revision" field  
- **Serial Number**: Extracted from the "Serial Number" field

## Usage

### Method 1: Command Line IP List

```bash
python manage.py populate_switches --ips "192.168.1.10,192.168.1.11,192.168.1.12"
```

### Method 2: IP Address File

1. Create a file with IP addresses (one per line):
```
192.168.1.10
192.168.1.11
192.168.1.12
```

2. Run the command:
```bash
python manage.py populate_switches --file switch_ips.txt
```

### Command Options

- `--ips`: Comma-separated list of IP addresses
- `--file`: Path to file containing IP addresses (one per line)
- `--update`: Update existing switches instead of skipping them
- `--username`: SSH username (default: admin)
- `--password`: SSH password (default: switch)

### Examples

```bash
# Basic usage with IP list
python manage.py populate_switches --ips "192.168.1.10,192.168.1.11"

# Using a file with custom credentials
python manage.py populate_switches --file switches.txt --username myuser --password mypass

# Update existing switches
python manage.py populate_switches --file switches.txt --update

# Get help
python manage.py populate_switches --help
```

## Expected Switch Output

The command expects the `show chassis` output to be in this format:

```
OS6900-V72 show chassis
Local Chassis ID 1 (Master)
  Model Name:                    OS6900-V72,
  Module Type:                   0xa062302,
  Description:                   72X25GBASE-X,
  Part Number:                   903985-90,
  Hardware Revision:             R02,
  Serial Number:                 AI46021761,
  Manufacture Date:              Nov 22 2018,
  Admin Status:                  POWER ON,
  Operational Status:            UP,
  Number Of Resets:              516,
  MAC Address:                   dc:08:56:10:4d:b9
```

## Output

The command provides detailed output showing:
- Connection status for each switch
- Success/failure of chassis information extraction
- Summary of processed, skipped, and failed switches

Example output:
```
Processing 3 switch(es)...

Processing switch: 192.168.1.10
  Connecting to 192.168.1.10...
  Executing "show chassis" command...
  Successfully retrieved chassis information
  Parsed: OS6900-V72 (S/N: AI46021761)
✓ Created switch 192.168.1.10 (OS6900-V72)

==================================================
Summary:
  Successfully processed: 2
  Skipped (already exist): 1
  Failed: 0
==================================================
```

## Error Handling

The command handles various error scenarios:
- SSH connection failures
- Authentication failures
- Missing or malformed chassis output
- Network timeouts

All errors are logged and displayed with clear error messages.

## Prerequisites

- Network switches must be accessible via SSH
- Default credentials: username=admin, password=switch
- Switches must support the `show chassis` command with the expected output format
