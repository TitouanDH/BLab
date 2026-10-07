from django.utils import timezone
import logging
import os
from django.db import models  # type: ignore
from django.contrib.auth.models import User  # type: ignore

from .lab_switch import LabSwitchError, lab_switch

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
        Shows on the switch who holds it.

        Returns:
            bool: True if the banner is successfully changed, False otherwise.
        """
        if self.mngt_IP == "Not available":
            logger.info(f"Skipping banner update for switch with management IP: {self.mngt_IP}")
            return True

        user_names = [reservation.user.username for reservation in Reservation.objects.filter(switch=self)]
        logger.info("Updating banner for switch %s", self.mngt_IP)
        try:
            lab_switch(self.mngt_IP).set_banner(user_names)
        except LabSwitchError as e:
            logger.error("Banner update failed on %s: %s", self.mngt_IP, e)
            return False
        logger.info("Banner updated successfully for switch %s", self.mngt_IP)
        return True

    def cleanup(self) -> bool:
        """
        Cleans up the switch: restores its init config and reboots it (see CONTEXT.md).
        Only performs cleanup if the switch is not currently reserved.

        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        logger.info("Attempting to clean up switch %s", self.mngt_IP)
        if Reservation.objects.filter(switch=self).exists():
            logger.info("Switch %s is reserved. Skipping cleanup.", self.mngt_IP)
            return False

        try:
            lab_switch(self.mngt_IP).restore_init_and_reload()
        except LabSwitchError as e:
            logger.error("Error during cleanup for switch %s: %s", self.mngt_IP, e)
            return False
        logger.info("Successfully initiated cleanup reload for switch %s", self.mngt_IP)
        return True

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
