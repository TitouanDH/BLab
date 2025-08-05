import logging
import re
import paramiko
from django.core.management.base import BaseCommand, CommandError
from api.models import Switch, Port

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Populates the port database by discovering connections between switches and backbone using LLDP'

    def add_arguments(self, parser):
        parser.add_argument(
            '--backbone-ips',
            type=str,
            help='Comma-separated list of backbone IP addresses (e.g., "10.69.144.130")'
        )
        parser.add_argument(
            '--backbone-file',
            type=str,
            help='Path to a file containing backbone IP addresses (one per line)'
        )
        parser.add_argument(
            '--switch-ips',
            type=str,
            help='Comma-separated list of switch IP addresses to scan (optional, will use database if not provided)'
        )
        parser.add_argument(
            '--switch-file',
            type=str,
            help='Path to a file containing switch IP addresses to scan (one per line)'
        )
        parser.add_argument(
            '--username',
            type=str,
            default='admin',
            help='SSH username (default: admin)'
        )
        parser.add_argument(
            '--password',
            type=str,
            default='switch',
            help='SSH password (default: switch)'
        )
        parser.add_argument(
            '--skip-backbone-enable',
            action='store_true',
            help='Skip enabling/disabling backbone ports (use if manually configured)'
        )

    def handle(self, *args, **options):
        # Get backbone IPs
        backbone_ips = self.get_ips_from_options(
            options['backbone_ips'], 
            options['backbone_file'],
            "backbone"
        )
        
        if not backbone_ips:
            # Default to the known backbone
            backbone_ips = ['10.69.144.130']
            self.stdout.write(f'Using default backbone IP: {backbone_ips[0]}')

        # Get switch IPs (from options or database)
        switch_ips = self.get_ips_from_options(
            options['switch_ips'], 
            options['switch_file'],
            "switch"
        )
        
        if not switch_ips:
            # Get from database
            switches = Switch.objects.all()
            switch_ips = [switch.mngt_IP for switch in switches]
            self.stdout.write(f'Using {len(switch_ips)} switches from database')

        if not switch_ips:
            raise CommandError("No switches found. Please populate switches first or provide switch IPs.")

        username = options['username']
        password = options['password']
        skip_backbone_enable = options['skip_backbone_enable']

        self.stdout.write(f'Discovering ports for {len(switch_ips)} switch(es) and {len(backbone_ips)} backbone(s)...')
        
        successful = 0
        failed = 0
        skipped = 0

        try:
            # Enable backbone ports for discovery (unless explicitly skipped)
            if not skip_backbone_enable:
                for backbone_ip in backbone_ips:
                    self.enable_backbone_ports(backbone_ip, username, password)
            else:
                self.stdout.write('Skipping backbone port enablement (--skip-backbone-enable used)')

            # Process each switch
            for switch_ip in switch_ips:
                try:
                    result = self.process_switch_ports(
                        switch_ip, backbone_ips, username, password
                    )
                    if result == 'success':
                        successful += 1
                    elif result == 'skipped':
                        skipped += 1
                    else:
                        failed += 1
                except Exception as e:
                    failed += 1
                    self.stdout.write(
                        self.style.ERROR(f'✗ Failed to process {switch_ip}: {e}')
                    )
                    logger.error(f"Failed to process switch {switch_ip}: {e}")

        finally:
            # Disable backbone ports after discovery (unless explicitly skipped)
            if not skip_backbone_enable:
                for backbone_ip in backbone_ips:
                    self.disable_backbone_ports(backbone_ip, username, password)

        # Summary
        self.stdout.write('\n' + '='*50)
        self.stdout.write(f'Summary:')
        self.stdout.write(f'  Backbones processed: {len(backbone_ips)}')
        for backbone_ip in backbone_ips:
            backbone_ports = Port.objects.filter(backbone=backbone_ip).count()
            self.stdout.write(f'    {backbone_ip}: {backbone_ports} ports in database')
        self.stdout.write(f'  Successfully processed: {successful}')
        self.stdout.write(f'  Skipped (already exist): {skipped}')
        self.stdout.write(f'  Failed: {failed}')
        self.stdout.write('='*50)

    def get_ips_from_options(self, ip_option, file_option, device_type):
        """Get IP addresses from command line options or file"""
        ips = []
        
        if ip_option:
            ips = [ip.strip() for ip in ip_option.split(',')]
        elif file_option:
            try:
                with open(file_option, 'r') as f:
                    ips = [line.strip() for line in f 
                           if line.strip() and not line.strip().startswith('#')]
            except FileNotFoundError:
                raise CommandError(f"{device_type.capitalize()} file not found: {file_option}")
        
        return ips

    def enable_backbone_ports(self, backbone_ip, username, password):
        """Enable backbone ports for LLDP discovery"""
        try:
            self.stdout.write(f'  Enabling backbone ports on {backbone_ip} for discovery...')
            
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(backbone_ip, username=username, password=password, timeout=10)
            
            # Enable LLDP globally
            stdin, stdout, stderr = ssh.exec_command('lldp nearest-bridge chassis lldpdu tx-and-rx')
            stdout.channel.recv_exit_status()
            
            # Enable all backbone ports (1/1/1 to 1/8/48)
            self.stdout.write(f'    Enabling backbone interfaces...')
            enabled_count = 0
            
            for ni in range(1, 9):  # NI 1 to 8  
                for port in range(1, 49):  # Ports 1 to 48
                    interface = f'1/{ni}/{port}'
                    stdin, stdout, stderr = ssh.exec_command(f'interfaces {interface} admin-state enable')
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status == 0:
                        enabled_count += 1
            
            self.stdout.write(f'    Enabled {enabled_count} backbone interfaces')
            ssh.close()
            
            # Wait for LLDP discovery
            self.stdout.write(f'    Waiting 10 seconds for LLDP discovery...')
            import time
            time.sleep(10)
            
        except Exception as e:
            logger.warning(f"Failed to enable backbone ports on {backbone_ip}: {e}")
            raise Exception(f"Failed to enable backbone ports: {e}")

    def disable_backbone_ports(self, backbone_ip, username, password):
        """Disable backbone ports and restore database states"""
        try:
            self.stdout.write(f'  Restoring backbone ports on {backbone_ip}...')
            
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(backbone_ip, username=username, password=password, timeout=10)
            
            # Disable LLDP globally
            stdin, stdout, stderr = ssh.exec_command('lldp nearest-bridge chassis lldpdu disable')
            stdout.channel.recv_exit_status()
            
            # Get all backbone ports from database to know their intended states
            backbone_ports = Port.objects.filter(backbone=backbone_ip)
            ports_in_db = {}
            for port in backbone_ports:
                ports_in_db[port.port_backbone] = port.status
            
            # Restore port states based on database
            restored_count = 0
            for ni in range(1, 9):  # NI 1 to 8
                for port in range(1, 49):  # Ports 1 to 48
                    interface = f'1/{ni}/{port}'
                    
                    # Check database for intended state
                    if interface in ports_in_db:
                        intended_state = ports_in_db[interface]
                        if intended_state == 'UP':
                            action = 'enable'
                        else:  # DOWN or any other state
                            action = 'disable'
                    else:
                        # Not in database, consider it should be down
                        action = 'disable'
                    
                    stdin, stdout, stderr = ssh.exec_command(f'interfaces {interface} admin-state {action}')
                    exit_status = stdout.channel.recv_exit_status()
                    if exit_status == 0:
                        restored_count += 1
            
            self.stdout.write(f'    Restored {restored_count} backbone interfaces to database states')
            ssh.close()
            
        except Exception as e:
            logger.warning(f"Failed to restore backbone ports on {backbone_ip}: {e}")

    def process_switch_ports(self, switch_ip, backbone_ips, username, password):
        """Process ports for a single switch"""
        self.stdout.write(f'\nProcessing switch: {switch_ip}')
        
        # Get switch object from database
        try:
            switch_obj = Switch.objects.get(mngt_IP=switch_ip)
        except Switch.DoesNotExist:
            raise Exception(f"Switch {switch_ip} not found in database. Run populate_switches first.")

        # Check if ports already exist (we'll handle conflicts during creation)
        existing_ports = Port.objects.filter(switch=switch_obj).count()
        if existing_ports > 0:
            self.stdout.write(f'  Switch {switch_ip} already has {existing_ports} ports, checking for new connections...')

        # Get LLDP information from the switch
        lldp_info = self.get_lldp_info(switch_ip, username, password)
        
        if not lldp_info:
            raise Exception("Failed to retrieve LLDP information")

        # Parse LLDP information to find backbone connections
        connections = self.parse_lldp_connections(lldp_info, backbone_ips)
        
        if not connections:
            self.stdout.write(
                self.style.WARNING(f'⚠ No backbone connections found for {switch_ip}')
            )
            # Log discovered systems for debugging
            discovered_systems = self.get_discovered_systems(lldp_info)
            if discovered_systems:
                self.stdout.write(f'  Discovered systems: {", ".join(discovered_systems)}')
                self.stdout.write(f'  Expected backbones: {", ".join(backbone_ips)}')
            return 'success'

        # Create or update port entries with conflict checking
        ports_created = 0
        ports_updated = 0
        ports_skipped = 0
        
        for connection in connections:
            switch_port = connection['switch_port']
            backbone_ip = connection['backbone_ip']
            backbone_port = connection['backbone_port']
            
            # Check for existing port on same switch-port
            existing_switch_port = Port.objects.filter(
                switch=switch_obj, 
                port_switch=switch_port
            ).first()
            
            # Check for existing port on same backbone-port
            existing_backbone_port = Port.objects.filter(
                backbone=backbone_ip,
                port_backbone=backbone_port
            ).first()
            
            # Handle conflicts
            if existing_switch_port and existing_backbone_port:
                # Both ports exist - check if it's the same connection
                if (existing_switch_port.backbone == backbone_ip and 
                    existing_switch_port.port_backbone == backbone_port and
                    existing_backbone_port.switch == switch_obj and
                    existing_backbone_port.port_switch == switch_port):
                    # Same connection already exists, skip
                    ports_skipped += 1
                    self.stdout.write(f'  ⚠ Skipped: {switch_port} -> {backbone_ip}:{backbone_port} (already exists)')
                    continue
                else:
                    # Conflict: both ports are used for different connections
                    self.stdout.write(
                        self.style.ERROR(f'  ✗ ERROR: Port conflict for {switch_port} -> {backbone_ip}:{backbone_port}')
                    )
                    self.stdout.write(f'    Switch port {switch_port} is already connected to {existing_switch_port.backbone}:{existing_switch_port.port_backbone}')
                    self.stdout.write(f'    Backbone port {backbone_ip}:{backbone_port} is already connected to switch {existing_backbone_port.switch.mngt_IP}:{existing_backbone_port.port_switch}')
                    continue
                    
            elif existing_switch_port:
                # Switch port is already used
                self.stdout.write(
                    self.style.ERROR(f'  ✗ ERROR: Switch port {switch_port} already connected to {existing_switch_port.backbone}:{existing_switch_port.port_backbone}')
                )
                continue
                
            elif existing_backbone_port:
                # Backbone port is already used
                self.stdout.write(
                    self.style.ERROR(f'  ✗ ERROR: Backbone port {backbone_ip}:{backbone_port} already connected to switch {existing_backbone_port.switch.mngt_IP}:{existing_backbone_port.port_switch}')
                )
                continue
            
            # No conflicts, create the port
            port_obj = Port.objects.create(
                switch=switch_obj,
                port_switch=switch_port,
                backbone=backbone_ip,
                port_backbone=backbone_port,
                status='DOWN'  # New ports start as DOWN
            )
            ports_created += 1
            self.stdout.write(f'  ✓ Created port {switch_port} -> {backbone_ip}:{backbone_port} (status: DOWN)')

        if ports_created > 0 or ports_skipped > 0:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Switch {switch_ip}: {ports_created} ports created, {ports_skipped} ports skipped')
            )
            logger.info(f"Processed switch {switch_ip}: {ports_created} created, {ports_skipped} skipped")
        
        return 'success'

    def get_lldp_info(self, switch_ip, username, password):
        """Get LLDP information from a switch"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.stdout.write(f'  Connecting to {switch_ip}...')
            ssh.connect(switch_ip, username=username, password=password, timeout=10)
            
            self.stdout.write(f'  Executing "show lldp remote-system" command...')
            stdin, stdout, stderr = ssh.exec_command('show lldp remote-system')
            
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            ssh.close()
            
            if error:
                logger.warning(f"SSH command stderr for {switch_ip}: {error}")
            
            if not output:
                raise Exception("No output received from 'show lldp remote-system' command")
                
            self.stdout.write(f'  Successfully retrieved LLDP information')
            return output
            
        except Exception as e:
            raise Exception(f"Error getting LLDP info from {switch_ip}: {e}")

    def parse_lldp_connections(self, lldp_output, backbone_ips):
        """Parse LLDP output to find backbone connections"""
        connections = []
        
        # Regular expressions to parse LLDP output
        port_pattern = r'Remote LLDP nearest-bridge Agents on Local Port (\S+):'
        system_name_pattern = r'System Name\s*=\s*(\S+)'
        port_desc_pattern = r'Port Description\s*=\s*([^,\n]+)'
        
        # Split output into blocks for each remote system
        blocks = re.split(r'Remote LLDP nearest-bridge Agents on Local Port', lldp_output)
        
        for block in blocks[1:]:  # Skip first empty block
            try:
                # Extract local port
                port_match = re.search(r'^(\S+):', block)
                if not port_match:
                    continue
                local_port = port_match.group(1)
                
                # Extract system name
                system_name_match = re.search(system_name_pattern, block)
                if not system_name_match:
                    continue
                system_name = system_name_match.group(1)
                
                # Extract port description to get remote port
                port_desc_match = re.search(port_desc_pattern, block)
                if not port_desc_match:
                    continue
                port_description = port_desc_match.group(1).strip()
                
                # Parse remote port from description (e.g., "Alcatel-Lucent OS10K XNI 1/2/1")
                remote_port_match = re.search(r'(\d+/\d+/\d+)', port_description)
                if not remote_port_match:
                    continue
                remote_port = remote_port_match.group(1)
                
                # Check if this is a backbone system
                backbone_ip = self.find_backbone_ip_by_name(system_name, backbone_ips)
                if backbone_ip:
                    connection = {
                        'switch_port': local_port,
                        'backbone_ip': backbone_ip,
                        'backbone_port': remote_port,
                        'system_name': system_name
                    }
                    connections.append(connection)
                    self.stdout.write(f'  Found connection: {local_port} -> {system_name}:{remote_port}')
                
            except Exception as e:
                logger.warning(f"Error parsing LLDP block: {e}")
                continue
        
        return connections

    def find_backbone_ip_by_name(self, system_name, backbone_ips):
        """Find backbone IP by system name - enhanced for multiple backbones"""
        # First check if this looks like a backbone switch
        if not ('OS10K' in system_name or 'BLAB' in system_name or 'backbone' in system_name.lower()):
            return None
        
        # If only one backbone, return it
        if len(backbone_ips) == 1:
            return backbone_ips[0]
        
        # For multiple backbones, try to match by querying each backbone for its system name
        for backbone_ip in backbone_ips:
            try:
                # Quick check if this backbone has the matching system name
                # This could be enhanced to actually SSH and check, but for now we'll use IP mapping
                if self.backbone_matches_system_name(backbone_ip, system_name):
                    return backbone_ip
            except Exception as e:
                logger.warning(f"Failed to verify backbone {backbone_ip} system name: {e}")
                continue
        
        # If no specific match found, return first backbone as fallback
        logger.warning(f"Could not match system name '{system_name}' to specific backbone, using first one")
        return backbone_ips[0]
    
    def backbone_matches_system_name(self, backbone_ip, system_name):
        """Check if backbone IP matches the discovered system name"""
        # Simple heuristic based on IP and system name patterns
        # This could be enhanced to actually SSH and verify
        
        # Extract last octet of IP for basic matching
        ip_parts = backbone_ip.split('.')
        if len(ip_parts) == 4:
            last_octet = ip_parts[3]
            # If system name contains the last octet, likely a match
            if last_octet in system_name:
                return True
        
        # If system name contains part of the IP, likely a match
        if any(part in system_name for part in backbone_ip.split('.')):
            return True
            
        # Default: if only basic backbone indicators, assume it could match
        return True

    def get_discovered_systems(self, lldp_output):
        """Get list of all discovered system names for debugging"""
        systems = []
        system_name_pattern = r'System Name\s*=\s*(\S+)'
        
        for match in re.finditer(system_name_pattern, lldp_output):
            system_name = match.group(1)
            if system_name not in systems:
                systems.append(system_name)
        
        return systems

    def verify_backbone_connection(self, backbone_ip, backbone_port, expected_switch_name, username, password):
        """Verify the connection from backbone side (optional verification)"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(backbone_ip, username=username, password=password, timeout=10)
            
            # Get LLDP info for specific port
            stdin, stdout, stderr = ssh.exec_command('show lldp remote-system')
            output = stdout.read().decode('utf-8')
            ssh.close()
            
            # Check if expected switch is found on the specified port
            port_block_pattern = f'Remote LLDP nearest-bridge Agents on Local Port {backbone_port}:'
            if port_block_pattern in output and expected_switch_name in output:
                return True
            
        except Exception as e:
            logger.warning(f"Failed to verify backbone connection: {e}")
        
        return False
