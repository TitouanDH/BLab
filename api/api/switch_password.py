"""
Switch passwords (see CONTEXT.md, docs/adr/0004): the one password a user chooses for all their
Switch accounts, at their first login, behind the BLAB_PROJECTS stage setting.

problems() holds the rules, which the Account page also checks as the user types
(frontend/src/utils/switchPassword.js). choose() stores the password encrypted and pushes it to
the user's Switch accounts at once (api.switch_accounts.sync); the Switch worker retries the ones
it couldn't reach, and new Switch accounts get it too. It is never shown again, only changed.

It is encrypted with Fernet under BLAB_SWITCH_PASSWORD_KEY, a key both stages share, since BLab
pushes it while the user is away and either stage's worker may do it. Several keys, comma-
separated, let the key change: the first encrypts, every one decrypts.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, NamedTuple

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from .lab_switch import ACCOUNT_PASSWORD_REFUSED
from .models import Switch, SwitchPassword

logger = logging.getLogger(__name__)

MIN_LENGTH = 8
MAX_LENGTH = 30  # AOS refuses longer ones
# The lab's old shared passwords, and BLab's own login: never a user's, in any case
FORBIDDEN = ('switch@123', 'switch', 'admin')
ALLOWED = re.compile(r'[\x21-\x7e]*')  # printable ASCII, no space: what AOS takes


class Chosen(NamedTuple):
    """A user's Switch password, and when they chose it: an account given it records that time."""
    password: str
    changed_at: datetime


def enabled() -> bool:
    """Whether users choose a Switch password: the BLAB_PROJECTS stage setting."""
    return settings.BLAB_PROJECTS


def problems(password: str, user: User) -> List[str]:
    """Why password can't be user's Switch password, one message per rule it breaks; [] if it can."""
    from .switch_accounts import account_name  # here: switch_accounts imports this module
    found = []
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        found.append(f'It must be {MIN_LENGTH} to {MAX_LENGTH} characters long.')
    if not ALLOWED.fullmatch(password) or ACCOUNT_PASSWORD_REFUSED & set(password):
        found.append("It may only hold English letters, digits and symbols, without spaces, ! \" or '.")
    for pattern, what in (('[A-Z]', 'an upper case letter'), ('[a-z]', 'a lower case letter'),
                          ('[0-9]', 'a digit'), ('[^A-Za-z0-9]', 'a symbol')):
        if not re.search(pattern, password):
            found.append(f'It must hold {what}.')
    names = {user.username.lower(), account_name(user).lower()} - {''}
    if any(name in password.lower() for name in names):
        found.append('It must not contain your username.')
    if password.lower() in FORBIDDEN:
        found.append("It must not be the lab's old password.")
    return found


def _fernet() -> MultiFernet:
    """Encrypts with the first key of BLAB_SWITCH_PASSWORD_KEY, decrypts with any of them."""
    if not settings.BLAB_SWITCH_PASSWORD_KEYS:
        raise RuntimeError('BLAB_SWITCH_PASSWORD_KEY is not set')
    return MultiFernet([Fernet(key) for key in settings.BLAB_SWITCH_PASSWORD_KEYS])


def encrypt(password: str) -> str:
    """The password as stored in SwitchPassword.encrypted."""
    return _fernet().encrypt(password.encode()).decode()


def decrypt(encrypted: str) -> str:
    """Raises cryptography.fernet.InvalidToken if no key of BLAB_SWITCH_PASSWORD_KEY opens it."""
    return _fernet().decrypt(encrypted.encode()).decode()


def has_one(user: User) -> bool:
    """Whether the user has chosen a Switch password (whatever BLAB_PROJECTS says)."""
    return SwitchPassword.objects.filter(user=user).exists()


def current(user_ids) -> Dict[int, Chosen]:
    """
    The Switch passwords of these users who chose one, by user id, as Chosen: changed_at tells
    which one an account has. {} when disabled. One that no key opens (the key
    changed without keeping the old one) is left out and logged: BLab then gives new Switch
    accounts a random password, shown as before, until the user chooses theirs again.
    """
    if not enabled():
        return {}
    result = {}
    for row in SwitchPassword.objects.filter(user_id__in=user_ids):
        try:
            result[row.user_id] = Chosen(decrypt(row.encrypted), row.changed_at)
        except InvalidToken:
            logger.error("No key of BLAB_SWITCH_PASSWORD_KEY opens the Switch password of user %s", row.user_id)
    return result


def choose(user: User, password: str) -> List[Dict]:
    """
    Makes password user's Switch password and pushes it to every Switch account they have.
    Returns the accounts it couldn't reach, as accounts_for gives them (the Switch worker
    retries them). Raises ValueError with the broken rules if the password can't be one.
    """
    from . import switch_accounts  # here: switch_accounts imports this module
    broken = problems(password, user)
    if broken:
        raise ValueError(broken)
    SwitchPassword.objects.update_or_create(
        user=user, defaults={'encrypted': encrypt(password), 'changed_at': timezone.now()})
    logger.info("User %s chose a new Switch password", user.username)
    for switch in Switch.objects.filter(accounts__user=user).distinct().order_by('id'):
        switch_accounts.sync(switch)
    return [a for a in switch_accounts.accounts_for(user) if a['state'] == 'failed']
