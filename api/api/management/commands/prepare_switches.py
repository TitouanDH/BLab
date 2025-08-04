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
        parser.add_argument(
            '--reload',
            action='store_true',
            help='Apply configuration and reload switch (required for LLDP to work)'
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
        reload_switch = options['reload']

        self.stdout.write(f'Preparing {len(ips)} switch(es)...')
        
        successful = 0
        failed = 0

        for ip in ips:
            try:
                self.prepare_switch(ip, username, password, skip_cleanup, skip_init, skip_config, reload_switch)
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

    def prepare_switch(self, ip, username, password, skip_cleanup, skip_init, skip_config, reload_switch):
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
            
            # Step 4: Apply configuration and reload if requested
            if reload_switch:
                self.stdout.write(f'  Applying configuration and reloading...')
                self.apply_config_and_reload(ssh, ip)
            else:
                self.stdout.write(f'  ⚠ Configuration created but not applied. Use --reload to activate LLDP configuration.')
            
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
        try:
            # First, check what's available in working and certified directories
            self.stdout.write(f'    Checking available files...')
            
            # Check working directory
            stdin, stdout, stderr = ssh.exec_command('ls working/')
            working_output = stdout.read().decode('utf-8').strip() if stdout.channel.recv_exit_status() == 0 else ""
            has_img_in_working = '.img' in working_output
            has_pkg_in_working = 'pkg' in working_output
            
            # Check certified directory
            stdin, stdout, stderr = ssh.exec_command('ls certified/')
            certified_output = stdout.read().decode('utf-8').strip() if stdout.channel.recv_exit_status() == 0 else ""
            has_img_in_certified = '.img' in certified_output
            has_pkg_in_certified = 'pkg' in certified_output
            
            self.stdout.write(f'      Working dir: img={has_img_in_working}, pkg={has_pkg_in_working}')
            self.stdout.write(f'      Certified dir: img={has_img_in_certified}, pkg={has_pkg_in_certified}')
            
            # Check if we have the essential files somewhere
            if not (has_img_in_working or has_img_in_certified):
                raise Exception("No image files (.img) found in working or certified directories")
            if not (has_pkg_in_working or has_pkg_in_certified):
                raise Exception("No pkg directory found in working or certified directories")
            
            # Remove existing init folder and recreate it to ensure clean state
            self.stdout.write(f'    Cleaning and creating init directory...')
            stdin, stdout, stderr = ssh.exec_command('rm -rf init')
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.warning(f"Failed to remove existing init directory on {ip}: {error_output}")
            
            # Create fresh init directory
            stdin, stdout, stderr = ssh.exec_command('mkdir -p init')
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.error(f"Failed to create init directory on {ip}: {error_output}")
                raise Exception(f"Failed to create init directory: {error_output}")
            
            # Copy image files - prefer working, fallback to certified
            if has_img_in_working:
                self.stdout.write(f'    Copying image files from working directory...')
                stdin, stdout, stderr = ssh.exec_command('cp working/*.img init/')
                exit_status = stdout.channel.recv_exit_status()
                if exit_status == 0:
                    self.stdout.write(f'      ✓ Image files copied from working')
                else:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning(f"Failed to copy image files from working on {ip}: {error_output}")
                    # Fallback to certified
                    if has_img_in_certified:
                        self.stdout.write(f'    Fallback: copying image files from certified directory...')
                        stdin, stdout, stderr = ssh.exec_command('cp certified/*.img init/')
                        if stdout.channel.recv_exit_status() == 0:
                            self.stdout.write(f'      ✓ Image files copied from certified')
                        else:
                            raise Exception("Failed to copy image files from both working and certified")
            elif has_img_in_certified:
                self.stdout.write(f'    Copying image files from certified directory...')
                stdin, stdout, stderr = ssh.exec_command('cp certified/*.img init/')
                if stdout.channel.recv_exit_status() == 0:
                    self.stdout.write(f'      ✓ Image files copied from certified')
                else:
                    raise Exception("Failed to copy image files from certified")
            
            # Copy pkg directory - prefer working, fallback to certified
            if has_pkg_in_working:
                self.stdout.write(f'    Copying pkg directory from working...')
                stdin, stdout, stderr = ssh.exec_command('cp -r working/pkg init/')
                exit_status = stdout.channel.recv_exit_status()
                if exit_status == 0:
                    self.stdout.write(f'      ✓ Pkg directory copied from working')
                else:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning(f"Failed to copy pkg from working on {ip}: {error_output}")
                    # Fallback to certified
                    if has_pkg_in_certified:
                        self.stdout.write(f'    Fallback: copying pkg directory from certified...')
                        stdin, stdout, stderr = ssh.exec_command('cp -r certified/pkg init/')
                        if stdout.channel.recv_exit_status() == 0:
                            self.stdout.write(f'      ✓ Pkg directory copied from certified')
                        else:
                            raise Exception("Failed to copy pkg directory from both working and certified")
            elif has_pkg_in_certified:
                self.stdout.write(f'    Copying pkg directory from certified...')
                stdin, stdout, stderr = ssh.exec_command('cp -r certified/pkg init/')
                if stdout.channel.recv_exit_status() == 0:
                    self.stdout.write(f'      ✓ Pkg directory copied from certified')
                else:
                    raise Exception("Failed to copy pkg directory from certified")
            
            # Verify init directory contents
            self.stdout.write(f'    Verifying init directory...')
            stdin, stdout, stderr = ssh.exec_command('ls -la init/')
            exit_status = stdout.channel.recv_exit_status()
            
            if exit_status == 0:
                output = stdout.read().decode('utf-8').strip()
                self.stdout.write(f'      Init directory contents:\n{output}')
                
                # Final check for essential files
                if '.img' not in output:
                    raise Exception("No image files found in init directory after setup")
                
                if 'pkg' not in output:
                    raise Exception("No pkg directory found in init directory after setup")
                
                self.stdout.write(f'      ✓ All essential files verified in init directory')
                
            else:
                error_output = stderr.read().decode('utf-8')
                raise Exception(f"Could not verify init directory: {error_output}")
                
        except Exception as e:
            logger.error(f"Setup init folder failed on {ip}: {e}")
            raise Exception(f"Failed to setup init folder: {e}")

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

    def apply_config_and_reload(self, ssh, ip):
        """Apply configuration by cleaning working directory and copying essential files from init"""
        try:
            # First, backup to certified BEFORE any changes to working directory
            self.stdout.write(f'    Backing up to certified directory first...')
            stdin, stdout, stderr = ssh.exec_command("rm -rf certified/*")
            stdin, stdout, stderr = ssh.exec_command("cp -r init/* certified/")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.error("Backup to certified failed on %s with exit status %s: %s", ip, exit_status, error_output)
                raise Exception(f"Failed to backup init contents to certified: {error_output}")
            self.stdout.write(f'    ✓ Backup to certified completed')

            # Clean working directory
            self.stdout.write(f'    Cleaning working directory...')
            stdin, stdout, stderr = ssh.exec_command("rm -rf working/*")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.warning("Working directory cleanup on %s returned status %s: %s", ip, exit_status, error_output)

            # Copy all contents from init to working
            self.stdout.write(f'    Copying all files from init to working...')
            stdin, stdout, stderr = ssh.exec_command("cp -r init/* working/")
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                error_output = stderr.read().decode('utf-8')
                logger.error("Copy to working failed on %s with exit status %s: %s", ip, exit_status, error_output)
                raise Exception(f"Failed to copy init contents to working: {error_output}")

            # Verify working directory before reload
            self.stdout.write(f'    Verifying working directory before reload...')
            stdin, stdout, stderr = ssh.exec_command("ls working/")
            if stdout.channel.recv_exit_status() == 0:
                working_contents = stdout.read().decode('utf-8').strip()
                self.stdout.write(f'      Working directory: {working_contents}')
                
                # Check for main image file (Yos.img or any .img file)
                if '.img' not in working_contents:
                    raise Exception("No image files found in working directory before reload")
            
            # Execute reload command with pseudo-tty for interactive confirmation
            self.stdout.write(f'    Initiating reload...')
            stdin, stdout, stderr = ssh.exec_command("reload from working no rollback-timeout", get_pty=True)
            time.sleep(1)  # Wait for the prompt
            stdin.write('y\n')
            stdin.flush()
            
            self.stdout.write(f'    ✓ Working and certified directories populated')
            self.stdout.write(f'    ✓ Configuration applied and reload initiated')
            self.stdout.write(f'    ⚠ Switch will reboot - LLDP configuration will be active after restart')
            logger.info("Successfully applied configuration and initiated reload for switch %s", ip)
            
        except Exception as e:
            logger.error("Error during configuration application for switch %s: %s", ip, e)
            raise Exception(f"Failed to apply configuration and reload: {e}")
