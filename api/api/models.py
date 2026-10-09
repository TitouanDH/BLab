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
    # Out of service (see CONTEXT.md): set and lifted by an admin only, with the reason
    out_of_service_reason = models.CharField(max_length=255, null=True, blank=True,
                                             help_text='Set to take the Switch out of reservation; empty it to put it back')
    out_of_service_since = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.model}_{self.mngt_IP}"

    @property
    def out_of_service(self) -> bool:
        return bool(self.out_of_service_reason)

    @staticmethod
    def in_service():
        """The Switches not Out of service, as a filter: out_of_service, for a query."""
        return models.Q(out_of_service_reason__isnull=True) | models.Q(out_of_service_reason='')

    def open_quarantine(self):
        """The Quarantine this Switch is in, or None."""
        return self.quarantines.filter(lifted_at__isnull=True).first()

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

    def ports_cabled_on_purpose(self) -> set:
        """Ports whose link may be up without being an Unwanted cable: those paired with a UNI, and PermanentCables."""
        return (set(self.port_set.values_list('port_switch', flat=True))
                | set(self.permanent_cables.values_list('port', flat=True)))

    def last_inspection(self):
        """The latest Inspection recorded for this Switch, or None."""
        return self.events.filter(kind=SwitchEvent.INSPECTION).first()


class SwitchEvent(models.Model):
    """
    One entry in a Switch's history, newest first. `ok` is the outcome (for an Inspection:
    clean; for a Cleanup: the reload started), `reasons` why it isn't ok or what it is about,
    `warnings` what is worth knowing but doesn't make it fail, `user` who it was for or by
    (the holder for a Release or a Quarantine, whoever asked for a Re-check, the admin).
    """
    INSPECTION = 'inspection'
    RELEASE = 'release'
    CLEANUP = 'cleanup'
    QUARANTINE = 'quarantine'
    QUARANTINE_LIFTED = 'quarantine_lifted'
    OUT_OF_SERVICE = 'out_of_service'
    BACK_IN_SERVICE = 'back_in_service'
    KINDS = [(INSPECTION, 'Inspection'), (RELEASE, 'Release'), (CLEANUP, 'Cleanup'),
             (QUARANTINE, 'Quarantine'), (QUARANTINE_LIFTED, 'Quarantine lifted'),
             (OUT_OF_SERVICE, 'Out of service'), (BACK_IN_SERVICE, 'Back in service')]

    # No database constraint: main's code deletes Switches without knowing this table (ADR 0002)
    switch = models.ForeignKey(Switch, related_name='events', on_delete=models.CASCADE, db_constraint=False)
    kind = models.CharField(max_length=32, choices=KINDS)
    at = models.DateTimeField(default=timezone.now)
    ok = models.BooleanField(null=True)
    reasons = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    # No database constraint: main's code deletes Users without knowing this table (ADR 0002)
    user = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
                             db_constraint=False)

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


class PendingCleanup(models.Model):
    """
    A Cleanup the Switch worker still has to carry out (api.switch_worker): restore init and
    reload, wait for the Switch to come back, Inspect it, and Quarantine it in `holder`'s
    name if it isn't clean. The Switch can't be reserved meanwhile. Deleted once done.
    """
    # No database constraint: main's code deletes Switches and Users without knowing this table (ADR 0002)
    switch = models.OneToOneField(Switch, related_name='pending_cleanup', on_delete=models.CASCADE,
                                  db_constraint=False)
    holder = models.ForeignKey(User, null=True, blank=True, related_name='+', on_delete=models.SET_NULL,
                               db_constraint=False)
    requested_at = models.DateTimeField(default=timezone.now)
    # Set once the reload is started (or failed to start): from then on, it waits to Inspect
    started_at = models.DateTimeField(null=True, blank=True)
    next_inspection_at = models.DateTimeField(null=True, blank=True)
    give_up_at = models.DateTimeField(null=True, blank=True)  # Inspected as it is then, reachable or not

    def __str__(self):
        return f"Cleanup of {self.switch}"


class SwitchAccount(models.Model):
    """
    A Switch account (see CONTEXT.md): a login BLab creates on a Switch for one user who may
    work on it, kept by api.switch_accounts. `name` is the local user on the Switch, and
    `password` is shown to that user only. The row stays until the account is removed from
    the Switch, so an account BLab couldn't remove is never forgotten.
    """
    # No database constraint: main's code deletes Switches and Users without knowing this table
    # (ADR 0002). A deleted User's account must still be removed: the row stays (DO_NOTHING).
    switch = models.ForeignKey(Switch, related_name='accounts', on_delete=models.CASCADE, db_constraint=False)
    user = models.ForeignKey(User, related_name='+', on_delete=models.DO_NOTHING, db_constraint=False)
    name = models.CharField(max_length=63)
    password = models.CharField(max_length=64)
    created = models.BooleanField(default=False)  # BLab has created it on the Switch
    error = models.TextField(null=True, blank=True)  # why the last attempt to create or remove it failed
    tried_at = models.DateTimeField(null=True, blank=True)  # when BLab last tried to create or remove it

    class Meta:
        constraints = [models.UniqueConstraint(fields=['switch', 'user'], name='one_switch_account_per_user')]

    def __str__(self):
        return f"Switch account {self.name} on {self.switch}"


class Quarantine(models.Model):
    """
    A Switch taken out of reservation because an Inspection after Cleanup found it not clean
    (see CONTEXT.md). Open while `lifted_at` is empty. `holder` is the last holder, who must
    clear it; when there is none, an admin does. While named in an open one, a user can't
    make new Reservations.
    """
    # No database constraint: main's code deletes Switches and Users without knowing this table (ADR 0002)
    switch = models.ForeignKey(Switch, related_name='quarantines', on_delete=models.CASCADE, db_constraint=False)
    holder = models.ForeignKey(User, null=True, blank=True, related_name='quarantines', on_delete=models.SET_NULL,
                               db_constraint=False)
    opened_at = models.DateTimeField(default=timezone.now)
    reasons = models.JSONField(default=list, blank=True)  # what the Inspection found
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-opened_at', '-id']

    def __str__(self):
        state = 'lifted' if self.lifted_at else 'open'
        return f"Quarantine of {self.switch} ({state})"


class Reservation(models.Model):
    """
    Represents a reservation for a switch. A Switch has at most one at a time.
    Deleting one only deletes the row: ending a Reservation is api.release.release().

    Attributes:
        switch (Switch): Switch associated with the reservation.
        user (User): User who made the reservation.
        creation_date (datetime): Date and time when the reservation was created.
        end_date (datetime): Date and time when the reservation ends.
        renewals (int): How many times it has been Renewed.
        admin_exception (bool): Whether an admin set its end date beyond the limits.
    """
    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(auto_now_add=True)
    # Required and limited by api.reservations; still nullable in the database because
    # production's older code may create Reservations without one (docs/adr/0002)
    end_date = models.DateTimeField(null=True, blank=True)
    # Renewals so far (see CONTEXT.md)
    renewals = models.PositiveSmallIntegerField(default=0, db_default=0)
    # An admin set an end date beyond the limits: it isn't capped, and isn't Renewed
    admin_exception = models.BooleanField(default=False, db_default=False)

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
    # The Link worker's last Reconcile found this Port's Link a Ghost Link (api.reconcile): why, in
    # plain words, and when that Reconcile ran. Like the teardown fields, it holds for ghost_svlan
    # only, so a Port that production's older code unlinks and relinks never shows an old reason.
    ghost_svlan = models.IntegerField(null=True, blank=True)
    ghost_reason = models.TextField(null=True, blank=True)
    ghost_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.switch}_{self.port_backbone}"

    @property
    def teardown_pending(self) -> bool:
        return self.teardown_requested_at is not None and self.svlan is not None and self.teardown_svlan == self.svlan

    @property
    def ghost(self) -> bool:
        """Whether the last Reconcile found this Port's current Link a Ghost Link."""
        return self.ghost_reason is not None and self.svlan is not None and self.ghost_svlan == self.svlan

class TopologyShare(models.Model):
    """
    Represents a topology sharing between two users.
    """
    owner = models.ForeignKey(User, related_name='shared_topologies', on_delete=models.CASCADE)
    target = models.ForeignKey(User, related_name='received_topologies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Topology of {self.owner.username} shared with {self.target.username}"


class Sweep(models.Model):
    """
    One nightly Sweep (see CONTEXT.md), carried out by the Switch worker a few Switches at a
    time (api.sweep). Ids of Switches: `swept` it has looked at, `skipped` it ran out of time
    for, `cleanups_asked` it asked the Switch worker to Clean up. `quarantines_seen` holds the
    ids of the open Quarantines it found, so it can tell which it lifted. Once its Cleanups are
    done it is finished, and `problems` holds what is wrong, one line each: empty when all is well.
    """
    started_at = models.DateTimeField(default=timezone.now)
    # True while under way, None once finished: unique, so two Sweeps are never under way at once
    under_way = models.BooleanField(null=True, default=True)
    swept = models.JSONField(default=list, blank=True)
    skipped = models.JSONField(default=list, blank=True)
    cleanups_asked = models.JSONField(default=list, blank=True)
    quarantines_seen = models.JSONField(default=list, blank=True)
    problems = models.JSONField(default=list, blank=True)
    summary = models.TextField(blank=True, default='')
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-started_at', '-id']
        constraints = [models.UniqueConstraint(fields=['under_way'], name='one_sweep_under_way')]

    def __str__(self):
        return f"Sweep of {self.started_at:%Y-%m-%d %H:%M}"


class SwitchPosition(models.Model):
    """
    Where a Switch is drawn on a user's Topology (the Topology layout): its centre on the
    canvas, which places its ports around it. Saved per Topology owner, so everyone viewing a
    shared Topology sees the same picture. Re-arrange removes the owner's positions.
    """
    # No database constraint: main's code deletes Switches and Users without knowing this table
    # (ADR 0002). A position left behind by a deletion is never read: only Switches drawn are.
    owner = models.ForeignKey(User, related_name='+', on_delete=models.CASCADE, db_constraint=False)
    switch = models.ForeignKey(Switch, related_name='+', on_delete=models.CASCADE, db_constraint=False)
    x = models.FloatField()
    y = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['owner', 'switch'], name='one_position_per_switch')]

    def __str__(self):
        return f"{self.switch} on {self.owner}'s Topology"
