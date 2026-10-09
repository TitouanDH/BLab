"""
The Sweep (see CONTEXT.md): BLab's nightly pass over every Switch that isn't reserved, carried
out by the Switch worker under its lock (docs/adr/0005), so only one Sweep runs at a time
across production and pre-prod.

It starts at 02:00 Europe/Paris, or as soon after as the worker can until 06:00 (a worker
down all night skips that night rather than reloading Switches in the morning). It skips the
Switches that are reserved, Out of service, being Cleaned up, or without a management IP.
For each of the others, a few per worker cycle so that Cleanups keep moving:

1. It brings the Switch accounts in step (an account left behind fails the Inspection).
2. It Inspects the Switch. If BLab can reach it, it checks whether the Switch changed since
   its last Cleanup: its config differs from init, or someone other than BLab logged in
   since it last booted (every Cleanup reboots it, and the log of earlier logins is rotated
   then; see LabSwitch.logins_since_boot). BLab's own logins are `admin` from
   BLAB_SOURCE_ADDRESSES.
3. With BLAB_SWEEP_MODE=enforce, a Switch that changed is handed to the Switch worker for a
   Cleanup, which Inspects it once it is back. Any other is settled at once on this
   Inspection. Either way, a Switch that isn't clean is Quarantined, naming nobody (it has
   no holder: an admin clears it), and a Quarantined Switch that is clean has its Quarantine
   lifted. With BLAB_SWEEP_MODE=report (the default), it does none of that, and reports
   what it would have done instead.

Once its Cleanups are done, the Sweep is finished with what is wrong, if anything: the
Switches it looked at that are in Quarantine, what it would have done (report mode), and what
it couldn't do. The Lab status page shows that report when it isn't empty.
"""
import logging
from datetime import datetime, time, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import switch_accounts
from .inspection import CONFIG_DIFFERS, InspectionResult, inspect, inspect_and_record
from .lab_switch import SWITCH_ADMIN, LabSwitchError, lab_switch
from .models import NO_MANAGEMENT_IP, PendingCleanup, Quarantine, Reservation, Switch, Sweep
from .quarantine import settle
from .switch_worker import request_cleanup

logger = logging.getLogger(__name__)

SWEEP_ZONE = ZoneInfo('Europe/Paris')
SWEEP_AT = time(2, 0)                 # when the nightly Sweep starts, Paris time
# How long after 02:00 it may still start, and how long after it started it may still look
# at Switches: never into the working day
SWEEP_WINDOW = timedelta(hours=4)
SWITCHES_PER_STEP = 3                 # Switches looked at per worker cycle
SHOWN_LOGINS = 3                      # logins quoted in the reason to Clean up


def enforcing() -> bool:
    """Whether the Sweep acts (Cleans up, Quarantines, lifts), or only reports what it would do."""
    return settings.BLAB_SWEEP_MODE == 'enforce'


def last_start_time(now: datetime) -> datetime:
    """The latest 02:00 in Paris at or before now."""
    local = now.astimezone(SWEEP_ZONE)
    start = datetime.combine(local.date(), SWEEP_AT, tzinfo=SWEEP_ZONE)
    if local < start:
        start = datetime.combine(local.date() - timedelta(days=1), SWEEP_AT, tzinfo=SWEEP_ZONE)
    return start


def due(now: datetime) -> bool:
    """Whether tonight's Sweep should start now: in the window, and not started yet."""
    start = last_start_time(now)
    return now < start + SWEEP_WINDOW and not Sweep.objects.filter(started_at__gte=start).exists()


def under_way() -> Optional[Sweep]:
    """The Sweep under way, if any (there is at most one)."""
    return Sweep.objects.filter(under_way=True).first()


def start(now: Optional[datetime] = None) -> Sweep:
    """Starts a Sweep now, whatever the time (the Switch worker carries it out), or returns the one under way."""
    try:
        with transaction.atomic():
            return Sweep.objects.create(started_at=now or timezone.now())
    except IntegrityError:  # one is under way already (one_sweep_under_way)
        return under_way()


def last_report() -> Optional[Sweep]:
    """The last finished Sweep, if it found something wrong."""
    sweep = Sweep.objects.filter(finished_at__isnull=False).order_by('-finished_at', '-id').first()
    return sweep if sweep is not None and sweep.problems else None


def candidates():
    """The Switches a Sweep looks at now, in a steady order."""
    return (Switch.objects.filter(Switch.in_service())
            .exclude(id__in=Reservation.objects.values('switch_id'))
            .exclude(id__in=PendingCleanup.objects.values('switch_id'))
            .exclude(mngt_IP=NO_MANAGEMENT_IP)
            .order_by('id'))


def logged_in_by_others(switch: Switch) -> List[str]:
    """
    Who logged in to the Switch since it last booted, other than BLab: 'user from address',
    once each. Raises LabSwitchError if the log can't be read.
    """
    ours = set(settings.BLAB_SOURCE_ADDRESSES)
    others = []
    for login in lab_switch(switch.mngt_IP).logins_since_boot():
        if login.user == SWITCH_ADMIN and login.address in ours:
            continue
        who = f"{login.user} from {login.address or login.through or 'an unknown place'}"
        if who not in others:
            others.append(who)
    return others


def why_clean_up(switch: Switch, result: InspectionResult) -> List[str]:
    """Why the Switch changed since its last Cleanup, judged on this Inspection and its login log; [] if it didn't."""
    if not result.reached:
        return []  # nothing to read, and nothing a Cleanup could do
    why = [w for w in result.warnings if w.startswith(CONFIG_DIFFERS)] if result.config_differs else []
    try:
        others = logged_in_by_others(switch)
    except LabSwitchError as e:
        # An unreadable log alone is no reason to reload a Switch every night: judged on its config
        logger.warning("Sweep: cannot tell who logged in to %s, judging it on its config: %s", switch.mngt_IP, e)
        return why
    if others:
        more = len(others) - SHOWN_LOGINS
        why.append(f"logged in since its last reload by someone other than BLab: {', '.join(others[:SHOWN_LOGINS])}"
                   + (f" and {more} more" if more > 0 else ''))
    return why


class Sweeper:
    def __init__(self, now=timezone.now):
        self.now = now

    def step(self) -> List[str]:
        """Starts tonight's Sweep if it is due, and carries the one under way a few Switches on."""
        now = self.now()
        outcomes = []
        sweep = under_way()
        if sweep is None:
            if not due(now):
                return []
            sweep = start(now)
            outcomes.append(f"Sweep started at {now.astimezone(SWEEP_ZONE):%Y-%m-%d %H:%M} (Paris), "
                            f"mode {settings.BLAB_SWEEP_MODE}")
            logger.info(outcomes[-1])

        todo = list(candidates().exclude(id__in=sweep.swept + sweep.skipped))
        if todo and now >= sweep.started_at + SWEEP_WINDOW:
            # Never reload Switches in the working day: those left wait for the next night
            sweep.problems.append(f"Not swept, out of time: {', '.join(s.mngt_IP for s in todo)}")
            sweep.skipped.extend(s.id for s in todo)
            sweep.save(update_fields=['problems', 'skipped'])
            todo = []
        for switch in todo[:SWITCHES_PER_STEP]:
            try:
                outcome = self.sweep_switch(sweep, switch)
            except Exception as e:
                # One broken Switch must not stop the Sweep
                logger.exception("Sweep of %s failed", switch.mngt_IP)
                outcome = f"Sweep could not look at {switch.mngt_IP}: {e}"
                sweep.problems.append(outcome)
            sweep.swept.append(switch.id)
            sweep.save(update_fields=['swept', 'cleanups_asked', 'quarantines_seen', 'problems'])
            outcomes.append(outcome)

        if len(todo) <= SWITCHES_PER_STEP and not PendingCleanup.objects.filter(
                switch_id__in=sweep.cleanups_asked).exists():
            outcomes.append(self.finish(sweep))
        return outcomes

    def sweep_switch(self, sweep: Sweep, switch: Switch) -> str:
        """
        Cleans the Switch up if it changed, or settles it on an Inspection made now; in report
        mode, adds what it would have done to the report instead. Returns what it did.
        """
        warnings = switch_accounts.sync(switch, self.now())
        result = inspect(switch)
        why = why_clean_up(switch, result)
        with transaction.atomic():
            # Reserved, or Released, while BLab was reading it: it is no longer the Sweep's
            locked = Switch.objects.select_for_update().get(pk=switch.pk)
            if Reservation.objects.filter(switch=locked).exists() or PendingCleanup.objects.filter(switch=locked).exists():
                return f"Sweep: {switch.mngt_IP} was reserved or released meanwhile, left alone"
            if locked.out_of_service:
                return f"Sweep: {switch.mngt_IP} was put Out of service meanwhile, left alone"
            quarantine = locked.open_quarantine()
            if quarantine is not None:
                sweep.quarantines_seen.append(quarantine.id)
            # Recorded either way, so the history says why the Sweep Cleaned it up
            result.warnings.extend(warnings + [w for w in why if w not in result.warnings])
            inspect_and_record(locked, result)
            if not enforcing():
                outcome = self.would(sweep, locked, result, why, quarantine)
            elif why:
                # Like a Release's, without a holder to name: the Switch worker carries it out
                request_cleanup(locked, None)
                sweep.cleanups_asked.append(switch.id)
                outcome = f"Sweep: {switch.mngt_IP} changed ({'; '.join(why)}): Cleaning it up"
            else:
                outcome = f"Sweep: {switch.mngt_IP} " + settle(locked, result, None, 'the Sweep found it clean')
        logger.info(outcome)
        return outcome

    @staticmethod
    def would(sweep: Sweep, switch: Switch, result: InspectionResult, why: List[str],
              quarantine: Optional[Quarantine]) -> str:
        """Report mode: adds to the report what enforce mode would do to the Switch, and returns it."""
        ip = switch.mngt_IP
        if why:
            found = f" (its Inspection now: {'; '.join(result.reasons)})" if result.reasons else ''
            would = f"Would Clean up {ip}: {'; '.join(why)}{found}"
        elif result.clean and quarantine is not None:
            would = f"Would lift the Quarantine of {ip}: it is clean"
        elif not result.clean and quarantine is None:
            would = f"Would Quarantine {ip}, naming nobody: {'; '.join(result.reasons)}"
        else:
            return f"Sweep (report only): {ip} {'clean' if result.clean else 'not clean, still in Quarantine'}"
        sweep.problems.append(would)
        return f"Sweep (report only): {would}"

    def finish(self, sweep: Sweep) -> str:
        """Writes down what is wrong now with the Switches it looked at, and closes the Sweep."""
        problems = list(sweep.problems)
        for quarantine in (Quarantine.objects.filter(lifted_at__isnull=True, switch_id__in=sweep.swept)
                           .select_related('switch', 'holder').order_by('switch__mngt_IP')):
            named = f"names {quarantine.holder.username}" if quarantine.holder else 'for an admin'
            problems.append(f"{quarantine.switch.mngt_IP} is in Quarantine ({named}): {'; '.join(quarantine.reasons)}")
        # The Quarantines it found that are lifted now: by the Sweep itself, save a rare Re-check
        # between its Inspection and now (a Re-check can't run during a Cleanup)
        lifted = Quarantine.objects.filter(id__in=sweep.quarantines_seen, lifted_at__isnull=False).count()
        if enforcing():
            done = (f"Swept {len(sweep.swept)} Switch(es): Cleaned up {len(sweep.cleanups_asked)}, "
                    f"lifted {lifted} Quarantine(s)")
        else:
            done = (f"Report only (BLAB_SWEEP_MODE=report): swept {len(sweep.swept)} Switch(es), "
                    f"and Cleaned up, Quarantined or lifted none")
        sweep.problems = problems
        sweep.summary = f"{done}, {len(problems)} problem(s)."
        sweep.finished_at = self.now()
        sweep.under_way = None
        sweep.save(update_fields=['problems', 'summary', 'finished_at', 'under_way'])
        log = logger.warning if problems else logger.info
        log("Sweep finished. %s %s", sweep.summary, ' '.join(problems))
        return f"Sweep finished. {sweep.summary}"
