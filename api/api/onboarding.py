"""
Bulk onboarding (#55): onboard_switches scans the Management network and takes in, in bulk, the
Switches BLab doesn't record yet. Before this, it took three commands on a hand-made IP list
(populate_switches, prepare_switches, populate_ports).

For each address, in steps saved in its Onboarding row, so running it again goes on from there:
1. Found: it answers SSH, BLab's admin login, and says it is AOS. Left out: the gateway, the
   Backbones, what BLab records already, anything that isn't AOS or refuses the login, and the
   Management switches (by model).
2. Recorded: admin gets the lab's secret password if it still has a factory one (every lab
   Switch must have it: Titouan, 2026-10-20), and the Switch is recorded from show chassis,
   Out of service while it is onboarded, so nothing reserves, Sweeps or syncs it meanwhile.
3. Reloading: init/ gets its AOS image, pkg/ and the Golden init, and it is Cleaned up as a
   Release would (api.switch_worker.plan_repair). Every Switch reloads at once, rather than
   one after another through the Switch worker.
4. Back: rebooted and ready (api.reboot), within 20 minutes.
5. Cabled: its Ports read by LLDP from the Backbones, in one window for every Switch that is
   back: as populate_ports does, BLab turns the Backbones' LLDP on and enables their free ports
   (api.contribute: never a trunk, an uplink or a Port's), reads each Switch's neighbours, then
   disables them and turns LLDP off again. Nothing else is written to a Backbone.
6. Done: its first Inspection found it clean, and it is put in service. One that isn't clean
   stays Out of service, with why; it is Inspected again on the next run.

Without --apply it only scans and says what it would do: reading the Switches, writing nothing.
"""
import ipaddress
import logging
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional

from django.db import close_old_connections, connection, transaction
from django.utils import timezone

from . import contribute, fake_devices, links, management_network, reboot
from .backbone import APIRequestError, backbone
from .clean_state import golden_init_config
from .inspection import FRONT_PANEL_PORT, inspect, inspect_and_record, parse_chassis_ids
from .lab_switch import LabSwitchError, LoginRefused, lab_switch, port_order, secure_admin
from .models import LldpWindow, Onboarding, Port, Switch, SwitchEvent

logger = logging.getLogger(__name__)

ONBOARDING = 'Being onboarded by BLab (onboard_switches)'  # its Out of service reason meanwhile
LEFT_OUT, WOULD_ONBOARD, FAILED = 'left out', 'would onboard', 'failed'  # in the report, besides the steps
NOT_CLEAN = 'not clean, so still Out of service'  # how the detail of a first Inspection that failed starts
MANAGEMENT_SWITCH_MODELS = ('OS2260', 'OS2360')  # the racks' Management switches: never Switches
AOS = re.compile(r'Alcatel-Lucent|OmniSwitch', re.IGNORECASE)
RELOAD_TIMEOUT = timedelta(minutes=20)
LOOK_EVERY = timedelta(seconds=30)  # between two looks at the Switches reloading
LLDP_WAIT = 40.0  # seconds for the Switches to hear their Backbones' LLDP (sent every 30 s)
WORKERS = 8  # Switches recorded and reloaded at once
ONBOARDING_LOCK = 0x424C414F  # pg advisory lock key ("BLAO"): one onboarding at a time, bulk run or Switch worker
CHASSIS_FIELDS = {'model': r'Model Name:\s*([^,\n]+)', 'part_number': r'Part Number:\s*([^,\n]+)',
                  'hardware_revision': r'Hardware Revision:\s*([^,\n]+)',
                  'serial_number': r'Serial Number:\s*([^,\n]+)'}
# A Switch's LLDP neighbour, from show lldp remote-system: its port, then the Backbone's name and port
NEIGHBOUR = re.compile(r'Agents on Local Port\s+(\S+?):.*?System Name\s*=\s*([^,\n]+),.*?'
                       r'Port Description\s*=\s*[^\n]*?(\d+/\d+/\d+)', re.DOTALL)


@dataclass
class Outcome:
    """One address: how far it got, and why it stopped there (or was left out)."""
    ip: str
    step: str      # an Onboarding step, or LEFT_OUT, WOULD_ONBOARD or FAILED
    detail: str = ''

    def __str__(self):
        return f"{self.ip:<16} {self.step:<14} {self.detail}"


def parse_chassis(output: str) -> Optional[dict]:
    """The fields of a Switch record from show chassis (its first chassis), or None without a model."""
    fields = {name: (m.group(1).strip() if (m := re.search(pattern, output, re.IGNORECASE)) else '')
              for name, pattern in CHASSIS_FIELDS.items()}
    return fields if fields['model'] else None


def parse_neighbours(output: str) -> List[tuple]:
    """(Switch port, Backbone system name, Backbone port) for each LLDP neighbour in show lldp remote-system."""
    sections = re.split(r'(?=Remote LLDP nearest-bridge Agents on Local Port)', output or '')
    return [(m.group(1), m.group(2).strip(), m.group(3)) for m in map(NEIGHBOUR.search, sections) if m]


def answers_ssh(ip: str) -> bool:
    """Whether something listens on SSH there (a fake device: in lab_switches.present)."""
    if fake_devices.devices_are_fake():
        return ip in fake_devices.lab_switches.present
    try:
        with socket.create_connection((ip, 22), timeout=1.5):
            return True
    except OSError:
        return False


@contextmanager
def one_onboarding_at_a_time(wait: bool):
    """
    Holds the onboarding lock for this database session, so that a bulk run and the Switch
    worker (#59) never drive the same address, nor open LLDP windows, at once. Yields whether
    it got it: it waits for it if `wait`, and otherwise gives up at once.
    """
    if connection.vendor != 'postgresql':
        yield True
        return
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {'pg_advisory_lock' if wait else 'pg_try_advisory_lock'}(%s)", [ONBOARDING_LOCK])
        got = True if wait else cursor.fetchone()[0]
    try:
        yield got
    finally:
        if got:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [ONBOARDING_LOCK])


class Onboarder:
    def __init__(self, now: Callable[[], datetime] = timezone.now, sleep: Callable[[float], None] = time.sleep,
                 say: Callable[[str], None] = logger.info, backbones: Iterable[str] = ()):
        self.now = now
        self.sleep = sleep
        self.say = say  # progress, for the command's output
        self.backbones = list(backbones)  # read Ports from these too, besides those BLab's Ports name
        self.fake = fake_devices.devices_are_fake()
        self.failed: Dict[str, str] = {}  # why an address found as a Switch got no Onboarding row
        self.went_down = set()  # the addresses seen unreachable since their reload (api.reboot)

    # Which addresses

    def candidates(self, subnets: Iterable[str], ips: Iterable[str]) -> List[str]:
        """The addresses to look at: every host of the subnets but their gateways, and the ips given."""
        gateways = {s.gateway for s in management_network.subnets()}
        found = []
        for subnet in subnets:
            network = ipaddress.IPv4Network(subnet, strict=False)
            found += [str(ip) for ip in network.hosts() if str(ip) not in gateways]
        found += list(ips)
        return sorted(set(found), key=ipaddress.IPv4Address)

    def left_out(self, ip: str) -> Optional[str]:
        """Why BLab doesn't onboard this address, before talking to it, or None."""
        if ip in {s.gateway for s in management_network.subnets()}:
            return 'the gateway'
        if ip in set(Port.objects.values_list('backbone', flat=True)) | set(self.backbones):
            return 'a Backbone'
        recorded = Switch.everything.filter(mngt_IP=ip).first()
        if recorded and not Onboarding.objects.filter(mngt_IP=ip).exists():
            return 'a Tool BLab records already' if recorded.is_tool else 'a Switch BLab records already'
        return None

    def identify(self, ip: str):
        """(None, why it is left out), or (show chassis fields, None) for an AOS Switch BLab may onboard."""
        try:
            system, chassis = lab_switch(ip).identify()
        except LoginRefused:
            return None, "refuses BLab's admin login: not a lab Switch, or another password"
        except LabSwitchError as e:
            return None, f"doesn't answer BLab's login: {e}"
        if not AOS.search(system):
            return None, "doesn't say it runs AOS"
        fields = parse_chassis(chassis)
        if fields is None:
            return None, "show chassis gives no model"
        if fields['model'].upper().startswith(MANAGEMENT_SWITCH_MODELS):
            return None, f"a Management switch ({fields['model']})"
        chassis_ids = parse_chassis_ids(chassis) or []
        if len(chassis_ids) > 1:
            # Its Golden init is one chassis's: onboarding would break the VC up
            return None, f"a VC (chassis {', '.join(map(str, chassis_ids))}): onboard each chassis once it stands alone"
        return fields, None

    # The run

    def run(self, subnets: Iterable[str], ips: Iterable[str], apply: bool) -> List[Outcome]:
        if not apply:
            return self._run(subnets, ips, apply)
        with one_onboarding_at_a_time(wait=True):
            return self._run(subnets, ips, apply)

    def _run(self, subnets: Iterable[str], ips: Iterable[str], apply: bool) -> List[Outcome]:
        ips = list(ips)
        outcomes: Dict[str, Outcome] = {}
        todo = []
        if apply:
            self.close_windows_left_open()
            self.forget_deleted_switches()
        addresses = self.candidates(subnets, ips)
        self.say(f"Looking at {len(addresses)} address(es)...")
        for ip in addresses:
            onboarding = Onboarding.objects.filter(mngt_IP=ip).first()
            if onboarding is not None:
                if onboarding.step == Onboarding.DONE:
                    outcomes[ip] = Outcome(ip, Onboarding.DONE, 'onboarded already')
                elif apply:
                    todo.append(ip)
                else:
                    outcomes[ip] = Outcome(ip, onboarding.step, f"would go on from here. {onboarding.detail}".strip())
                continue
            why = self.left_out(ip) or (None if answers_ssh(ip) else "doesn't answer on SSH")
            if why:
                if ip in ips:  # an address asked for by name is reported; a subnet's are many
                    outcomes[ip] = Outcome(ip, LEFT_OUT, why)
                continue
            fields, why = self.identify(ip)
            if why:
                outcomes[ip] = Outcome(ip, LEFT_OUT, why)
            elif not apply:
                outcomes[ip] = Outcome(ip, WOULD_ONBOARD, fields['model'])
            else:
                todo.append(ip)
        if apply:
            self._each(todo, self.record_and_reload)
            self.wait_until_back(todo)
            self.read_ports([ip for ip in todo if self._step(ip) == Onboarding.BACK])
            for ip in todo:
                if self._step(ip) == Onboarding.CABLED:
                    self.first_inspection(ip)
            for ip in todo:
                row = Onboarding.objects.filter(mngt_IP=ip).first()
                outcomes[ip] = Outcome(ip, row.step, row.detail) if row else Outcome(ip, FAILED, self.failed.get(ip, ''))
        return sorted(outcomes.values(), key=lambda o: ipaddress.IPv4Address(o.ip))

    def forget_deleted_switches(self) -> None:
        """An address whose Switch an admin deleted meanwhile starts afresh."""
        recorded = set(Switch.everything.values_list('mngt_IP', flat=True))
        for row in Onboarding.objects.exclude(mngt_IP__in=recorded):
            self.say(f"{row.mngt_IP}: its Switch was deleted since it was {row.step}: onboarded afresh")
            row.delete()

    def _step(self, ip: str) -> Optional[str]:
        return Onboarding.objects.filter(mngt_IP=ip).values_list('step', flat=True).first()

    def _each(self, ips: List[str], do: Callable[[str], None]) -> None:
        """Does it for each address, several at once against real Switches (each in its own database connection)."""
        if self.fake or len(ips) < 2:
            for ip in ips:
                do(ip)
            return

        def one(ip):
            try:
                do(ip)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(one, ips))

    def _save(self, ip: str, step: str, detail: str = '', **fields) -> None:
        Onboarding.objects.update_or_create(mngt_IP=ip, defaults={'step': step, 'detail': detail,
                                                                  'updated_at': self.now(), **fields})

    def _note(self, ip: str, detail: str) -> None:
        """Why it stopped at its step, kept for the report and the next run."""
        Onboarding.objects.filter(mngt_IP=ip).update(detail=detail, updated_at=self.now())
        logger.warning("Onboarding %s: %s", ip, detail)

    # Steps 2 and 3

    def record_and_reload(self, ip: str) -> None:
        try:
            if self._step(ip) is None:
                self.record(ip)
            if self._step(ip) == Onboarding.RECORDED:
                self.reload(ip)
        except Exception as e:  # one Switch must not stop the others: it says why, and the next run goes on
            logger.exception("Onboarding %s failed", ip)
            if self._step(ip) is None:
                self.failed[ip] = f"failed: {e}"
            else:
                self._note(ip, f"failed: {e}")

    def record(self, ip: str) -> None:
        try:
            if secure_admin(ip):
                self.say(f"{ip}: admin now has the lab's secret password")
        except LabSwitchError as e:
            self.failed[ip] = f"couldn't give admin the lab's secret password: {e}"
            return
        fields, why = self.identify(ip)
        if why:
            self.failed[ip] = why
            return
        with transaction.atomic():
            switch = Switch.objects.create(mngt_IP=ip, console='TODO', out_of_service_reason=ONBOARDING,
                                           out_of_service_since=self.now(), **fields)
            SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.OUT_OF_SERVICE, ok=False, reasons=[ONBOARDING],
                                       at=self.now())
            self._save(ip, Onboarding.RECORDED)
        self.say(f"{ip}: recorded ({fields['model']}), Out of service while it is onboarded")

    def reload(self, ip: str) -> None:
        from .switch_worker import plan_repair  # switch_worker imports much of BLab
        switch = Switch.objects.get(mngt_IP=ip)
        device = lab_switch(ip)
        try:
            device.install_golden_init(golden_init_config(switch.model))
            repair, notes = plan_repair(switch)
        except LabSwitchError as e:
            self._note(ip, f"couldn't install its Golden init: {e}")
            return
        # Saved before the reload: a run killed meanwhile must not reload it a second time
        started = self.now()
        self._save(ip, Onboarding.RELOADING, reload_started_at=started)
        try:
            warnings = notes + device.restore_init_and_reload(repair)
        except LabSwitchError as e:
            self._save(ip, Onboarding.RECORDED, f"couldn't Clean it up and reload it: {e}")
            return
        SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.CLEANUP, ok=True, at=started,
                                   reasons=['Onboarding: Golden init installed'], warnings=warnings)
        self.say(f"{ip}: Golden init installed, reloading")

    # Step 4

    def wait_until_back(self, ips: List[str]) -> None:
        """Looks at the Switches reloading until each is back or RELOAD_TIMEOUT has passed since its reload."""
        while True:
            waiting = self.look_once(ips)
            if not waiting:
                return
            self.say(f"Waiting for {waiting} Switch(es) to come back...")
            self.sleep(LOOK_EVERY.total_seconds())

    def look_once(self, ips: List[str]) -> int:
        """Looks once at each of these Switches reloading: how many are still to wait for."""
        waiting = 0
        for row in Onboarding.objects.filter(step=Onboarding.RELOADING, mngt_IP__in=ips):
            since = self.now() - row.reload_started_at
            seen = reboot.look(row.mngt_IP, since, row.mngt_IP in self.went_down)
            if seen.state == reboot.DOWN:
                self.went_down.add(row.mngt_IP)
            if seen.state == reboot.BACK:
                self._save(row.mngt_IP, Onboarding.BACK)
                self.say(f"{row.mngt_IP}: back from its reload")
            elif since >= RELOAD_TIMEOUT:
                self._note(row.mngt_IP, f"not back {RELOAD_TIMEOUT} after its reload: {seen.detail or seen.state}")
            else:
                waiting += 1
        return waiting

    def advance(self, ips: List[str]) -> None:
        """
        Moves each address one step on, without waiting: what the Switch worker does each cycle
        for a contributed Switch (#59, api.contribute_switch). An address with no Onboarding row
        yet is recorded first.
        """
        for ip in ips:
            if self._step(ip) in (None, Onboarding.RECORDED):
                self.record_and_reload(ip)
        self.look_once(ips)
        self.read_ports([ip for ip in ips if self._step(ip) == Onboarding.BACK])
        for ip in ips:
            if self._step(ip) == Onboarding.CABLED:
                self.first_inspection(ip)

    # Step 5

    def read_ports(self, ips: List[str]) -> None:
        """One LLDP window on every Backbone, for the Switches back from their reload."""
        if not ips:
            return
        names = {}  # Backbone system name -> its ip
        free = {}   # Backbone ip -> the free ports enabled for the window
        try:
            for ip in sorted(set(contribute.backbones()) | set(self.backbones)):
                try:
                    names[backbone(ip).system_name()] = ip
                    free[ip] = self.open_window(ip)
                except Exception as e:  # one Backbone failing must not stop the others
                    logger.exception("Onboarding: LLDP window on backbone %s", ip)
                    self.say(f"Backbone {ip}: left out of the LLDP window: {e}")
            self.say(f"LLDP on {len(free)} Backbone(s), {sum(map(len, free.values()))} free port(s) enabled: "
                     "waiting for the Switches to hear it...")
            self.sleep(0 if self.fake else LLDP_WAIT)
            for ip in ips:
                try:
                    self.record_ports(ip, names, free)
                except Exception as e:
                    logger.exception("Onboarding %s: reading its Ports", ip)
                    self._note(ip, f"couldn't read its Ports: {e}")
        finally:
            for window in LldpWindow.objects.filter(backbone__in=list(free)):
                self.close_window(window)

    def open_window(self, ip: str) -> List[str]:
        """
        Turns the Backbone's LLDP on and enables its free ports, saving which in an LldpWindow
        first, so that a run killed meanwhile leaves the next one what to close.
        """
        bb = backbone(ip)
        ports = contribute.free_backbone_ports(ip)
        LldpWindow.objects.update_or_create(backbone=ip, defaults={'ports': ports, 'opened_at': self.now()})
        bb.set_lldp(True)
        for port in ports:
            bb.set_uni_admin_state(port, True)
        return ports

    def close_window(self, window: LldpWindow) -> None:
        """Disables the window's ports again and turns the Backbone's LLDP off, as it was. Kept if anything fails."""
        bb = backbone(window.backbone)
        failed = []
        for port in window.ports:
            try:
                bb.set_uni_admin_state(port, False)
            except APIRequestError as e:
                failed.append(port)
                logger.error("Onboarding: could not disable port %s of backbone %s again: %s", port, window.backbone, e)
        try:
            bb.set_lldp(False)
        except APIRequestError as e:
            failed.append('LLDP')
            logger.error("Onboarding: could not turn LLDP off on backbone %s: %s", window.backbone, e)
        if failed:
            window.ports = [port for port in window.ports if port in failed]
            window.save(update_fields=['ports'])
            self.say(f"Backbone {window.backbone}: COULD NOT CLOSE ITS LLDP WINDOW ({', '.join(failed)}): "
                     "the next run tries again; or disable those ports and LLDP by hand")
        else:
            window.delete()

    def close_windows_left_open(self) -> None:
        for window in LldpWindow.objects.all():
            self.say(f"Backbone {window.backbone}: closing the LLDP window a killed run left open")
            self.close_window(window)

    def record_ports(self, ip: str, names: Dict[str, str], free: Dict[str, List[str]]) -> None:
        switch = Switch.objects.get(mngt_IP=ip)
        try:
            neighbours = parse_neighbours(lab_switch(ip).lldp_neighbours())
        except LabSwitchError as e:
            self._note(ip, f"couldn't read its LLDP neighbours: {e}")
            return
        recorded = []
        for port, name, uni in sorted(neighbours, key=lambda n: port_order(n[0])):
            bb = names.get(name)
            if bb is None or uni not in free.get(bb, ()) or not FRONT_PANEL_PORT.fullmatch(port):
                continue  # a Backbone port BLab didn't enable is a trunk, a Port's, or not free
            with links.svlan_lock():
                if (Port.objects.filter(backbone=bb, port_backbone=uni).exists()
                        or Port.objects.filter(switch=switch, port_switch=port).exists()):
                    continue
                Port.objects.create(switch=switch, port_switch=port, backbone=bb, port_backbone=uni, status='DOWN')
            recorded.append(f"{port} to {bb} {uni}")
        self._save(ip, Onboarding.CABLED, 'Ports: ' + ', '.join(recorded) if recorded else 'no cable to a Backbone found')
        self.say(f"{ip}: " + (f"Ports {', '.join(recorded)}" if recorded else "no cable to a Backbone found"))

    # Step 6

    def first_inspection(self, ip: str) -> None:
        switch = Switch.objects.get(mngt_IP=ip)
        result = inspect(switch)
        inspect_and_record(switch, result)
        if not result.clean:
            self._note(ip, f"{NOT_CLEAN}: {'; '.join(result.reasons)}")
            return
        with transaction.atomic():
            if Switch.objects.filter(id=switch.id, out_of_service_reason=ONBOARDING).update(
                    out_of_service_reason=None, out_of_service_since=None):
                SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.BACK_IN_SERVICE, ok=True, at=self.now(),
                                           reasons=['Onboarded: its first Inspection found it clean'])
            self._save(ip, Onboarding.DONE, 'onboarded: clean, in service')
        self.say(f"{ip}: clean, in service")
