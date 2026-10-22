"""
AOS releases (see CONTEXT.md, Golden init; docs/adr/0011): which release the lab runs per
model family, where its images come from, and installing one on a Switch.

The release data is tsbuddy's (github.com/bgbyte/tsbuddy, by Brian, ALE US; copied with his OK,
#48): aos_releases/ga_latest.json (the latest GA per family) and aos_releases/ga_index.json (the GA
build of each release per family), as of tsbuddy commit 7f5245f (2026-09-17), and the model ->
family and family -> image tables of its aos/aosdl.py. To follow a new GA, copy tsbuddy's two
JSON files over these: the lab release changes at each Switch's next Cleanup.

Images come from ALE's image server (settings.BLAB_AOS_IMAGE_SERVER, 10.46.4.37: the code server,
reached by its address since its name needs ALE's DNS). Lab Switches have no curl in their CLI,
so BLab downloads each image once onto its server, checks it against the server's imgsha256sum,
keeps it (settings.BLAB_AOS_IMAGE_CACHE), and copies it to the Switch over SFTP
(LabSwitch.install_images). Where a family has a Secure Boot variant (Release/secure/), BLab takes
it, unless the Switch's image says it isn't one (show microcode: Secure Boot No).

A Cleanup installs the lab release into init/ (install_lab_release) once BLAB_AOS_RELEASE_MODE
is 'enforce'; until then it only says it would. One that fails never stops the Cleanup: it goes
on with the image init/ has, and says why in its history.
"""
import hashlib
import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import requests
from django.conf import settings

from .lab_switch import ImageFile, LabSwitchError, lab_switch

logger = logging.getLogger(__name__)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aos_releases')
# tsbuddy's image_map: the images a family boots from (medora's three are one release)
IMAGES = {
    'everest': ['Uos.img'], 'tor': ['Tos.img'], 'vindhya': ['Nos.img'], 'medora': ['Mos.img', 'Meni.img', 'Mhost.img'],
    'yukon': ['Yos.img'], 'yukon_plus': ['Ypos.img'], 'shasta': ['Uos.img'], 'aravalli': ['Nosa.img'],
    'shasta_n': ['Uosn.img'], 'whitney': ['Wos.img'], 'nandi': ['Nos.img'], 'kailash': ['Kaos.img'],
    'dronagiri': ['Dos.img'], 'horsley': ['Hos.img'],
}
# tsbuddy's model_family_map, by the model number BLab records ("OS6860E-24" -> 6860E, then 6860)
FAMILIES = {
    '6360': 'aravalli', '6370': 'horsley', '6465': 'vindhya', '6560': 'nandi', '6570M': 'whitney',
    '6575': 'dronagiri', '6860': 'shasta', '6860N': 'shasta_n', '6865': 'everest', '6870': 'kailash',
    '6920': 'yukon_plus', '9900': 'medora', '9907': 'medora', '9912': 'medora',
}
# tsbuddy's model_6900_variants: an OS6900 is a Yukon or a Tor by its variant
OS6900_VARIANTS = {'yukon': ('T48C6', 'V72', 'V48C8', 'X48C6'), 'tor': ('X20', 'X40', 'X72', 'T20', 'T40', 'Q32')}
NO_GA = ('', 'N/S', 'N/A', 'UNK')  # what ga_index.json says for a release a family has no GA of
MODEL = re.compile(r'^(?:OS)?(\d{4}[A-Z]*)(?:-(\S+))?', re.IGNORECASE)
RELEASE = re.compile(r'^(\d+\.\d+(?:\.\d+)?)\.(\d+)\.(R\d+)$')  # 8.11.162.R01 -> 8.11, 162, R01
SECURE = 'secure'
CHECKSUMS = 'imgsha256sum'  # "Yos.img: <sha256>", a line per image, beside them on the image server
CHUNK = 1 << 20
TIMEOUT = (10, 60)  # seconds to connect to the image server, and between two reads
_downloading = threading.Lock()  # one download at a time: two Cleanups of a family share the image


def _load(name: str) -> dict:
    with open(os.path.join(DATA, name), encoding='utf-8') as file:
        return json.load(file)


LATEST = _load('ga_latest.json')
INDEX = _load('ga_index.json')


def family(model: str) -> Optional[str]:
    """The AOS family of a Switch model ("OS6900-X48C6" -> yukon), or None if tsbuddy doesn't know it."""
    match = MODEL.match((model or '').strip())
    if not match:
        return None
    number, variant = match.group(1).upper(), (match.group(2) or '').upper()
    if number == '6900':
        return next((f for f, variants in OS6900_VARIANTS.items() if variant in variants), None)
    return FAMILIES.get(number) or FAMILIES.get(number[:4])


def lab_release(model: str) -> Optional[str]:
    """The release the lab runs on Switches of this model: the latest GA of its family, or None."""
    entry = LATEST.get(family(model) or '')
    return entry.get('latest_version') if entry else None


def compatible_releases(model: str) -> List[str]:
    """Every GA release of the model's family, newest first ("8.11.162.R01", ...); [] if its family is unknown."""
    name = family(model)
    releases = []
    for key, builds in INDEX.items():  # "8.10R02" -> {family: ".105"}
        build = (builds.get(name) or '') if name else ''
        major = re.match(r'^(.*?)(R\d+)$', key)
        if build in NO_GA or not major:
            continue
        releases.append(f"{major.group(1)}{build}.{major.group(2)}")
    return releases


def mode() -> str:
    return settings.BLAB_AOS_RELEASE_MODE


class ImageError(Exception):
    """An image couldn't be had from the image server, or isn't what it says."""


@dataclass(frozen=True)
class Source:
    """Where a release's images for one family are, on the image server."""
    release: str
    family: str
    secure: bool  # the Secure Boot variant (Release/secure/)

    @property
    def path(self) -> str:
        major, build, rel = RELEASE.match(self.release).groups()
        return f"OS_{major.replace('.', '_')}_{build}_{rel}/{self.family}/Release" + (f'/{SECURE}' if self.secure else '')

    def url(self, name: str) -> str:
        return f"{settings.BLAB_AOS_IMAGE_SERVER.rstrip('/')}/{self.path}/{name}"


def source(release: str, family_name: str, secure: Optional[bool]) -> Source:
    """
    Where to download the release from: its Secure Boot variant when the family has one, unless
    the Switch runs a non-Secure Boot image (secure False). Raises ImageError.
    """
    if not RELEASE.match(release or ''):
        raise ImageError(f"{release!r} is not an AOS release")
    if family_name not in IMAGES:
        raise ImageError(f"no images known for the {family_name} family")
    if secure is not False:
        candidate = Source(release, family_name, True)
        if _checksums(candidate, missing_ok=True) is not None:
            return candidate
    return Source(release, family_name, False)


class HttpImageServer:
    """The image server, over HTTP. fake_devices.image_server stands in for it with fake devices."""

    @staticmethod
    def text(url: str) -> Optional[str]:
        """A small file's text, or None if the server has no such file. Raises ImageError."""
        try:
            answer = requests.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise ImageError(f"cannot reach the image server: {e}") from e
        if answer.status_code == 404:
            return None
        if answer.status_code != 200:
            raise ImageError(f"{url} answered {answer.status_code}")
        return answer.text

    @staticmethod
    def fetch(url: str, path: str) -> None:
        """Downloads a file into path. Raises ImageError."""
        try:
            with requests.get(url, stream=True, timeout=TIMEOUT) as answer:
                if answer.status_code != 200:
                    raise ImageError(f"{url} answered {answer.status_code}")
                with open(path, 'wb') as file:
                    for chunk in answer.iter_content(CHUNK):
                        file.write(chunk)
        except requests.RequestException as e:
            raise ImageError(f"cannot download {url}: {e}") from e


def image_server():
    """The one place that decides whether images come from the real image server."""
    from . import fake_devices
    return fake_devices.image_server if fake_devices.devices_are_fake() else HttpImageServer


def _checksums(src: Source, missing_ok: bool = False) -> Optional[Dict[str, str]]:
    """The image server's imgsha256sum for a source, name -> sha256; None if it has none and missing_ok."""
    text = image_server().text(src.url(CHECKSUMS))
    if text is None:
        if missing_ok:
            return None
        raise ImageError(f"{src.url(CHECKSUMS)}: no such file")
    sums = re.findall(r'^\s*(\S+?):?\s+([0-9a-fA-F]{64})\s*$', text, re.MULTILINE)
    return {name: digest.lower() for name, digest in sums}


def _digests(path: str):
    sha256, md5 = hashlib.sha256(), hashlib.md5()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(CHUNK), b''):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def download(src: Source) -> List[ImageFile]:
    """
    The images of a source, downloaded onto the BLab server once and kept, each checked against
    the image server's sha256 (again when kept: a newer build under the same name is downloaded
    afresh). Raises ImageError.
    """
    sums = _checksums(src)
    directory = os.path.join(settings.BLAB_AOS_IMAGE_CACHE, *src.path.split('/'))
    images = []
    with _downloading:
        os.makedirs(directory, exist_ok=True)
        for name in IMAGES[src.family]:
            expected = sums.get(name)
            if not expected:
                raise ImageError(f"{src.url(CHECKSUMS)} has no sha256 for {name}")
            path = os.path.join(directory, name)
            digests = _digests(path) if os.path.exists(path) else None
            if digests is None or digests[0] != expected:
                part = path + '.part'
                try:
                    image_server().fetch(src.url(name), part)
                    digests = _digests(part)
                    if digests[0] != expected:
                        raise ImageError(f"{src.url(name)} doesn't match its sha256 in {CHECKSUMS}")
                    os.replace(part, path)
                finally:
                    if os.path.exists(part):
                        os.remove(part)
                logger.info("Downloaded %s", src.url(name))
            images.append(ImageFile(name, path, os.path.getsize(path), digests[1]))
    return images


def install(switch, release: str, directories: Sequence[str]) -> None:
    """
    Installs a release's images into the Switch's directories (each holds the same images
    after), and nothing else: no reload. What was there stays as it was if any step fails.
    Raises LabSwitchError or ImageError.
    """
    name = family(switch.model)
    if name is None:
        raise ImageError(f"no AOS family known for the model {switch.model!r}")
    switch_side = lab_switch(switch.mngt_IP)
    secure = switch_side.microcode(directories[0]).secure
    switch_side.install_images(download(source(release, name, secure)), directories)


# Where a Cleanup installs the lab release: init/, and certified/, where the Cleanup's check of
# init/ finds its copy (ADR 0006, #80) before it replaces working/ from init/
CLEANUP_DIRECTORIES = ('init', 'certified')


def install_lab_release(switch) -> List[str]:
    """
    A Cleanup's step, before init/ is copied into working/: if init/ doesn't hold the lab
    release for the Switch's model, installs it there (BLAB_AOS_RELEASE_MODE 'enforce'), or
    only says it would ('report'). Never raises: what it did, or why it couldn't, is a line
    each for the Cleanup's history, and a Switch it couldn't install on is Cleaned up with
    the image it has.
    """
    wanted = lab_release(switch.model)
    if wanted is None:
        return []
    try:
        have = lab_switch(switch.mngt_IP).microcode('init').release
    except LabSwitchError as e:
        return [f"AOS release not checked: {e}"]
    if have == wanted:
        return []
    if mode() != 'enforce':
        return [f"Would install AOS {wanted} (BLAB_AOS_RELEASE_MODE=report): init/ has {have}"]
    try:
        install(switch, wanted, CLEANUP_DIRECTORIES)
    except (LabSwitchError, ImageError) as e:
        logger.warning("AOS %s not installed on %s: %s", wanted, switch.mngt_IP, e)
        return [f"AOS {wanted} not installed, Cleaned up with {have}: {e}"]
    logger.info("AOS %s installed in init/ on %s (was %s)", wanted, switch.mngt_IP, have)
    return [f"AOS {wanted} installed (was {have})"]
