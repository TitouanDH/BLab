import logging
import re
import paramiko
import time
from django.core.management.base import BaseCommand, CommandError
from api.models import Switch, Port

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Discover switch-to-backbone connections using LLDP and populate port database'

    def add_arguments(self, parser):
        parser.add_argument('--backbone-ips', type=str, default='10.69.144.130',
                          help='Comma-separated backbone IPs (default: 10.69.144.130)')
        parser.add_argument('--username', type=str, default='admin',
                          help='SSH username (default: admin)')
        parser.add_argument('--password', type=str, default='switch',
                          help='SSH password (default: switch)')

    def handle(self, *args, **options):
        backbone_ips = [ip.strip() for ip in options['backbone_ips'].split(',')]
        username = options['username']
        password = options['password']
        
        # Get switches from database
        switches = Switch.objects.all()
        if not switches:
            raise CommandError("No switches found. Run populate_switches first.")
        
        self.stdout.write(f'Processing {len(switches)} switches and {len(backbone_ips)} backbones...')
        
        try:
            # Step 1: Prepare backbones for discovery
            backbone_info = {}
            for backbone_ip in backbone_ips:
                system_name = self.get_system_name(backbone_ip, username, password)
                if system_name:
                    backbone_info[system_name] = backbone_ip
                    self.stdout.write(f'Backbone {backbone_ip} → {system_name}')
                    self.enable_backbone_discovery(backbone_ip, username, password)
                else:
                    self.stdout.write(f'WARNING: Could not get system name for {backbone_ip}')
            
            # Step 2: Discover connections from each switch
            connections_found = 0
            for switch in switches:
                switch_ip = switch.mngt_IP
                self.stdout.write(f'\nProcessing switch {switch_ip}...')
                
                lldp_data = self.get_lldp_data(switch_ip, username, password)
                if not lldp_data:
                    continue
                
                connections = self.parse_connections(lldp_data, backbone_info)
                for conn in connections:
                    self.create_port_entry(switch, conn)
                    connections_found += 1
                    self.stdout.write(f'  {conn["switch_port"]} ↔ {conn["backbone_ip"]}:{conn["backbone_port"]}')
            
            # Step 3: Restore backbone ports to database states
            for backbone_ip in backbone_ips:
                self.restore_backbone_ports(backbone_ip, username, password)
            
            self.stdout.write(f'\nCompleted: {connections_found} connections discovered')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
            # Always try to restore backbone ports
            for backbone_ip in backbone_ips:
                try:
                    self.restore_backbone_ports(backbone_ip, username, password)
                except:
                    pass

    def ssh_connect(self, ip, username, password):
        """Create SSH connection"""
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, password=password, timeout=10)
        return ssh

    def ssh_command(self, ip, username, password, command):
        """Execute SSH command and return output"""
        ssh = self.ssh_connect(ip, username, password)
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode('utf-8')
        ssh.close()
        return output

    def get_system_name(self, backbone_ip, username, password):
        """Get backbone system name"""
        try:
            output = self.ssh_command(backbone_ip, username, password, 'show system')
            match = re.search(r'Name:\s*([^,\n]+)', output)
            return match.group(1).strip() if match else None
        except Exception as e:
            logger.warning(f"Failed to get system name from {backbone_ip}: {e}")
            return None

    def enable_backbone_discovery(self, backbone_ip, username, password):
        """Enable LLDP and all ports on backbone for discovery"""
        try:
            ssh = self.ssh_connect(backbone_ip, username, password)
            
            # Enable LLDP
            ssh.exec_command('lldp nearest-bridge chassis lldpdu tx-and-rx')
            
            # Enable all ports (1/1/1 to 1/8/48)
            for ni in range(1, 9):
                for port in range(1, 49):
                    ssh.exec_command(f'interfaces 1/{ni}/{port} admin-state enable')
            
            ssh.close()
            time.sleep(10)  # Wait for LLDP discovery
            
        except Exception as e:
            logger.warning(f"Failed to enable discovery on {backbone_ip}: {e}")

    def get_lldp_data(self, switch_ip, username, password):
        """Get LLDP data from switch"""
        try:
            return self.ssh_command(switch_ip, username, password, 'show lldp remote-system')
        except Exception as e:
            logger.warning(f"Failed to get LLDP from {switch_ip}: {e}")
            return None

    def parse_connections(self, lldp_data, backbone_info):
        """Parse LLDP data to find backbone connections"""
        connections = []
        
        # Split by port sections
        sections = lldp_data.split('Remote LLDP nearest-bridge Agents on Local Port')[1:]
        
        for section in sections:
            lines = section.strip().split('\n')
            if not lines:
                continue
            
            # Get local port from first line
            first_line = lines[0].strip()
            if ':' not in first_line:
                continue
            local_port = first_line.split(':')[0].strip()
            
            # Find system name and port description
            system_name = None
            port_description = None
            
            for line in lines:
                if 'System Name' in line and '=' in line:
                    system_name = line.split('=')[1].strip().rstrip(',')
                if 'Port Description' in line and '=' in line:
                    port_description = line.split('=')[1].strip()
            
            if system_name and port_description and system_name in backbone_info:
                # Extract remote port (format: "Alcatel-Lucent OS10K XNI 1/2/1")
                port_match = re.search(r'(\d+/\d+/\d+)', port_description)
                if port_match:
                    remote_port = port_match.group(1)
                    connections.append({
                        'switch_port': local_port,
                        'backbone_ip': backbone_info[system_name],
                        'backbone_port': remote_port,
                        'system_name': system_name
                    })
        
        return connections

    def create_port_entry(self, switch, connection):
        """Create or update port entry in database"""
        try:
            port, created = Port.objects.get_or_create(
                switch=switch,
                port_switch=connection['switch_port'],
                defaults={
                    'backbone': connection['backbone_ip'],
                    'port_backbone': connection['backbone_port'],
                    'status': 'DOWN'
                }
            )
            if not created and (port.backbone != connection['backbone_ip'] or 
                              port.port_backbone != connection['backbone_port']):
                # Update if connection changed
                port.backbone = connection['backbone_ip']
                port.port_backbone = connection['backbone_port']
                port.save()
        except Exception as e:
            logger.warning(f"Failed to create port entry: {e}")

    def restore_backbone_ports(self, backbone_ip, username, password):
        """Restore backbone ports to database states"""
        try:
            ssh = self.ssh_connect(backbone_ip, username, password)
            
            # Disable LLDP
            ssh.exec_command('lldp nearest-bridge chassis lldpdu disable')
            
            # Get database states
            db_ports = {}
            for port in Port.objects.filter(backbone=backbone_ip):
                db_ports[port.port_backbone] = port.status
            
            # Restore all ports
            for ni in range(1, 9):
                for port in range(1, 49):
                    interface = f'1/{ni}/{port}'
                    state = 'enable' if db_ports.get(interface) == 'UP' else 'disable'
                    ssh.exec_command(f'interfaces {interface} admin-state {state}')
            
            ssh.close()
            
        except Exception as e:
            logger.warning(f"Failed to restore ports on {backbone_ip}: {e}")
