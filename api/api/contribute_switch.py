"""
Contribute a Switch (see CONTEXT.md, Contribution; #59): a user adds a new Switch to the lab,
guided, with no admin approval. It goes through the same steps as bulk onboarding (#55).

1. The user gives the model, and says whether it has an EMP; without one, which front port they
   give over to management (its Management port, #81). BLab picks its management address: the
   lowest of the Management network that no device records, no Contribution holds, and nothing
   answers on. It holds it for this Contribution, 24 hours at most.
2. BLab gives the workflow, every command filled in (Titouan, 2026-10-20). The EMP's address can
   only be set at the console, in the boot parameters, and only takes effect after a reboot.
   So: the boot parameters at the console; then the routes and local AAA, saved; then the EMP
   cabled to the rack's Management switch and a reload. Without an EMP: VLAN 1443 untagged on the
   Management port, the address on an IP interface in it, the routes and local AAA, saved, and
   that port cabled to the rack's Management switch; no reload. Admin keeps its factory password:
   onboarding gives it the lab's secret one.
3. Done: BLab checks it answers at that address and logs in, then the Switch worker onboards
   it (api.onboarding, one step per cycle): records it Out of service, installs its Golden
   init and Cleans it up, reads its Ports, and puts it in service once its first Inspection
   finds it clean. The user may then Contribute a cable to it.

A Switch without an EMP keeps its management in its Golden VC setup (init/vcsetup.cfg,
clean_state.golden_vc_setup), which onboarding installs: so only a model with a Golden VC setup
can be contributed without one. An AOS 6 Switch (#108) keeps it in its Golden init (init/boot.cfg),
and its console commands are AOS 6's; BLab takes AOS 6 Switches without an EMP only, as the lab's
OS6450s are.
"""
import logging
import re
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.db import connection, transaction

from . import fake_devices, management_network
from .clean_state import (AUTO_VFL_COMMAND, GOLDEN_INIT_ROUTES, aos6_management_lines, golden_vc_setup,
                          management_lines, management_of, runs_aos6)
from .management_network import MANAGEMENT_VLAN
from .models import Onboarding, Port, Switch, SwitchContribution
from .onboarding import NOT_CLEAN, Onboarder, answers_ssh, one_onboarding_at_a_time

logger = logging.getLogger(__name__)

HOLD_FOR = timedelta(hours=24)  # an address held for a Contribution nobody finished is free again then
MOST_HELD = 3  # Contributions a user may have under way at once
PROBED_PORTS = (22, 23, 80, 443)  # what "nothing answers" is checked on
LOOK_EVERY = timedelta(seconds=30)  # between two steps of a Contribution's onboarding, in the Switch worker
RECHECK_EVERY = timedelta(minutes=10)  # between two Inspections of a contributed Switch found not clean
CHASSIS_MODELS = ('OS99',)  # chassis (OS9907, OS9912): an EMP per CMM
ADDRESS_LOCK = 0x424C4149  # pg advisory lock key ("BLAI"): two Contributions never get the same address
NO_MANAGEMENT_PORT = ("Without an EMP, give the front port you give over to management, as show interfaces "
                      "names it (e.g. 1/1/48): usually its last one.")
AOS6_NO_MANAGEMENT_PORT = ("Without an EMP, give the front port you give over to management, as AOS 6's show "
                           "interfaces names it (slot/port, e.g. 2/48): usually its last copper one.")
AOS6_WITH_EMP = ("BLab takes AOS 6 Switches without an EMP only, through a front port given over to management. "
                 "Ask an admin.")
PORT = re.compile(r'\d+/\d+/\d+[A-Za-z]?')  # a front port on AOS 8: chassis/slot/port
AOS6_PORT = re.compile(r'\d+/\d+')       # and on AOS 6: slot/port (#108)


class Refused(Exception):
    """The Contribution can't go on: the message says why, in words the user acts on."""


def held(now: datetime):
    """The Contributions whose address is held: under way, and if not Done yet, started within HOLD_FOR."""
    return (SwitchContribution.objects.filter(state=SwitchContribution.ONBOARDING)
            | SwitchContribution.objects.filter(state=SwitchContribution.HELD, created_at__gt=now - HOLD_FOR))


def answers(ip: str) -> bool:
    """Whether anything answers there, on any of PROBED_PORTS (a fake device: one in lab_switches.present)."""
    if fake_devices.devices_are_fake():
        return answers_ssh(ip)
    for port in PROBED_PORTS:
        try:
            with socket.create_connection((ip, port), timeout=0.5):
                return True
        except ConnectionRefusedError:
            return True  # a refusal is an answer: something is there
        except OSError:
            continue
    return False


def taken_addresses(now: datetime) -> set:
    return (set(Switch.everything.values_list('mngt_IP', flat=True))
            | set(Port.objects.values_list('backbone', flat=True))
            | set(Onboarding.objects.values_list('mngt_IP', flat=True))
            | set(held(now).values_list('mngt_IP', flat=True)))


@contextmanager
def _one_pick_at_a_time():
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [ADDRESS_LOCK])
        yield


def start(user: User, model: str, has_emp: bool, now: datetime, management_port: str = '') -> SwitchContribution:
    """Holds a management address for a new Switch. Without an EMP, `management_port` is the front port
    given over to management. Raises Refused."""
    model = (model or '').strip()
    if not model:
        raise Refused("Give the Switch's model, as show chassis names it (e.g. OS6860E-24).")
    management_port = '' if has_emp else (management_port or '').strip()
    aos6 = runs_aos6(model)
    if aos6 and has_emp:
        raise Refused(AOS6_WITH_EMP)
    if not has_emp:
        if not (AOS6_PORT if aos6 else PORT).fullmatch(management_port):
            raise Refused(AOS6_NO_MANAGEMENT_PORT if aos6 else NO_MANAGEMENT_PORT)
        if not aos6 and golden_vc_setup(model) is None:
            raise Refused(f"A {model} without an EMP can't be contributed: BLab keeps no VC setup for it, where its "
                          "management would live. Ask an admin.")
    with _one_pick_at_a_time():
        if held(now).filter(user=user, state=SwitchContribution.HELD).count() >= MOST_HELD:
            raise Refused(f"You have {MOST_HELD} Switches being contributed already: finish or cancel one first.")
        taken = taken_addresses(now)
        for subnet in management_network.subnets():
            for ip in subnet.hosts():
                if ip not in taken and not answers(ip):
                    contribution = SwitchContribution.objects.create(user=user, model=model, has_emp=has_emp,
                                                                     management_port=management_port,
                                                                     mngt_IP=ip, created_at=now)
                    logger.info("Contribution of a %s by %s: holding %s", model, user.username, ip)
                    return contribution
    raise Refused("The Management network has no free address left. Ask an admin.")


def expires_at(contribution: SwitchContribution) -> datetime:
    """When the address it holds is free again, if it isn't Done by then."""
    return contribution.created_at + HOLD_FOR


def expired(contribution: SwitchContribution, now: datetime) -> bool:
    return contribution.state == SwitchContribution.HELD and now >= expires_at(contribution)


def _hours(duration: timedelta) -> str:
    return f"{int(duration.total_seconds() // 3600)} hours"


def _block(commands: List[str], then: str = '') -> Dict:
    """Commands pasted at once, and what to do once they are in (#89). AOS's boot parameter parser fails a
    line with a stray character, so each line is the bare words, one space apart, and a block holds no question
    but at its end."""
    return {'commands': [' '.join(command.split()) for command in commands], 'then': then}


def workflow(contribution: SwitchContribution) -> List[Dict]:
    """The steps the user follows: each with its title, where it is done, a note, and its blocks of commands.
    A block ends wherever AOS asks something or needs time, so a paste never answers a question by accident."""
    ip = contribution.mngt_IP
    subnet = management_network.subnet_of(ip)
    gateway, length = (subnet.gateway, subnet.network.prefixlen) if subnet else ('<gateway>', '<mask length>')
    routes_and_aaa = ([f'ip static-route {route} gateway {gateway} metric 1' for route in GOLDEN_INIT_ROUTES]
                      + ['aaa authentication ssh "local"', 'aaa authentication console "local"', 'write memory'])
    factory_password = ("Leave admin's password as it came (switch): BLab gives it the lab's own when it takes the "
                        "Switch in.")
    if not contribution.has_emp:
        return _workflow_without_emp(contribution, routes_and_aaa, factory_password)
    chassis = contribution.model.upper().startswith(CHASSIS_MODELS)
    return [
        {'title': 'Set the EMP address', 'where': "At the Switch's console (a console cable): the EMP address "
                                                  "can only be set there",
         # One line a block: on Windows a copied line break becomes CR LF, and the parser takes the CR into the
         # command (#89)
         'blocks': [_block(['modify boot parameters'], 'Wait for the Boot > prompt.'),
                    _block([f'boot empipaddr {ip}']), _block([f'boot empmasklength {length}']),
                    _block(['commit boot'], 'It asks whether to commit to NVRAM anyway: answer y.'),
                    _block(['commit system']), _block(['exit'])],
         'note': ('Paste one line at a time. commit boot keeps the address across reboots; commit system alone '
                  'would lose it. It takes effect once the Switch reboots.'
                  + (" On a chassis, do this at each CMM's console: each CMM has its own EMP." if chassis else ''))},
        {'title': 'Add the routes and local logins, and save', 'where': 'Back at the CLI, still at the console',
         'blocks': [_block(routes_and_aaa)],
         'note': factory_password},
        {'title': 'Cable the EMP and reload', 'where': "The EMP port, to the rack's Management switch",
         'blocks': [_block(['reload from working no rollback-timeout'], 'If it asks to confirm, answer y.')],
         'note': f'Plug the EMP into a port of the Management switch that is untagged in VLAN {MANAGEMENT_VLAN}, '
                 'then reload. Once it is up, press Done.'},
    ]


def _workflow_without_emp(contribution: SwitchContribution, routes_and_aaa: List[str],
                          factory_password: str) -> List[Dict]:
    """A Switch without an EMP (#81): its Management port, a front port, untagged in the Management network's
    VLAN with its address on it. Once BLab takes it in, these lines live in its Golden VC setup (vcsetup.cfg);
    on AOS 6, in its Golden init (init/boot.cfg), and the commands are AOS 6's (#108)."""
    port = contribution.management_port
    cable = {'title': 'Cable the Management port', 'where': f"Port {port}, to the rack's Management switch",
             'blocks': [],
             'note': f'Plug {port} into a port of the Management switch that is untagged in VLAN {MANAGEMENT_VLAN}. '
                     'No reload is needed: once its link is up, press Done.'}
    if runs_aos6(contribution.model):
        subnet = management_network.subnet_of(contribution.mngt_IP)
        gateway = subnet.gateway if subnet else '<gateway>'
        routes = [f'ip static-route {route} gateway {gateway} metric 1' for route in GOLDEN_INIT_ROUTES]
        aaa = ['aaa authentication default "local"', 'aaa authentication ssh "local"', 'write memory']
        return [
            {'title': 'Give the Management port over to management, and save',
             'where': "At the Switch's console (a console cable)",
             'blocks': [_block(aos6_management_lines(management_of(contribution.mngt_IP, port)) + routes + aaa)],
             'note': f'{port} becomes its Management port for good: no Link ever uses it. When BLab takes the Switch '
                     f'in, it keeps these lines in its Golden init (init/boot.cfg), so every Cleanup brings them back. '
                     f'{factory_password}'},
            cable,
        ]
    return [
        {'title': 'Give the Management port over to management, and save',
         'where': "At the Switch's console (a console cable)",
         # AOS's default VC setup keeps the last ports as auto-VFL ports, which carry no VLAN (#94): until BLab
         # installs its Golden VC setup, the Management port must be taken out of them by hand
         'blocks': [_block([AUTO_VFL_COMMAND], f'If it lists {port}, paste the next line; otherwise skip it.'),
                    _block([f'no virtual-chassis auto-vf-link-port {port}']),
                    _block(management_lines(management_of(contribution.mngt_IP, port)) + routes_and_aaa)],
         'note': f'{port} becomes its Management port for good: no Link ever uses it. When BLab takes the Switch '
                 f'in, it keeps these lines in its vcsetup.cfg, so every Cleanup brings them back. {factory_password}'},
        cable,
    ]


def done(user: User, contribution: SwitchContribution, now: datetime) -> SwitchContribution:
    """The user set the Switch up: BLab checks it answers and logs in, then onboards it. Raises Refused."""
    if contribution.state != SwitchContribution.HELD:
        raise Refused("BLab is taking this Switch in already." if contribution.state == SwitchContribution.ONBOARDING
                      else "This Switch is in the lab already.")
    if expired(contribution, now):
        raise Refused(f"{contribution.mngt_IP} was held for {_hours(HOLD_FOR)} only and is free again: start a new "
                      "Contribution, which may give another address.")
    ip = contribution.mngt_IP
    if not answers_ssh(ip):
        if not contribution.has_emp:
            raise Refused(f"Nothing answers SSH at {ip} yet. Check that {contribution.management_port} is cabled to "
                          f"a port untagged in VLAN {MANAGEMENT_VLAN} and its link is up, the VLAN and the IP "
                          "interface (show vlan members, show ip interface), and the routes. Then press Done again.")
        raise Refused(f"Nothing answers SSH at {ip} yet. Check the EMP address (show the boot parameters at the "
                      f"console), that the EMP is cabled to a port untagged in VLAN {MANAGEMENT_VLAN}, that the "
                      "Switch rebooted, and the routes. Then press Done again.")
    fields, why = Onboarder().identify(ip)
    if why:
        raise Refused(f"{ip} answers, but BLab can't take it in: {why}. Check it is the Switch you set up, "
                      "and that admin still has its factory password.")
    found = f"Found a {fields['model']} at {ip}"
    if fields['model'].upper() != contribution.model.upper():
        found += f" (you said {contribution.model}: BLab records what the Switch says)"
    contribution.state = SwitchContribution.ONBOARDING
    contribution.detail = f"{found}. BLab is taking it in."
    contribution.save(update_fields=['state', 'detail'])
    logger.info("Contribution %s by %s: %s answers, onboarding it", contribution.id, user.username, ip)
    return contribution


def cancel(contribution: SwitchContribution) -> None:
    """Frees the address of a Contribution the user gives up, before Done. Raises Refused once BLab is taking it in."""
    if contribution.state != SwitchContribution.HELD:
        raise Refused("BLab is taking this Switch in already: it can't be cancelled.")
    contribution.delete()


class Advancer:
    """
    The Switch worker's part (api.switch_worker): moves each Contribution's onboarding one step
    on, every LOOK_EVERY, or every RECHECK_EVERY once its Inspection found it not clean. A step
    takes seconds (SSH), or LLDP_WAIT for the window that reads its Ports: the worker's other
    duties wait that long, once per Contribution. A bulk run under way has the onboarding lock:
    the Contributions then wait for it.
    """

    def __init__(self, now):
        self.now = now
        self.onboarder = Onboarder(now=now)  # kept: it remembers which Switches went down since their reload
        self.next_step: Dict[int, datetime] = {}

    def work(self) -> List[str]:
        now = self.now()
        due = [c for c in SwitchContribution.objects.filter(state=SwitchContribution.ONBOARDING)
               if now >= self.next_step.get(c.id, now)]
        if not due:
            return []
        with one_onboarding_at_a_time(wait=False) as got:
            if not got:
                return []
            self.onboarder.forget_deleted_switches()
            return [outcome for c in due if (outcome := self.advance(c))]

    def advance(self, contribution: SwitchContribution) -> Optional[str]:
        """One step of its onboarding, then what the user sees of it. What changed, or None."""
        ip = contribution.mngt_IP
        self.next_step[contribution.id] = self.now() + LOOK_EVERY
        if contribution.management_port:
            self.onboarder.management_ports[ip] = contribution.management_port
        try:
            self.onboarder.advance([ip])
        except Exception as e:  # one Contribution must not stop the worker: it says why, and is tried again
            logger.exception("Contribution %s failed", contribution.id)
            return self._show(contribution, contribution.state, f"BLab couldn't go on: {e}. It tries again.")
        row = Onboarding.objects.filter(mngt_IP=ip).first()
        if row is None:
            # It couldn't even be recorded: back to the user, who fixes it and presses Done again
            why = self.onboarder.failed.pop(ip, 'it could not be recorded')
            return self._show(contribution, SwitchContribution.HELD, f"BLab couldn't take it in: {why}. Fix it, then "
                                                                     "press Done again.")
        if row.step == Onboarding.DONE:
            return self._show(contribution, SwitchContribution.DONE, 'In the lab and in service.')
        if row.step == Onboarding.CABLED and row.detail.startswith(NOT_CLEAN):
            self.next_step[contribution.id] = self.now() + RECHECK_EVERY
            return self._show(contribution, contribution.state,
                              f"{row.detail}. Fix it at the Switch: BLab Inspects it again every "
                              f"{int(RECHECK_EVERY.total_seconds() // 60)} minutes.")
        return self._show(contribution, contribution.state, f"{dict(Onboarding.STEPS)[row.step]}. {row.detail}".strip())

    @staticmethod
    def _show(contribution: SwitchContribution, state: str, detail: str) -> Optional[str]:
        if (state, detail) == (contribution.state, contribution.detail):
            return None
        contribution.state, contribution.detail = state, detail
        contribution.save(update_fields=['state', 'detail'])
        return f"Contribution of {contribution.mngt_IP}: {detail}"


def as_dict(contribution: SwitchContribution, now: datetime) -> dict:
    """What the Contribute page shows of it: its workflow, how it goes, and its Switch once recorded."""
    switch = (Switch.objects.filter(mngt_IP=contribution.mngt_IP).first()
              if contribution.state != SwitchContribution.HELD else None)
    return {'id': contribution.id, 'model': contribution.model, 'mngt_IP': contribution.mngt_IP,
            'has_emp': contribution.has_emp, 'management_port': contribution.management_port, 'state': contribution.state, 'detail': contribution.detail,
            'created_at': contribution.created_at, 'expires_at': expires_at(contribution),
            'expired': expired(contribution, now), 'steps': workflow(contribution),
            'switch': switch.id if switch else None}
