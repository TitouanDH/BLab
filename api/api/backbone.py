"""
The backbone, seen as the Services it carries (see CONTEXT.md).

Backbone turns Service operations into CLI commands and reads them back from the
configuration snapshot. The commands go through a transport, picked in one place
(transport()): the real switches over HTTPS, or the in-memory fake in fake_devices.
"""
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

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


@dataclass(frozen=True)
class Service:
    """What a backbone holds for one SVLAN. Parts may be missing if a change stopped halfway."""
    svlan: int
    svlan_configured: bool = False
    name: Optional[str] = None
    sap: bool = False
    unis: tuple = ()
    cvlan_all: bool = False

    def is_complete(self, name: str, unis) -> bool:
        return (self.svlan_configured and self.name == name and self.sap
                and self.cvlan_all and set(self.unis) == set(unis))


class Backbone:
    """One backbone, reached at its IP."""
    def __init__(self, ip: str, cli: Transport):
        self.ip = ip
        self._cli = cli

    def cli(self, cmd: str) -> str:
        return self._cli(self.ip, cmd)

    def configure_service(self, svlan: int, name: str, unis) -> None:
        self.cli(f"ethernet-service svlan {svlan} admin-state enable")
        self.cli(f"ethernet-service service-name {name} svlan {svlan}")
        self.cli(f"ethernet-service sap {svlan} service-name {name}")
        for uni in unis:
            self.cli(f"ethernet-service sap {svlan} uni port {uni}")
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
        if service.svlan_configured:
            self.cli(f"no ethernet-service svlan {svlan}")

    def read_service(self, svlan: int) -> Optional[Service]:
        return parse_service(self.cli("show configuration snapshot vlan"), svlan)

    def set_uni_admin_state(self, uni: str, enabled: bool) -> None:
        self.cli(f"interfaces {uni} admin-state {'enable' if enabled else 'disable'}")


def parse_service(snapshot: str, svlan: int) -> Optional[Service]:
    """Reads the ethernet-service lines for one SVLAN out of 'show configuration snapshot vlan'."""
    n = str(svlan)
    found = False
    fields = {}
    unis = []
    for line in snapshot.splitlines():
        words = [w.strip('"') for w in line.split()]
        if words[:1] != ["ethernet-service"]:
            continue
        rest = words[1:]
        if rest[:2] == ["svlan", n]:
            fields["svlan_configured"] = True
        elif rest[:1] == ["service-name"] and rest[2:4] == ["svlan", n]:
            fields["name"] = rest[1]
        elif rest[:2] == ["sap", n]:
            sap_rest = rest[2:]
            if sap_rest[:1] == ["service-name"]:
                fields["sap"] = True
            elif sap_rest[:2] == ["uni", "port"]:
                for port in sap_rest[2:]:
                    unis.extend(expand_port_range(port))
            elif sap_rest[:2] == ["cvlan", "all"]:
                fields["cvlan_all"] = True
            else:
                continue
        else:
            continue
        found = True
    if not found:
        return None
    return Service(svlan=svlan, unis=tuple(unis), **fields)


def expand_port_range(port_range: str) -> list:
    """'1/1/1-3' -> ['1/1/1', '1/1/2', '1/1/3']; a single port is returned as is."""
    match = re.fullmatch(r"(.*/)(\d+)-(\d+)", port_range)
    if not match:
        return [port_range]
    prefix, start, end = match.groups()
    return [f"{prefix}{port}" for port in range(int(start), int(end) + 1)]


COOKIE_CACHE = {}  # Dictionary to store cookies per switch IP


def get_cookie(ip: str, retries: int = 3, delay: float = 1.0) -> str:
    """Authenticates on a device and caches its session cookie. Raises APIRequestError."""
    auth_url = f"https://{ip}?domain=auth&username={SWITCH_USERNAME}&password={SWITCH_PASSWORD}"
    headers = dict(AOS_JSON)

    for attempt in range(retries):
        try:
            response = requests.get(auth_url, headers=headers, verify=False, timeout=5)
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

    for attempt in range(retries):
        url = "https://{}?domain=cli&cmd={}".format(ip, cmd)
        try:
            response = requests.get(url, headers=headers, data={}, verify=False, timeout=5)
            if response.status_code != 200:
                try:
                    error_message = response.json().get("error", response.text)
                except ValueError:
                    error_message = response.text

                logger.error(f"Request to {ip} failed with status {response.status_code}: {error_message}")
                raise APIRequestError(f"Request to {ip} failed with status {response.status_code}: {error_message}")

            data = response.json()
            result = data.get("result", {})
            if result.get("error") == "You must login first":
                logger.info(f"Cookie expired on {ip}, re-authenticating.")
                COOKIE_CACHE[ip] = get_cookie(ip)
                headers['Cookie'] = f"wv_sess={COOKIE_CACHE[ip]}"
                continue

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


def transport() -> Transport:
    """The one place that decides whether commands reach real devices."""
    from . import fake_devices  # fake_devices needs APIRequestError from here
    if fake_devices.devices_are_fake():
        return fake_devices.backbone.cli
    return https_cli


def backbone(ip: str) -> Backbone:
    return Backbone(ip, transport())
