from django.utils import timezone
import time
import logging
import os
from django.db import models  # type: ignore
from . import fake_devices
from django.contrib.auth.models import User  # type: ignore
import paramiko

from .backbone import SWITCH_USERNAME, SWITCH_PASSWORD

# Configure logging to save logs to a file
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR, 'api_models.log'), level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Switch(models.Model):
    """
    Represents a network switch.

    Attributes:
        mngt_IP (str): Management IP address of the switch.
        model (str): Model of the switch.
        console (str): Type of console used for the switch.
        part_number (str): Part number of the switch.
        hardware_revision (str): Hardware revision of the switch.
        serial_number (str): Serial number of the switch.
    """
    mngt_IP = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    console = models.CharField(max_length=255)
    part_number = models.CharField(max_length=255)
    hardware_revision = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.model}_{self.mngt_IP}"

    def delete(self):
        """
        Deletes the switch and its associated ports.
        """
        logger.info(f"Deleting switch {self.mngt_IP} and its associated ports.")
        ports = Port.objects.filter(switch=self.id)
        for port in ports:
            port.delete()
        return super().delete()

    def changeBanner(self) -> bool:
        """
        Changes the banner of the switch.

        Returns:
            bool: True if the banner is successfully changed, False otherwise.
        """
        if self.mngt_IP == "Not available":
            logger.info(f"Skipping banner update for switch with management IP: {self.mngt_IP}")
            return True

        reservations = Reservation.objects.filter(switch=self)
        user_names = ', '.join(reservation.user.username for reservation in reservations) if reservations.exists() else "nobody"

        text = f"""
***************** LAB RESERVATION SYSTEM ******************
This switch is reserved by : {user_names}
If you access this switch without reservation, please contact admin

To cleanup the switch:
cp init/vc* working
reload from working no rollback-timeout
"""
        logger.info("Updating banner for switch %s", self.mngt_IP)
        if fake_devices.devices_are_fake():
            logger.info("[fake switch %s] banner:%s", self.mngt_IP, text)
            return True
        try:
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.mngt_IP, username=SWITCH_USERNAME, password=SWITCH_PASSWORD, port=22, timeout=5)
                with ssh.open_sftp() as sftp:
                    # Open file in write mode; adjust path if necessary
                    with sftp.file('switch/pre_banner.txt', "w") as file:
                        file.write(text)
                logger.info("Banner updated successfully for switch %s", self.mngt_IP)
                return True
        except paramiko.SSHException as ssh_exception:
            logger.error("SSH Connection Error on %s: %s", self.mngt_IP, ssh_exception)
            return False
        except Exception as e:
            logger.error("Unexpected error in changeBanner for %s: %s", self.mngt_IP, e)
            return False

    def cleanup(self) -> bool:
        """
        Cleans up the switch configuration by restoring clean state from init directory.
        Only performs cleanup if the switch is not currently reserved.
        This provides a clean slate after users release their reservations.

        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        logger.info("Attempting to clean up switch %s", self.mngt_IP)
        if Reservation.objects.filter(switch=self).exists():
            logger.info("Switch %s is reserved. Skipping cleanup.", self.mngt_IP)
            return False

        if fake_devices.devices_are_fake():
            logger.info("[fake switch %s] restore init/ into working/ and reload", self.mngt_IP)
            return True

        try:
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.mngt_IP, username=SWITCH_USERNAME, password=SWITCH_PASSWORD, port=22, timeout=5)

                # Clean working directory completely
                logger.info("Cleaning working directory on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("rm -rf working/*")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning("Working directory cleanup on %s returned status %s: %s", self.mngt_IP, exit_status, error_output)

                # Copy all clean files from init to working
                logger.info("Restoring clean configuration from init directory on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("cp -r init/* working/")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.error("Copy from init to working failed on %s with exit status %s: %s", self.mngt_IP, exit_status, error_output)
                    return False

                # Verify essential files are present before reload
                logger.info("Verifying essential files are present before reload on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("ls working/")
                if stdout.channel.recv_exit_status() == 0:
                    working_contents = stdout.read().decode('utf-8').strip()
                    logger.info("Working directory contents: %s", working_contents)
                    
                    # Check for essential files
                    if '.img' not in working_contents:
                        logger.error("No image files found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    if 'pkg' not in working_contents:
                        logger.error("No pkg directory found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    if 'vcboot.cfg' not in working_contents:
                        logger.error("No vcboot.cfg found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    logger.info("All essential files verified in working directory on switch %s", self.mngt_IP)
                else:
                    logger.error("Could not verify working directory contents on switch %s", self.mngt_IP)
                    return False

                # Also update certified directory as backup
                logger.info("Updating certified directory backup on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("rm -rf certified/*")
                stdin, stdout, stderr = ssh.exec_command("cp -r init/* certified/")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning("Copy to certified failed on %s: %s", self.mngt_IP, error_output)

                # Execute reload command with pseudo-tty for interactive confirmation
                logger.info("Initiating reload for clean state on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("reload from working no rollback-timeout", get_pty=True)
                time.sleep(1)  # Wait for the prompt
                stdin.write('y\n')
                stdin.flush()
                logger.info("Successfully initiated cleanup reload for switch %s", self.mngt_IP)
                return True
                
        except (paramiko.SSHException, Exception) as e:
            logger.error("Error during cleanup for switch %s: %s", self.mngt_IP, e)
            return False

class Reservation(models.Model):
    """
    Represents a reservation for a switch.

    Attributes:
        switch (Switch): Switch associated with the reservation.
        user (User): User who made the reservation.
        creation_date (datetime): Date and time when the reservation was created.
        end_date (datetime): Date and time when the reservation ends.
    """
    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.switch}_{self.user}"

    @classmethod
    def cleanup_expired_reservations(cls):
        """
        Cleans up expired reservations automatically.
        """
        from django.utils import timezone
        
        logger.info("Cleaning up expired reservations...")
        expired_reservations = cls.objects.filter(end_date__lt=timezone.now()).exclude(end_date__isnull=True)
        
        cleaned_count = 0
        for reservation in expired_reservations:
            logger.info(f"Found expired reservation: {reservation.user.username} on switch {reservation.switch.mngt_IP}")
            try:
                # Use cleanup=True for expired reservations to clean up automatically
                if reservation.delete(reservation.user.username, cleanup_switch=True):
                    cleaned_count += 1
                    logger.info(f"Successfully cleaned up expired reservation for {reservation.user.username}")
                else:
                    logger.error(f"Failed to cleanup expired reservation for {reservation.user.username}")
            except Exception as e:
                logger.error(f"Error cleaning up reservation for {reservation.user.username}: {e}")
        
        logger.info(f"Cleanup completed. Cleaned {cleaned_count} expired reservations")
        return cleaned_count

    def delete(self, username, cleanup_switch=False):
        """
        Deletes the reservation and releases associated ports.
        Optionally cleans up the switch if it's the last reservation.

        Args:
            username (str): Username of the user making the deletion.
            cleanup_switch (bool): Whether to cleanup the switch after releasing

        Returns:
            bool: True if the reservation was successfully deleted, False otherwise.
        """
        logger.info(f"Deleting reservation for user {username} on switch {self.switch.mngt_IP}.")
        from . import links  # links imports this module

        # First, remove every link with an end on this switch
        errors = links.disconnect_all(self.switch)
        for e in errors:
            logger.error(f"Failed to delete a link of switch {self.switch.mngt_IP}: {e}")
        failure_on_port_release = bool(errors)

        if not failure_on_port_release:
            # Delete the reservation
            super().delete()
            logger.info(f"Reservation for user {username} on switch {self.switch.mngt_IP} deleted successfully.")
            
            # Only cleanup if explicitly requested and it's the last reservation
            remaining_reservations = Reservation.objects.filter(switch=self.switch)
            if not remaining_reservations.exists() and cleanup_switch:
                cleanup_success = self.switch.cleanup()
                if not cleanup_success:
                    logger.warning(f"Failed to clean up switch {self.switch.mngt_IP} after releasing last reservation")
                else:
                    logger.info(f"Switch {self.switch.mngt_IP} cleaned up successfully")
            elif not remaining_reservations.exists():
                logger.info(f"Switch {self.switch.mngt_IP} is free but cleanup was not requested")
            else:
                logger.info(f"Skipping cleanup for switch {self.switch.mngt_IP} as there are remaining reservations")

            return True
        else:
            logger.error(f"Failed to release all ports for switch {self.switch.mngt_IP}")
            return False


class Port(models.Model):
    """
    Represents a port on a switch.

    Attributes:
        switch (Switch): Switch to which the port belongs.
        port_switch (str): Port identifier on the switch.
        backbone (str): Backbone information.
        port_backbone (str): Port identifier on the backbone.
        svlan (int): Service VLAN associated with the port.
        status (str): Status of the port, either 'UP' or 'DOWN'.
    """
    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    port_switch = models.CharField(max_length=255)
    backbone = models.CharField(max_length=255)
    port_backbone = models.CharField(max_length=255)
    svlan = models.IntegerField(default=None, blank=True, null=True)
    status = models.CharField(
        max_length=10, 
        default='DOWN', 
        null=True, 
        blank=True, 
        choices=[('UP', 'Up'), ('DOWN', 'Down')]
    )

    def __str__(self):
        return f"{self.switch}_{self.port_backbone}"

class TopologyShare(models.Model):
    """
    Represents a topology sharing between two users.
    """
    owner = models.ForeignKey(User, related_name='shared_topologies', on_delete=models.CASCADE)
    target = models.ForeignKey(User, related_name='received_topologies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Topology of {self.owner.username} shared with {self.target.username}"
