"""
Inspection (see CONTEXT.md): reading a Switch to decide whether it is clean. It only reads:
show commands and the init config file, through LabSwitch.read_for_inspection.

A Switch is clean when BLab can log in to it, it stands alone (one chassis in show chassis),
and, when it is not reserved, it has no Unwanted cable (no port with its link up other than
the Ports paired with a UNI, the management port (EMP), and its PermanentCables), no Missing
cable (a Port whose link is down, api.missing_cables: only once BLAB_MISSING_CABLE_MODE is
'enforce'; a warning until then, and always on a reserved Switch) and no Switch account left
(a local user in show user that BLab created and hasn't removed), and it is in its clean
state (api.clean_state): no Unwanted file, no local user but admin and the lab managers', no
SU allowed but admin's, and an init that holds its Golden init. Until
BLAB_CLEAN_STATE_MODE is 'enforce', the clean state is only warnings, and so is a config
that differs from init. Whatever an Inspection can't read makes it fail with an
"unreadable: ..." reason: an Inspection never passes on something it didn't see.

It only reads the Switch. The one thing it changes is on the backbone: to look for Missing
cables on a Switch nobody holds, it enables some disabled UNIs for a few seconds, and
disables them again (api.missing_cables).
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from . import clean_state, missing_cables
from .lab_switch import (INIT_CONFIG_PATH, RELEASE_COMMAND, LabSwitchError, LoginRefused, Readings, lab_switch,
                         parse_microcode, port_order)
from .models import NO_MANAGEMENT_IP, Reservation, Switch, SwitchAccount, SwitchEvent

logger = logging.getLogger(__name__)

# "Local Chassis ID 1 (Master)", "Remote Chassis ID 2 (Slave)"
CHASSIS_ID = re.compile(r'^\s*(?:Local|Remote)?\s*Chassis\s+ID\s+(\d+)', re.IGNORECASE | re.MULTILINE)
# Each port's block in show interfaces starts with "Chassis/Slot/Port 1/1/1    :" (AOS 8.9), or
# "Chassis/Slot/Port          : 1/1/1" (AOS 8.10.86, 10.69.144.132 on 2026-10-12)
PORT_HEADER = re.compile(r'^\s*(?:Chassis/)?Slot/Port[ \t]+(?:([^\s:]\S*?)[ \t]*:|:[ \t]*(\S+))',
                         re.IGNORECASE | re.MULTILINE)
OPERATIONAL_STATUS = re.compile(r'Operational Status\s*:\s*([A-Za-z]+)', re.IGNORECASE)
# Front-panel ports (chassis/slot/port, maybe a split port like 1/1/49A), and the management port.
# An up port that is neither is reported unreadable rather than skipped.
FRONT_PANEL_PORT = re.compile(r'\d+/\d+/\d+[A-Za-z]?')
MANAGEMENT_PORT = re.compile(r'EMP(-\w+)?', re.IGNORECASE)
CONFIG_DIFFERS = 'config differs from init'  # how the warning about a changed config starts
ACCOUNT_LEFT = 'Switch account left: '  # the reason an Inspection gives for Switch accounts left behind
UNREADABLE = 'unreadable: '  # how the reason for what an Inspection couldn't read starts
T = TypeVar('T')
SHOWN_DIFFERENCES = 5  # config lines quoted in the warning


@dataclass
class InspectionResult:
    reasons: List[str] = field(default_factory=list)   # why it isn't clean
    warnings: List[str] = field(default_factory=list)  # worth knowing, not a failure
    reached: bool = True  # False when BLab couldn't log in: a reloading Switch may yet come back
    config_differs: bool = False  # its config differs from init (a warning, not a failure)
    repairable: List[str] = field(default_factory=list)  # the reasons a Cleanup repairs (api.clean_state)
    aos_release: Optional[str] = None  # the release it runs (show microcode), if it said

    @property
    def clean(self) -> bool:
        return not self.reasons


def parse_chassis_ids(output: str) -> Optional[List[int]]:
    """The chassis IDs in show chassis, or None if it lists none (unreadable)."""
    ids = sorted({int(n) for n in CHASSIS_ID.findall(output or '')})
    return ids or None


def parse_link_states(output: str) -> Optional[Dict[str, str]]:
    """
    Each port's operational status ('up', 'down'...) in show interfaces, or None if it lists
    no port, or a port without its status (unreadable).
    """
    headers = list(PORT_HEADER.finditer(output or ''))
    if not headers:
        return None
    states = {}
    for header, following in zip(headers, headers[1:] + [None]):
        block = output[header.end():following.start() if following else len(output)]
        status = OPERATIONAL_STATUS.search(block)
        if not status:
            return None
        states[header.group(1) or header.group(2)] = status.group(1).lower()
    return states


def parse_user_names(output: str) -> Optional[List[str]]:
    """The local user names in show user, or None if it lists none (unreadable: admin is always there)."""
    users = clean_state.parse_users(output)
    return list(users) if users else None


def config_differences(running: str, init: str) -> Tuple[List[str], List[str]]:
    """The config lines only in running (added) and only in init (removed), comments aside."""
    running_lines, init_lines = clean_state.config_lines(running), clean_state.config_lines(init)
    init_set, running_set = set(init_lines), set(running_lines)
    return ([line for line in running_lines if line not in init_set],
            [line for line in init_lines if line not in running_set])


def inspect(switch: Switch) -> InspectionResult:
    """
    Inspects one Switch. Never writes to it, nor to the database; enables some UNIs for a few
    seconds to look for Missing cables, and disables them again.
    """
    result = InspectionResult()
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        result.reasons.append('unreachable: no management IP')
        return result
    try:
        readings = lab_switch(switch.mngt_IP).read_for_inspection()
    except LoginRefused:
        result.reasons.append('login refused')
        result.reached = False
        return result
    except LabSwitchError as e:
        result.reasons.append(f'unreachable: {e}')
        result.reached = False
        return result

    def output(cmd: str) -> Optional[str]:
        """The command's output, or None (with an unreadable reason) if it failed."""
        answer = readings.outputs[cmd]
        if answer.status != 0:
            logger.warning("Inspection of %s: '%s' returned %s: %s", switch.mngt_IP, cmd, answer.status,
                           answer.error.strip())
            result.reasons.append(UNREADABLE + cmd)
            return None
        return answer.output

    def parsed(cmd: str, parse: Callable[[str], Optional[T]]) -> Optional[T]:
        """The command's output parsed, or None (with an unreadable reason) if it failed or can't be parsed."""
        text = output(cmd)
        if text is None:
            return None
        value = parse(text)
        if value is None:
            result.reasons.append(UNREADABLE + cmd)
        return value

    microcode = readings.outputs.get(RELEASE_COMMAND)
    found = parse_microcode(microcode.output) if microcode is not None and microcode.status == 0 else None
    result.aos_release = found.release if found else None

    chassis = parsed('show chassis', parse_chassis_ids)
    if chassis is not None and len(chassis) > 1:
        result.reasons.append(f"in a VC: chassis {', '.join(map(str, chassis))}")

    reserved = Reservation.objects.filter(switch=switch).exists()
    states = parsed('show interfaces', parse_link_states)
    if states is not None and not reserved:
        unwanted, unknown = unwanted_cables(switch, states)
        result.reasons.extend(f'{UNREADABLE}show interfaces port {port}' for port in unknown)
        if unwanted:
            result.reasons.append(f"Unwanted cable: {', '.join(unwanted)}")
    if states is not None:
        judge_missing_cables(switch, states, reserved, result)

    if not reserved:
        users = parsed('show user', parse_user_names)
        ours = set(SwitchAccount.objects.filter(switch=switch).values_list('name', flat=True))
        left = [name for name in users or () if name in ours]
        if left:
            result.reasons.append(ACCOUNT_LEFT + ', '.join(left))
        judge_clean_state(switch, readings, result, ours)
    else:
        # Its users work on it: only SU allowed is worth knowing (it opens a root shell)
        su = clean_state_findings(switch, readings).su
        if su:
            result.warnings.append(clean_state.SU_ALLOWED_FOR + clean_state.shown(su))

    running = output('show configuration snapshot')
    if readings.init_config is None:
        result.reasons.append(UNREADABLE + INIT_CONFIG_PATH)
    if running is not None and readings.init_config is not None:
        added, removed = config_differences(running, readings.init_config)
        if added or removed:
            result.config_differs = True
            shown = [f'+ {line}' for line in added] + [f'- {line}' for line in removed]
            more = len(shown) - SHOWN_DIFFERENCES
            result.warnings.append(
                f"{CONFIG_DIFFERS}: {len(added)} line(s) added, {len(removed)} removed: "
                + '; '.join(shown[:SHOWN_DIFFERENCES]) + (f'; and {more} more' if more > 0 else ''))
    return result


def judge_missing_cables(switch: Switch, states: Dict[str, str], reserved: bool, result: InspectionResult) -> None:
    """
    Adds the Missing cables (api.missing_cables) to the result: as a reason on a Switch nobody
    holds once BLAB_MISSING_CABLE_MODE is 'enforce', and otherwise as a warning (on a reserved
    Switch they are only shown).
    """
    def read_again() -> Optional[Dict[str, str]]:
        try:
            answer = lab_switch(switch.mngt_IP).show('show interfaces')
        except LabSwitchError as e:
            logger.warning("Missing cables on %s: show interfaces failed: %s", switch.mngt_IP, e)
            return None
        return parse_link_states(answer.output) if answer.status == 0 else None

    found = missing_cables.find(switch, states, reserved, read_again)
    result.warnings.extend(found.warnings)
    fails = not reserved and missing_cables.mode() == 'enforce'
    lines = []
    if found.unreadable:
        lines.append(f"{UNREADABLE}{', '.join(found.unreadable)} (looking for Missing cables)")
    if found.missing:
        lines.append(missing_cables.MISSING_CABLE + ', '.join(map(missing_cables.describe, found.missing)))
    if fails:
        result.reasons.extend(lines)
    elif reserved:
        result.warnings.extend(lines)
    else:
        result.warnings.extend(line.replace(missing_cables.MISSING_CABLE, missing_cables.REPORTED_PREFIX, 1)
                               for line in lines)


def clean_state_findings(switch: Switch, readings: Readings) -> clean_state.Findings:
    """Where a Switch strays from its clean state, judged on what an Inspection read. Only reads the database."""
    answer = readings.outputs['show user']
    users = clean_state.parse_users(answer.output) if answer.status == 0 else None
    return clean_state.find(switch.model, readings.listings, users, readings.init_config, clean_state.extras())


def judge_clean_state(switch: Switch, readings: Readings, result: InspectionResult, accounts: set) -> None:
    """
    Adds to the result where the Switch strays from its clean state: as reasons once
    BLAB_CLEAN_STATE_MODE is 'enforce', as warnings until then. Switch accounts left are
    already a reason of their own; unreadable show user and init config are too.
    """
    found = clean_state_findings(switch, readings)
    found.logins = [name for name in found.logins if name not in accounts]
    found.unreadable = [what for what in found.unreadable if what != 'show user']
    lines = clean_state.describe(found)
    if not clean_state.enforcing():
        result.warnings.extend(lines)
        return
    result.reasons.extend(lines)
    result.repairable.extend(line for line in lines if line.startswith(clean_state.REPAIRED_BY_CLEANUP))


def unwanted_cables(switch: Switch, states: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    The ports whose link is up and that would be Unwanted cables once the Switch is not
    reserved, in port order; and the up ports it can't judge (neither front panel nor EMP).
    """
    up = [port for port, state in states.items() if state == 'up' and not MANAGEMENT_PORT.fullmatch(port)]
    unknown = [port for port in up if not FRONT_PANEL_PORT.fullmatch(port)]
    expected = switch.ports_cabled_on_purpose()
    unwanted = [port for port in up if port not in unknown and port not in expected]
    return sorted(unwanted, key=port_order), unknown


def cables_left(switch: Switch) -> List[str]:
    """
    The ports to unplug before a Release: those that would be Unwanted cables if the Switch
    were released now, then the up ports an Inspection can't judge (they would fail it too).
    Only reads show interfaces. Raises LabSwitchError if it can't tell.
    """
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        raise LabSwitchError('no management IP')
    answer = lab_switch(switch.mngt_IP).show('show interfaces')
    states = parse_link_states(answer.output) if answer.status == 0 else None
    if states is None:
        raise LabSwitchError(f"cannot read show interfaces on {switch.mngt_IP}")
    unwanted, unknown = unwanted_cables(switch, states)
    return unwanted + unknown


def inspect_and_record(switch: Switch, result: Optional[InspectionResult] = None, user=None) -> SwitchEvent:
    """
    Inspects one Switch (unless given the result of an Inspection just made) and adds the
    result to its history, for `user` when someone asked for it. Records the AOS release it
    runs, when it said.
    """
    if result is None:
        result = inspect(switch)
    if result.aos_release and result.aos_release != switch.aos_release:
        switch.aos_release = result.aos_release
        Switch.everything.filter(id=switch.id).update(aos_release=result.aos_release)
    event = SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.INSPECTION, ok=result.clean,
                                       reasons=result.reasons, warnings=result.warnings, user=user)
    log = logger.info if result.clean else logger.warning
    log("Inspection of %s: %s", switch.mngt_IP, 'clean' if result.clean else '; '.join(result.reasons))
    return event
