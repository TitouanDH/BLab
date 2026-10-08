"""
Inspection (see CONTEXT.md): reading a Switch to decide whether it is clean. It only reads:
show commands and the init config file, through LabSwitch.read_for_inspection.

A Switch is clean when BLab can log in to it, it stands alone (one chassis in show chassis),
and, when it is not reserved, it has no Unwanted cable: no port with its link up other than
the Ports paired with a UNI, the management port (EMP), and its PermanentCables. A config
that differs from init is only a warning. Whatever an Inspection can't read makes it fail
with an "unreadable: ..." reason: an Inspection never passes on something it didn't see.
Checking that no Switch account is left comes with Switch accounts themselves (#21).
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from .lab_switch import INIT_CONFIG_PATH, LabSwitchError, LoginRefused, lab_switch
from .models import NO_MANAGEMENT_IP, Reservation, Switch, SwitchEvent

logger = logging.getLogger(__name__)

# "Local Chassis ID 1 (Master)", "Remote Chassis ID 2 (Slave)"
CHASSIS_ID = re.compile(r'^\s*(?:Local|Remote)?\s*Chassis\s+ID\s+(\d+)', re.IGNORECASE | re.MULTILINE)
# Each port's block in show interfaces starts with "Chassis/Slot/Port 1/1/1    :"
PORT_HEADER = re.compile(r'^\s*(?:Chassis/)?Slot/Port\s+(\S+?)\s*:', re.IGNORECASE | re.MULTILINE)
OPERATIONAL_STATUS = re.compile(r'Operational Status\s*:\s*([A-Za-z]+)', re.IGNORECASE)
# Front-panel ports (chassis/slot/port, maybe a split port like 1/1/49A), and the management port.
# An up port that is neither is reported unreadable rather than skipped.
FRONT_PANEL_PORT = re.compile(r'\d+/\d+/\d+[A-Za-z]?')
MANAGEMENT_PORT = re.compile(r'EMP(-\w+)?', re.IGNORECASE)
T = TypeVar('T')
SHOWN_DIFFERENCES = 5  # config lines quoted in the warning


@dataclass
class InspectionResult:
    reasons: List[str] = field(default_factory=list)   # why it isn't clean
    warnings: List[str] = field(default_factory=list)  # worth knowing, not a failure
    reached: bool = True  # False when BLab couldn't log in: a reloading Switch may yet come back

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
        states[header.group(1)] = status.group(1).lower()
    return states


def _config_lines(text: str) -> List[str]:
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith('!')]


def config_differences(running: str, init: str) -> Tuple[List[str], List[str]]:
    """The config lines only in running (added) and only in init (removed), comments aside."""
    running_lines, init_lines = _config_lines(running), _config_lines(init)
    init_set, running_set = set(init_lines), set(running_lines)
    return ([line for line in running_lines if line not in init_set],
            [line for line in init_lines if line not in running_set])


def inspect(switch: Switch) -> InspectionResult:
    """Inspects one Switch. Never writes to it, nor to the database."""
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
            result.reasons.append(f'unreadable: {cmd}')
            return None
        return answer.output

    def parsed(cmd: str, parse: Callable[[str], Optional[T]]) -> Optional[T]:
        """The command's output parsed, or None (with an unreadable reason) if it failed or can't be parsed."""
        text = output(cmd)
        if text is None:
            return None
        value = parse(text)
        if value is None:
            result.reasons.append(f'unreadable: {cmd}')
        return value

    chassis = parsed('show chassis', parse_chassis_ids)
    if chassis is not None and len(chassis) > 1:
        result.reasons.append(f"in a VC: chassis {', '.join(map(str, chassis))}")

    states = parsed('show interfaces', parse_link_states)
    if states is not None and not Reservation.objects.filter(switch=switch).exists():
        unwanted, unknown = unwanted_cables(switch, states)
        result.reasons.extend(f'unreadable: show interfaces port {port}' for port in unknown)
        if unwanted:
            result.reasons.append(f"Unwanted cable: {', '.join(unwanted)}")

    running = output('show configuration snapshot')
    if readings.init_config is None:
        result.reasons.append(f'unreadable: {INIT_CONFIG_PATH}')
    if running is not None and readings.init_config is not None:
        added, removed = config_differences(running, readings.init_config)
        if added or removed:
            shown = [f'+ {line}' for line in added] + [f'- {line}' for line in removed]
            more = len(shown) - SHOWN_DIFFERENCES
            result.warnings.append(
                f"config differs from init: {len(added)} line(s) added, {len(removed)} removed: "
                + '; '.join(shown[:SHOWN_DIFFERENCES]) + (f'; and {more} more' if more > 0 else ''))
    return result


def unwanted_cables(switch: Switch, states: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """
    The ports whose link is up and that would be Unwanted cables once the Switch is not
    reserved, in port order; and the up ports it can't judge (neither front panel nor EMP).
    """
    up = [port for port, state in states.items() if state == 'up' and not MANAGEMENT_PORT.fullmatch(port)]
    unknown = [port for port in up if not FRONT_PANEL_PORT.fullmatch(port)]
    expected = switch.ports_cabled_on_purpose()
    unwanted = [port for port in up if port not in unknown and port not in expected]
    return sorted(unwanted, key=_port_order), unknown


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


def _port_order(port: str):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', port)]


def inspect_and_record(switch: Switch, result: Optional[InspectionResult] = None, user=None) -> SwitchEvent:
    """
    Inspects one Switch (unless given the result of an Inspection just made) and adds the
    result to its history, for `user` when someone asked for it.
    """
    if result is None:
        result = inspect(switch)
    event = SwitchEvent.objects.create(switch=switch, kind=SwitchEvent.INSPECTION, ok=result.clean,
                                       reasons=result.reasons, warnings=result.warnings, user=user)
    log = logger.info if result.clean else logger.warning
    log("Inspection of %s: %s", switch.mngt_IP, 'clean' if result.clean else '; '.join(result.reasons))
    return event
