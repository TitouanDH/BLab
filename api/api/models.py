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

NO_MANAGEMENT_IP = 'Not available'  # mngt_IP of a Switch BLab can't reach


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
        if self.mngt_IP == NO_MANAGEMENT_IP:
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

    def ports_cabled_on_purpose(self) -> set:
        """Ports whose link may be up without being an Unwanted cable: those paired with a UNI, and PermanentCables."""
        return (set(self.port_set.values_list('port_switch', flat=True))
                | set(self.permanent_cables.values_list('port', flat=True)))

    def last_inspection(self):
        """The latest Inspection recorded for this Switch, or None."""
        return self.events.filter(kind=SwitchEvent.INSPECTION).first()


class SwitchEvent(models.Model):
    """
    One entry in a Switch's history, newest first. Only Inspections so far; Releases,
    Cleanups and Quarantines join it later. `ok` is the outcome (for an Inspection: clean),
    `reasons` why it isn't ok, `warnings` what is worth knowing but doesn't make it fail.
    """
    INSPECTION = 'inspection'
    KINDS = [(INSPECTION, 'Inspection')]

    # No database constraint: main's code deletes Switches without knowing this table (ADR 0002)
    switch = models.ForeignKey(Switch, related_name='events', on_delete=models.CASCADE, db_constraint=False)
    kind = models.CharField(max_length=32, choices=KINDS)
    at = models.DateTimeField(default=timezone.now)
    ok = models.BooleanField(null=True)
    reasons = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-at', '-id']
        indexes = [models.Index(fields=['switch', '-at'])]

    def __str__(self):
        outcome = {True: 'ok', False: 'failed', None: ''}[self.ok]
        return f"{self.switch} {self.kind} {outcome} at {self.at:%Y-%m-%d %H:%M}"


class PermanentCable(models.Model):
    """
    A port of a Switch that an admin has marked as permanently cabled: its link being up is
    not an Unwanted cable. Ports paired with a UNI never need one (they are Port records).
    """
    # No database constraint: main's code deletes Switches without knowing this table (ADR 0002)
    switch = models.ForeignKey(Switch, related_name='permanent_cables', on_delete=models.CASCADE,
                               db_constraint=False)
    port = models.CharField(max_length=32, help_text='Switch port, as in show interfaces (e.g. 1/1/5)')
    note = models.CharField(max_length=255, blank=True, help_text='What it is cabled to, and why it stays')

    def __str__(self):
        return f"{self.switch} {self.port}"

class Reservation(models.Model):
    """
    Represents a reservation for a switch. A Switch has at most one at a time.
    Deleting one only deletes the row: ending a Reservation is api.release.release().

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
    # A disconnect asked for and not done yet: the Link worker tears the Link down (api.link_worker).
    # It holds for the SVLAN it was asked on only: production's older code may unlink and relink
    # the Port without knowing these fields, and a new Link must not be torn down for an old request.
    teardown_requested_at = models.DateTimeField(null=True, blank=True)
    teardown_svlan = models.IntegerField(null=True, blank=True)
    teardown_error = models.TextField(null=True, blank=True)  # why the attempts so far failed

    def __str__(self):
        return f"{self.switch}_{self.port_backbone}"

    @property
    def teardown_pending(self) -> bool:
        return self.teardown_requested_at is not None and self.svlan is not None and self.teardown_svlan == self.svlan

class TopologyShare(models.Model):
    """
    Represents a topology sharing between two users.
    """
    owner = models.ForeignKey(User, related_name='shared_topologies', on_delete=models.CASCADE)
    target = models.ForeignKey(User, related_name='received_topologies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Topology of {self.owner.username} shared with {self.target.username}"
