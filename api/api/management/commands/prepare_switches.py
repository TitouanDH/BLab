import logging
import time
import paramiko
from django.core.management.base import BaseCommand, CommandError
from api.models import Switch

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Prepares newly installed switches by setting up init folder and basic configuration'

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
            '--skip-cleanup',
            action='store_true',
            help='Skip the initial cleanup (rm commands)'
        )
        parser.add_argument(
            '--skip-init',
            action='store_true',
            help='Skip creating init folder and copying files'
        )
        parser.add_argument(
            '--skip-config',
            action='store_true',
            help='Skip creating vcboot.cfg configuration'
        )

    def handle(self, *args, **options):
        ips = []
        
        # Get IP addresses from command line or file
        if options['ips']:
            ips = [ip.strip() for ip in options['ips'].split(',')]
        elif options['file']:
            try:
                with open(options['file'], 'r') as f:
                    ips = [line.strip() for line in f 
                           if line.strip() and not line.strip().startswith('#')]
            except FileNotFoundError:
                raise CommandError(f"File not found: {options['file']}")
        else:
            raise CommandError("Please provide either --ips or --file parameter")

        if not ips:
            raise CommandError("No IP addresses provided")

        username = options['username']
        password = options['password']
        skip_cleanup = options['skip_cleanup']
        skip_init = options['skip_init']
        skip_config = options['skip_config']

        self.stdout.write(f'Preparing {len(ips)} switch(es)...')
        
        successful = 0
        failed = 0

        for ip in ips:
            try:
                self.prepare_switch(ip, username, password, skip_cleanup, skip_init, skip_config)
                successful += 1
            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f'✗ Failed to prepare {ip}: {e}')
                )
                logger.error(f"Failed to prepare switch {ip}: {e}")

        # Summary
        self.stdout.write('\n' + '='*50)
        self.stdout.write(f'Summary:')
        self.stdout.write(f'  Successfully prepared: {successful}')
        self.stdout.write(f'  Failed: {failed}')
        self.stdout.write('='*50)

    def prepare_switch(self, ip, username, password, skip_cleanup, skip_init, skip_config):
        """Prepare a single switch"""
        self.stdout.write(f'\nPreparing switch: {ip}')
        
        # Get switch model for configuration
        switch_model = self.get_switch_model(ip)
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            self.stdout.write(f'  Connecting to {ip}...')
            ssh.connect(ip, username=username, password=password, timeout=10)
            
            # Step 1: Cleanup old files
            if not skip_cleanup:
                self.stdout.write(f'  Performing cleanup...')
                self.cleanup_files(ssh, ip)
            
            # Step 2: Create init folder and copy files
            if not skip_init:
                self.stdout.write(f'  Setting up init folder...')
                self.setup_init_folder(ssh, ip)
            
            # Step 3: Create configuration file
            if not skip_config:
                self.stdout.write(f'  Creating configuration...')
                self.create_config(ssh, ip, switch_model)
            
            self.stdout.write(
                self.style.SUCCESS(f'✓ Successfully prepared switch {ip}')
            )
            logger.info(f"Successfully prepared switch {ip}")
            
        except Exception as e:
            raise Exception(f"Error preparing switch {ip}: {e}")
        finally:
            ssh.close()

    def get_switch_model(self, ip):
        """Get switch model from database or detect it"""
        try:
            # Try to get from database first
            switch = Switch.objects.filter(mngt_IP=ip).first()
            if switch and switch.model:
                return switch.model
        except:
            pass
        
        # If not in database, use a default format based on IP
        return f"OS6900-{ip.replace('.', '_')}"

    def cleanup_files(self, ssh, ip):
        """Remove old log and config files"""
        cleanup_commands = [
            'rm swlog*',
            'rm vcboot.cfg*',
            'rm ovng*'
        ]
        
        for command in cleanup_commands:
            try:
                self.stdout.write(f'    Executing: {command}')
                stdin, stdout, stderr = ssh.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                
                # Don't fail if files don't exist (rm returns non-zero for missing files)
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    if 'No such file or directory' not in error_output:
                        logger.warning(f"Cleanup command '{command}' on {ip} returned status {exit_status}: {error_output}")
                
            except Exception as e:
                logger.warning(f"Cleanup command '{command}' failed on {ip}: {e}")

    def setup_init_folder(self, ssh, ip):
        """Create init folder and copy necessary files"""
        init_commands = [
            'mkdir -p init',
            'cp working/*.img init/ 2>/dev/null || true',
            'cp -r working/pkg init/ 2>/dev/null || true'
        ]
        
        for command in init_commands:
            try:
                self.stdout.write(f'    Executing: {command}')
                stdin, stdout, stderr = ssh.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    # Log but don't fail for copy operations that might not find files
                    if 'mkdir' in command:
                        logger.error(f"Failed to create init directory on {ip}: {error_output}")
                        raise Exception(f"Failed to create init directory: {error_output}")
                    else:
                        logger.warning(f"Copy command '{command}' on {ip} returned status {exit_status}: {error_output}")
                
            except Exception as e:
                if 'mkdir' in command:
                    raise e
                else:
                    logger.warning(f"Copy command '{command}' failed on {ip}: {e}")

    def create_config(self, ssh, ip, switch_model):
        """Create vcboot.cfg configuration file"""
        
        # Generate configuration content
        config_content = f'''system name "{switch_model}"
session prompt default "{switch_model}"
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
'''
        
        try:
            # Use SFTP to create the configuration file
            with ssh.open_sftp() as sftp:
                self.stdout.write(f'    Creating vcboot.cfg with model: {switch_model}')
                with sftp.file('init/vcboot.cfg', 'w') as config_file:
                    config_file.write(config_content)
                
                self.stdout.write(f'    Configuration file created successfully')
                
        except Exception as e:
            logger.error(f"Failed to create configuration file on {ip}: {e}")
            raise Exception(f"Failed to create configuration file: {e}")

    def verify_setup(self, ssh, ip):
        """Verify that the setup was completed correctly"""
        try:
            self.stdout.write(f'  Verifying setup...')
            
            # List contents of init directory
            stdin, stdout, stderr = ssh.exec_command('ls init/')
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status == 0:
                output = stdout.read().decode('utf-8').strip()
                self.stdout.write(f'    Init directory contents: {output}')
                
                # Check for required files
                required_files = ['Yos.img', 'pkg', 'vcboot.cfg']
                missing_files = []
                
                for required_file in required_files:
                    if required_file not in output:
                        missing_files.append(required_file)
                
                if missing_files:
                    logger.warning(f"Missing files in init directory on {ip}: {missing_files}")
                    self.stdout.write(
                        self.style.WARNING(f'    Warning: Missing files: {", ".join(missing_files)}')
                    )
                else:
                    self.stdout.write(f'    ✓ All required files present')
            else:
                error_output = stderr.read().decode('utf-8')
                logger.warning(f"Could not verify init directory on {ip}: {error_output}")
                
        except Exception as e:
            logger.warning(f"Verification failed on {ip}: {e}")

    def perform_cleanup_like_model(self, ip, username, password):
        """Perform cleanup similar to the Switch.cleanup method"""
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ip, username=username, password=password, port=22, timeout=5)

            self.stdout.write(f'  Performing final cleanup and reload...')
            
            # Execute copy command
            stdin, stdout, stderr = ssh.exec_command("cp init/vc* working")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.error("Copy command failed on %s with exit status %s: %s", ip, exit_status, error_output)
                return False

            # Execute reload command with pseudo-tty for interactive confirmation
            stdin, stdout, stderr = ssh.exec_command("reload from working no rollback-timeout", get_pty=True)
            time.sleep(1)  # Wait for the prompt
            stdin.write('y\n')
            stdin.flush()
            
            self.stdout.write(f'  ✓ Cleanup and reload initiated successfully')
            logger.info("Successfully initiated reload for switch %s", ip)
            return True
            
        except Exception as e:
            logger.error("Error during final cleanup for switch %s: %s", ip, e)
            return False
        finally:
            ssh.close()
