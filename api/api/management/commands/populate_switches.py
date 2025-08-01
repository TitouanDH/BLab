import logging
import re
import paramiko
from django.core.management.base import BaseCommand, CommandError
from api.models import Switch

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Populates the switch database by connecting to switches via SSH and extracting hardware info'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ips',
            type=str,
            help='Comma-separated list of IP addresses (e.g., "192.168.1.1,192.168.1.2")'
        )
        parser.add_argument(
            '--file',
            type=str,
            help='Path to a file containing IP addresses (one per line)'
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing switches instead of skipping them'
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

    def handle(self, *args, **options):
        ips = []
        
        # Get IP addresses from command line or file
        if options['ips']:
            ips = [ip.strip() for ip in options['ips'].split(',')]
        elif options['file']:
            try:
                with open(options['file'], 'r') as f:
                    ips = [line.strip() for line in f if line.strip()]
            except FileNotFoundError:
                raise CommandError(f"File not found: {options['file']}")
        else:
            raise CommandError("Please provide either --ips or --file parameter")

        if not ips:
            raise CommandError("No IP addresses provided")

        username = options['username']
        password = options['password']
        update_existing = options['update']

        self.stdout.write(f'Processing {len(ips)} switch(es)...')
        
        successful = 0
        failed = 0
        skipped = 0

        for ip in ips:
            try:
                result = self.process_switch(ip, username, password, update_existing)
                if result == 'success':
                    successful += 1
                elif result == 'skipped':
                    skipped += 1
            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'✗ Failed to process {ip}: {e}')
                )
                logger.error(f"Failed to process switch {ip}: {e}")

        # Summary
        self.stdout.write('\n' + '='*50)
        self.stdout.write(f'Summary:')
        self.stdout.write(f'  Successfully processed: {successful}')
        self.stdout.write(f'  Skipped (already exist): {skipped}')
        self.stdout.write(f'  Failed: {failed}')
        self.stdout.write('='*50)

    def process_switch(self, ip, username, password, update_existing):
        """Process a single switch"""
        self.stdout.write(f'\nProcessing switch: {ip}')
        
        # Check if switch already exists
        existing_switch = Switch.objects.filter(mngt_IP=ip).first()
        if existing_switch and not update_existing:
            self.stdout.write(
                self.style.WARNING(f'⚠ Switch {ip} already exists. Use --update to update existing switches.')
            )
            return 'skipped'

        # Connect via SSH and get chassis info
        chassis_info = self.get_chassis_info(ip, username, password)
        
        if not chassis_info:
            raise Exception("Failed to retrieve chassis information")

        # Parse the chassis info
        parsed_info = self.parse_chassis_info(chassis_info)
        
        if not parsed_info:
            raise Exception("Failed to parse chassis information")

        # Create or update the switch
        if existing_switch:
            # Update existing switch
            existing_switch.model = parsed_info['model']
            existing_switch.console = parsed_info['console']
            existing_switch.part_number = parsed_info['part_number']
            existing_switch.hardware_revision = parsed_info['hardware_revision']
            existing_switch.serial_number = parsed_info['serial_number']
            existing_switch.save()
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Updated switch {ip} ({parsed_info["model"]})')
            )
            logger.info(f"Updated switch {ip} with model {parsed_info['model']}")
        else:
            # Create new switch
            switch = Switch.objects.create(
                mngt_IP=ip,
                model=parsed_info['model'],
                console=parsed_info['console'],
                part_number=parsed_info['part_number'],
                hardware_revision=parsed_info['hardware_revision'],
                serial_number=parsed_info['serial_number']
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Created switch {ip} ({parsed_info["model"]})')
            )
            logger.info(f"Created switch {ip} with model {parsed_info['model']}")

        return 'success'

    def get_chassis_info(self, ip, username, password):
        """Connect to switch via SSH and execute 'show chassis' command"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            self.stdout.write(f'  Connecting to {ip}...')
            ssh.connect(ip, username=username, password=password, timeout=10)
            
            self.stdout.write(f'  Executing "show chassis" command...')
            stdin, stdout, stderr = ssh.exec_command('show chassis')
            
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            ssh.close()
            
            if error:
                logger.warning(f"SSH command stderr for {ip}: {error}")
            
            if not output:
                raise Exception("No output received from 'show chassis' command")
                
            self.stdout.write(f'  Successfully retrieved chassis information')
            return output
            
        except paramiko.AuthenticationException:
            raise Exception(f"Authentication failed for {ip}")
        except paramiko.SSHException as e:
            raise Exception(f"SSH connection failed: {e}")
        except Exception as e:
            raise Exception(f"Error connecting to {ip}: {e}")

    def parse_chassis_info(self, chassis_output):
        """Parse the 'show chassis' output to extract hardware information"""
        try:
            # Initialize result dictionary
            result = {
                'model': '',
                'console': 'TODO',  # Placeholder as requested
                'part_number': '',
                'hardware_revision': '',
                'serial_number': ''
            }
            
            # Define regex patterns for each field
            patterns = {
                'model': r'Model Name:\s*([^,\n]+)',
                'part_number': r'Part Number:\s*([^,\n]+)',
                'hardware_revision': r'Hardware Revision:\s*([^,\n]+)',
                'serial_number': r'Serial Number:\s*([^,\n]+)'
            }
            
            # Extract each field using regex
            for field, pattern in patterns.items():
                match = re.search(pattern, chassis_output, re.IGNORECASE)
                if match:
                    result[field] = match.group(1).strip().rstrip(',')
                else:
                    self.stdout.write(
                        self.style.WARNING(f'  Warning: Could not extract {field}')
                    )
            
            # Validate that we got at least the model
            if not result['model']:
                raise Exception("Could not extract model name from chassis output")
            
            self.stdout.write(f'  Parsed: {result["model"]} (S/N: {result["serial_number"]})')
            return result
            
        except Exception as e:
            logger.error(f"Error parsing chassis info: {e}")
            logger.error(f"Chassis output was: {chassis_output}")
            raise Exception(f"Failed to parse chassis information: {e}")
