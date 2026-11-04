"""
Restore onto replacement Switches (#46, CONTEXT.md: Restore): a Switch of the Snapshot that is
busy can be replaced by a free Switch the member picks, which api.restore then Restores as it
would the Snapshot's.

candidates() offers the free Switches with at least as many cabled ports as the Snapshot's Links
need on the busy one, ranked same model, then same family (api.aos_release.family), then others;
never one that runs the other AOS (AOS 6 and AOS 8 configs don't translate), and none for an AOS 6
Switch yet: its ports are slot/port, which the config rewrite doesn't know (#108).
prepare() builds the port map and rewrites the config for the pick:

- **Port map.** The ports the Snapshot's Links use on the busy Switch, in port order, take the
  replacement's cabled Ports (those not carrying a Link), in port order. BLab doesn't know the
  speed of the busy Switch's ports, so port order is all it goes by. Any other port the config
  names keeps its name if the replacement has such a port (show interfaces capability) and no
  Link port took it; otherwise it is left out of the config, and the diff says so.
- **Config.** Every port reference is rewritten (`1/1/1`, ranges `1/1/1-4` and `1/1/1-1/1/4`,
  split ports `1/1/49A`, SAPs `1/1/8:100`), outside quotes and `!` comments. A range whose ports
  no longer follow each other becomes one line per run; a line whose ports are all left out is
  left out. The model in `system name` and `session prompt default` (the Golden init names a
  Switch after its model) becomes the replacement's.
- **Links**: rewrite_links() moves each end on the busy Switch to the replacement's port.

The rewritten config is shown as a diff (preview()) before the member confirms.
"""
import difflib
import itertools
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import aos_release
from .clean_state import runs_aos6
from .lab_switch import LabSwitchError, lab_switch, port_order
from .models import NO_MANAGEMENT_IP, Port, Switch, SnapshotSwitch

SAME_MODEL, SAME_FAMILY, OTHER = 'same_model', 'same_family', 'other'
RANKS = (SAME_MODEL, SAME_FAMILY, OTHER)

# A port reference: chassis/slot/port, a split port's letter, then maybe a range ("-4", "-4D" or
# "-1/1/4"); a SAP's ":<vlan>" may follow. Not part of a longer word, path or address.
PORT_REF = re.compile(r'(?<![\w./:-])(\d+)/(\d+)/(\d+)([A-Za-z]?)(?:-(?:(\d+)/(\d+)/)?(\d+)([A-Za-z]?))?(?![\w/.-])')
QUOTED = re.compile(r'("[^"]*")')
NAMED_AFTER_MODEL = ('system name ', 'session prompt default ')


class ReplacementError(Exception):
    """The Switch can't stand in for the Snapshot's: why, in plain words."""


def link_ports(links: Iterable, switch_id: int) -> List[str]:
    """The ports the Snapshot's Links use on the Switch, in port order."""
    return sorted({end['port'] for ends in links for end in ends if end['switch'] == switch_id}, key=port_order)


def free_ports(switch_id: int) -> List[str]:
    """A Switch's cabled Ports that carry no Link, in port order."""
    return sorted(Port.objects.filter(switch_id=switch_id, svlan__isnull=True).values_list('port_switch', flat=True),
                  key=port_order)


def rank(old_model: str, new_model: str) -> str:
    if old_model == new_model:
        return SAME_MODEL
    family = aos_release.family(old_model)
    return SAME_FAMILY if family and family == aos_release.family(new_model) else OTHER


def candidates(entry: SnapshotSwitch, needed: int, free: Sequence[Switch]) -> List[Dict]:
    """
    The Switches among `free` that can stand in for the Snapshot's: those with at least `needed`
    cabled ports free, ranked same model, then same family, then others (then by address). AOS 6
    Switches are left out on both sides (#108): see the module's docstring.
    """
    cabled = {}
    for switch_id in Port.objects.filter(switch__in=free, svlan__isnull=True).values_list('switch_id', flat=True):
        cabled[switch_id] = cabled.get(switch_id, 0) + 1
    found = [{'id': s.id, 'model': s.model, 'mngt_IP': s.mngt_IP, 'rank': rank(entry.model, s.model),
              'cabled': cabled.get(s.id, 0)}
             for s in free if s.mngt_IP != NO_MANAGEMENT_IP and cabled.get(s.id, 0) >= needed
             and not runs_aos6(s.model) and not runs_aos6(entry.model)]
    return sorted(found, key=lambda c: (RANKS.index(c['rank']), port_order(c['mngt_IP']), c['id']))


def port_map(needed: Sequence[str], cabled: Sequence[str]) -> Dict[str, str]:
    """The Links' ports, in port order, onto the replacement's cabled ports, in port order."""
    if len(cabled) < len(needed):
        raise ReplacementError(f"it has {len(cabled)} cabled port{'s' if len(cabled) != 1 else ''} free, "
                               f"and the Snapshot's Links need {len(needed)}")
    return dict(zip(sorted(needed, key=port_order), sorted(cabled, key=port_order)))


@dataclass
class Rewrite:
    """A config rewritten for the replacement, and what to know about it."""
    text: str
    notes: List[str] = field(default_factory=list)


def _expand(m: re.Match) -> Optional[List[str]]:
    """The ports a reference names, or None if BLab can't tell (a range across slots, or odd letters)."""
    chassis, slot, first, letter, end_chassis, end_slot, last, last_letter = m.groups()
    start = f'{chassis}/{slot}/{first}{letter}'
    if last is None:
        return [start]
    if end_chassis is not None and (end_chassis, end_slot) != (chassis, slot):
        return None
    if not letter and not last_letter:
        if int(last) < int(first):
            return None
        return [f'{chassis}/{slot}/{n}' for n in range(int(first), int(last) + 1)]
    if letter and last_letter and first == last and letter.upper() <= last_letter.upper():
        return [f'{chassis}/{slot}/{first}{chr(c)}' for c in range(ord(letter.upper()), ord(last_letter.upper()) + 1)]
    return None


def _runs(ports: Iterable[str]) -> List[str]:
    """Ports as AOS writes them: consecutive ones of a slot as a range ('1/1/1-4'), in port order."""
    runs: List[Tuple[str, int, int]] = []  # (chassis/slot, first, last)
    singles = []
    for port in sorted(set(ports), key=port_order):
        head, _, number = port.rpartition('/')
        if not number.isdigit():
            singles.append(port)
            continue
        if runs and runs[-1][0] == head and runs[-1][2] + 1 == int(number):
            runs[-1] = (head, runs[-1][1], int(number))
        else:
            runs.append((head, int(number), int(number)))
    named = [f'{head}/{a}' if a == b else f'{head}/{a}-{b}' for head, a, b in runs]
    return sorted(named + singles, key=port_order)


def _rewrite_line(line: str, new_name: Callable[[str], Optional[str]], unreadable: List[str]) -> List[str]:
    """
    The line with its port references (outside quotes) rewritten through new_name: one line per
    run of ports a reference turns into, none if a reference keeps no port.
    """
    pieces, choices = [''], []  # the line split around its references: [text, ref, text, ..., text]
    for part in QUOTED.split(line):
        if part.startswith('"'):
            pieces[-1] += part
            continue
        at = 0
        for m in PORT_REF.finditer(part):
            pieces[-1] += part[at:m.start()]
            named = _expand(m)
            if named is None:
                unreadable.append(m.group(0))
                choices.append([])
            else:
                mapped = [n for n in map(new_name, named) if n]
                choices.append([m.group(0)] if mapped == named else _runs(mapped))  # unchanged: as written
            pieces.append('')
            at = m.end()
        pieces[-1] += part[at:]
    if not all(choices):
        return []
    return [''.join(piece + port for piece, port in zip(pieces, picked)) + pieces[-1]
            for picked in itertools.product(*choices)]


def rewrite_config(text: Optional[str], ports: Dict[str, str], exists: Callable[[str], bool],
                   old_model: str = '', new_model: str = '') -> Rewrite:
    """
    The config with every port reference rewritten: a port of `ports` (the port map) to its new
    port, any other to itself if `exists` says the replacement has it and no Link port took it,
    else left out. The model in the system name and prompt becomes new_model.
    """
    if text is None:
        return Rewrite(None)
    taken = set(ports.values())
    dropped, taken_over, unreadable = set(), set(), []

    def new_name(port: str) -> Optional[str]:
        if port in ports:
            return ports[port]
        if port in taken:
            taken_over.add(port)
            return None
        if not exists(port):
            dropped.add(port)
            return None
        return port

    out, left_out = [], []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('!') or not PORT_REF.search(line):
            if old_model and new_model and old_model != new_model and stripped.startswith(NAMED_AFTER_MODEL):
                line = line.replace(old_model, new_model)
            out.append(line)
            continue
        lines = _rewrite_line(line, new_name, unreadable)
        out.extend(lines)
        if not lines:
            left_out.append(stripped)
    notes = []
    if dropped:
        notes.append(f"Not on the replacement, so left out of its config: {', '.join(_runs(dropped))}.")
    if taken_over:
        notes.append(f"Taken over by a Link's port, so left out of its config: {', '.join(_runs(taken_over))}.")
    if unreadable:
        notes.append(f"Port ranges BLab can't rewrite, so left out: {', '.join(unreadable)}.")
    if left_out:
        notes.append(f"{len(left_out)} line{'s' if len(left_out) != 1 else ''} left out: " + '; '.join(left_out) + '.')
    return Rewrite('\n'.join(out) + ('\n' if text.endswith('\n') else ''), notes)


def rewrite_links(links: Iterable, replaced: Dict[int, Tuple[int, Dict[str, str]]]) -> List[List[Dict]]:
    """The Snapshot's Links with each end on a replaced Switch moved to its replacement's port
    ({old switch id: (new switch id, port map)})."""
    def moved(end):
        if end['switch'] not in replaced:
            return end
        new_id, ports = replaced[end['switch']]
        return {'switch': new_id, 'port': ports.get(end['port'], end['port'])}
    return [[moved(end) for end in ends] for ends in links]


@dataclass
class Prepared:
    """What a replacement Switch gets: its port map and the Snapshot's config rewritten for it."""
    port_map: Dict[str, str]
    vcboot: Optional[str]
    vcsetup: Optional[str]
    notes: List[str]


def existing_ports(switch: Switch) -> Tuple[Callable[[str], bool], List[str]]:
    """Which ports the replacement has (show interfaces capability), and a note if BLab couldn't tell."""
    try:
        names = set(lab_switch(switch.mngt_IP).port_capabilities())
        return names.__contains__, []
    except LabSwitchError as e:
        return (lambda port: True), [f"BLab couldn't read the replacement's ports ({e}): every other port keeps its "
                                     f"name."]


def prepare(snapshot, entry: SnapshotSwitch, switch: Switch) -> Prepared:
    """The port map and config for `switch` standing in for the Snapshot's entry. ReplacementError if it can't."""
    if entry.vcboot is None:
        raise ReplacementError("the Snapshot has no config for the Switch it would replace")
    ports = port_map(link_ports(snapshot.links, entry.switch_id), free_ports(switch.id))
    exists, notes = existing_ports(switch)
    vcboot = rewrite_config(entry.vcboot, ports, exists, entry.model, switch.model)
    vcsetup = rewrite_config(entry.vcsetup, ports, exists, entry.model, switch.model)
    return Prepared(ports, vcboot.text, vcsetup.text, notes + vcboot.notes + [f'vcsetup.cfg: {n}' for n in vcsetup.notes])


def diff(old: Optional[str], new: Optional[str]) -> List[str]:
    """How the rewritten config differs from the Snapshot's, as a unified diff without its header."""
    lines = difflib.unified_diff((old or '').splitlines(), (new or '').splitlines(), lineterm='', n=1)
    return [line for line in lines if not line.startswith(('---', '+++'))]
