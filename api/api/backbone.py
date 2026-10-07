"""
The backbone, seen as the Services it carries (see CONTEXT.md).

Backbone turns Service operations into CLI commands and reads them back from the
configuration snapshot. The commands go through a transport, picked in one place
(backbone()): the real switches over HTTPS, or the in-memory fake in fake_devices.
"""
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set, Tuple

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning  # type: ignore

logger = logging.getLogger(__name__)

# The lab switches use self-signed certificates.
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

SWITCH_USERNAME = "admin"
SWITCH_PASSWORD = "switch"
AOS_JSON = {'Accept': 'application/vnd.alcatellucentaos+json; version=1.0'}


class APIRequestError(Exception):
    """A command could not be run on a device."""
    def __init__(self, message: str = "API request failed"):
        self.message = message
        super().__init__(self.message)


Transport = Callable[[str, str], str]  # (ip, command) -> output
SNAPSHOT_HEADERS = {"vlan": "! VLAN:", "interface": "! Interface:"}  # each section's first line


@dataclass(frozen=True)
class Service:
    """What a backbone holds for one SVLAN. Parts may be missing if a change stopped halfway."""
    svlan: int
    svlan_configured: bool = False
    nni: bool = False  # bound to a trunk between backbones, by hand: never removed here
    name: Optional[str] = None
    sap: bool = False
    unis: tuple = ()
    cvlan_all: bool = False

    def is_complete(self, name: str, unis) -> bool:
        return self.name == name and self.carries(unis)

    def carries(self, unis) -> bool:
        """Built in full for exactly these UNIs, whatever its name (older Links are named after users)."""
        return (self.svlan_configured and self.name is not None and self.sap
                and self.cvlan_all and set(self.unis) == set(unis))

    def is_removed(self) -> bool:
        """Nothing of a Link is left: at most the SVLAN itself, kept for its trunk."""
        return self.is_bare_svlan() and (self.nni or not self.svlan_configured)

    def is_bare_svlan(self) -> bool:
        """At most the SVLAN itself is configured: no part of a Service."""
        return not (self.name or self.sap or self.unis or self.cvlan_all)

    def can_carry(self, name: str, unis) -> bool:
        """A Service of that name for these UNIs can be built here without touching anyone else's."""
        return self.name in (None, name) and set(self.unis) <= set(unis)


class Backbone:
    """One backbone, reached at its IP."""
    def __init__(self, ip: str, cli: Transport, settle_delay: float = 0.0):
        self.ip = ip
        self._cli = cli
        self.settle_delay = settle_delay  # between reads: a real backbone takes a moment to show a change

    def cli(self, cmd: str) -> str:
        return self._cli(self.ip, cmd)

    def configure_service(self, svlan: int, name: str, unis, existing: Optional[Service] = None) -> None:
        """Builds the Service, skipping the parts an existing one already has."""
        existing = existing or Service(svlan)
        if not existing.svlan_configured:
            self.cli(f"ethernet-service svlan {svlan} admin-state enable")
        if existing.name is None:
            self.cli(f"ethernet-service service-name {name} svlan {svlan}")
        if not existing.sap:
            self.cli(f"ethernet-service sap {svlan} service-name {name}")
        for uni in unis:
            if uni not in existing.unis:
                self.cli(f"ethernet-service sap {svlan} uni port {uni}")
        if not existing.cvlan_all:
            self.cli(f"ethernet-service sap {svlan} cvlan all")

    def remove_service(self, svlan: int) -> None:
        """
        Removes whatever the backbone holds for this SVLAN, and nothing if it holds nothing.
        The service name is read from the device, since it may have been named by anyone.
        """
        service = self.read_service(svlan)
        if service is None:
            return
        for uni in service.unis:
            self.cli(f"no ethernet-service sap {svlan} uni port {uni}")
        if service.sap:
            self.cli(f"no ethernet-service sap {svlan}")
        if service.name is not None:
            self.cli(f"no ethernet-service service-name {service.name} svlan {svlan}")
        if service.svlan_configured and not service.nni:
            self.cli(f"no ethernet-service svlan {svlan}")

    def unbuild_service(self, svlan: int, name: str, unis, keep_svlan: bool) -> None:
        """
        Takes back what configure_service(svlan, name, unis) built, and nothing else: a Service
        under another name is left alone, and so are the SAP and name while other UNIs remain.
        """
        service = self.read_service(svlan)
        if service is None or service.name not in (None, name):
            return
        for uni in unis:
            if uni in service.unis:
                self.cli(f"no ethernet-service sap {svlan} uni port {uni}")
        if set(service.unis) - set(unis):
            return
        if service.sap:
            self.cli(f"no ethernet-service sap {svlan}")
        if service.name is not None:
            self.cli(f"no ethernet-service service-name {name} svlan {svlan}")
        if service.svlan_configured and not service.nni and not keep_svlan:
            self.cli(f"no ethernet-service svlan {svlan}")

    def read_service(self, svlan: int) -> Optional[Service]:
        return parse_service(self.snapshot("vlan"), svlan)

    def read_services(self) -> Tuple[Dict[int, Service], List[str]]:
        """Every Service on the backbone, and the ethernet-service lines that couldn't be read."""
        return parse_services(self.snapshot("vlan"))

    def disabled_unis(self) -> Set[str]:
        """The ports whose admin state is disabled; any other port is enabled, the default."""
        return parse_disabled_ports(self.snapshot("interface"))

    def snapshot(self, section: str) -> str:
        """
        One section of the configuration snapshot. Without its header, the answer is not the
        section (an empty one would read as a backbone holding nothing): that is a failure.
        """
        header = SNAPSHOT_HEADERS[section]
        output = self.cli(f"show configuration snapshot {section}")
        if header not in output:
            raise APIRequestError(f"Backbone {self.ip} answered 'show configuration snapshot {section}' "
                                  f"without its '{header}' section: {output[:80]!r}")
        return output

    def wait_for_service(self, svlan: int, check: Callable[[Optional[Service]], bool],
                         within: float = 4.0) -> bool:
        """
        Reads the service every settle_delay until check() accepts it, for about `within`
        seconds: short reads return as soon as the backbone shows the change.
        """
        attempts = int(within / self.settle_delay) + 1 if self.settle_delay else 3
        for attempt in range(attempts):
            if check(self.read_service(svlan)):
                return True
            if attempt < attempts - 1:
                time.sleep(self.settle_delay)
        return False

    def set_uni_admin_state(self, uni: str, enabled: bool) -> None:
        try:
            self.cli(f"interfaces {uni} admin-state {'enable' if enabled else 'disable'}")
        except APIRequestError:
            # The backbone may refuse a change that is already made: what counts is the state it is in
            if (uni not in self.disabled_unis()) != enabled:
                raise


def parse_service(snapshot: str, svlan: int) -> Optional[Service]:
    """Reads the ethernet-service lines for one SVLAN out of 'show configuration snapshot vlan'."""
    return parse_services(snapshot)[0].get(svlan)


def parse_services(snapshot: str) -> Tuple[Dict[int, Service], List[str]]:
    """
    Every Service in 'show configuration snapshot vlan', by SVLAN, and the ethernet-service
    lines this parser doesn't understand (so that they get reported rather than ignored).
    """
    fields: Dict[int, dict] = {}
    unreadable = []
    for line in snapshot.splitlines():
        words = [w.strip('"') for w in line.split()]
        if words[:1] != ["ethernet-service"]:
            continue
        rest = words[1:]
        svlans, field = _service_field(rest)
        if not svlans:
            unreadable.append(line.strip())
        for svlan in svlans:
            service = fields.setdefault(svlan, {"svlan": svlan, "unis": []})
            if field == "unis":
                service["unis"].extend(port for word in rest[4:] for port in expand_port_range(word))
            elif field == "name":
                service["name"] = rest[1]
            else:
                service[field] = True
                if field == "svlan_configured" and rest[2:3] == ["nni"]:
                    service["nni"] = True
    services = {svlan: Service(**dict(f, unis=tuple(f["unis"]))) for svlan, f in fields.items()}
    return services, unreadable


SAP_FIELDS = {("service-name",): "sap", ("uni", "port"): "unis", ("cvlan", "all"): "cvlan_all"}


def _service_field(rest: List[str]) -> Tuple[range, Optional[str]]:
    """Which SVLANs the words after 'ethernet-service' are about, and the Service field they set."""
    def word(i: int) -> str:
        return rest[i] if len(rest) > i else ""

    # The device folds svlan lines into ranges ("svlan 1001-1003"); the other lines name one SVLAN
    if word(0) == "svlan":
        return expand_svlan_range(word(1)), "svlan_configured"
    if word(0) == "service-name" and word(2) == "svlan":
        return expand_svlan_range(word(3)), "name"
    if word(0) == "sap":
        for keys, field in SAP_FIELDS.items():
            if tuple(rest[2:2 + len(keys)]) == keys:
                return expand_svlan_range(word(1)), field
    return range(0), None


def parse_disabled_ports(snapshot: str) -> Set[str]:
    """Ports in 'interfaces port 1/1/2 admin-state disable' lines of 'show configuration snapshot interface'."""
    disabled = set()
    for line in snapshot.splitlines():
        words = line.split()
        if words[:2] == ["interfaces", "port"]:
            words = words[:1] + words[2:]  # the device prints "port"; commands may leave it out
        if words[:1] == ["interfaces"] and words[2:4] == ["admin-state", "disable"]:
            disabled.update(expand_port_range(words[1]))
    return disabled


def expand_svlan_range(svlan_range: str) -> range:
    """'1001-1003' -> 1001, 1002, 1003; a single SVLAN is a range of one. Anything else is empty."""
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", svlan_range)
    if not match:
        return range(0)
    first = int(match.group(1))
    return range(first, int(match.group(2) or first) + 1)


def expand_port_range(port_range: str) -> list:
    """'1/1/1-3' -> ['1/1/1', '1/1/2', '1/1/3']; a single port is returned as is."""
    match = re.fullmatch(r"(.*/)(\d+)-(\d+)", port_range)
    if not match:
        return [port_range]
    prefix, start, end = match.groups()
    return [f"{prefix}{port}" for port in range(int(start), int(end) + 1)]


COOKIE_CACHE = {}  # Dictionary to store cookies per switch IP
_sessions = threading.local()  # per thread: ip -> requests.Session


def _session(ip: str) -> requests.Session:
    """A kept-alive connection: about a third faster per command than opening a new one each time."""
    if not hasattr(_sessions, 'by_ip'):
        _sessions.by_ip = {}
    return _sessions.by_ip.setdefault(ip, requests.Session())


def get_cookie(ip: str, retries: int = 3, delay: float = 1.0) -> str:
    """Authenticates on a device and caches its session cookie. Raises APIRequestError."""
    auth_url = f"https://{ip}?domain=auth&username={SWITCH_USERNAME}&password={SWITCH_PASSWORD}"
    headers = dict(AOS_JSON)
    # An expired session's answer carries a new session cookie, which the kept-alive connection
    # keeps: logging in with it sets no cookie. Without one, the login hands out a fresh session.
    _session(ip).cookies.clear()

    for attempt in range(retries):
        try:
            response = _session(ip).get(auth_url, headers=headers, verify=False, timeout=5)
            response.raise_for_status()

            set_cookie = response.headers.get('Set-Cookie')
            if set_cookie:
                cookie_pair = set_cookie.split(';')[0]
                if '=' in cookie_pair:
                    _, cookie_value = cookie_pair.split('=', 1)
                    COOKIE_CACHE[ip] = cookie_value
                    logger.info(f"Authenticated on {ip}; cookie obtained.")
                    return cookie_value
            logger.warning(f"Authentication on {ip} did not return a cookie.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt+1}/{retries}: Authentication failed for {ip}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise APIRequestError(f"Authentication failed for {ip}: {e}")
    raise APIRequestError(f"Authentication failed for {ip} after {retries} attempts.")


def https_cli(ip: str, cmd: str, retries: int = 3, delay: float = 1.0) -> str:
    """Runs a CLI command on a real device through its HTTPS API. Raises APIRequestError."""
    headers = dict(AOS_JSON)

    if ip not in COOKIE_CACHE:
        COOKIE_CACHE[ip] = get_cookie(ip)
    headers['Cookie'] = f"wv_sess={COOKIE_CACHE[ip]}"

    logged_in_again = False
    for attempt in range(retries):
        url = "https://{}?domain=cli&cmd={}".format(ip, cmd)
        try:
            response = _session(ip).get(url, headers=headers, data={}, verify=False, timeout=5)
            if response.status_code != 200:
                try:
                    error_message = response.json().get("error", response.text)
                except ValueError:
                    error_message = response.text

                logger.error(f"Request to {ip} failed with status {response.status_code}: {error_message}")
                raise APIRequestError(f"Request to {ip} failed with status {response.status_code}: {error_message}")

            data = response.json()
            result = data.get("result", {})
            # The backbone answers 200 even when the command failed: the outcome is in diag and error.
            # An idle session expires after a few minutes and then answers diag 401, with no output,
            # either "no such session - expired?" or "You must login first". The command never ran,
            # so it is sent again once logged in, but only once: a second refusal is a failure.
            if result.get("diag") == 401 or result.get("error") == "You must login first":
                if logged_in_again:
                    raise APIRequestError(f"'{cmd}' refused on {ip} after logging in again: {result.get('error')}")
                logger.info(f"Session expired on {ip} ({result.get('error')}), re-authenticating.")
                COOKIE_CACHE[ip] = get_cookie(ip)
                headers['Cookie'] = f"wv_sess={COOKIE_CACHE[ip]}"
                logged_in_again = True
                continue
            if result.get("diag", 200) != 200 or result.get("error"):
                raise APIRequestError(f"'{cmd}' failed on {ip} (diag {result.get('diag')}): {result.get('error')}")

            output = result.get("output")
            if output is None:
                raise APIRequestError("Unexpected response format: 'output' missing.")
            return output

        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt+1}/{retries}: Request to {ip} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise APIRequestError(f"Request to {ip} failed: {e}")

    raise APIRequestError(f"CLI command failed on {ip} after {retries} attempts.")


def backbone(ip: str) -> Backbone:
    """The one place that decides whether commands reach real devices."""
    from . import fake_devices  # fake_devices needs APIRequestError from here
    if fake_devices.devices_are_fake():
        return Backbone(ip, fake_devices.backbone.cli)
    return Backbone(ip, https_cli, settle_delay=0.5)
