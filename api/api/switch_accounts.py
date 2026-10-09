"""
Switch accounts (see CONTEXT.md, docs/adr/0004): the logins BLab creates on a Switch for each
user who may work on it (the holder, and each user the holder's Topology is shared with), so
that nobody but BLab and the admins needs `admin`.

sync() makes a Switch's accounts match who may work on it now. Reserve, Share and Unshare call
it at once; the Switch worker calls it before each Cleanup (so a Release removes them first)
and, through sync_due(), retries whatever failed, so a Switch that can't be reached never
blocks a Reservation, a Share or a Release. A SwitchAccount row stays until its account is
removed from the Switch, so an account left on a Switch is never forgotten.

Account names (docs/adr/0004): the BLab username as is when AOS takes it (1 to 63 ASCII
letters, digits and ._@+-), otherwise `blab-<user id>`. A username that is itself of the
form blab-<number>, or that is `admin` or `default` in any case, also gets `blab-<user id>`,
so two users never share a name and nobody is ever given `admin`.
"""
import logging
import re
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import topology
from .lab_switch import LabSwitchError, check_account_name, lab_switch
from .models import NO_MANAGEMENT_IP, PendingCleanup, Reservation, Switch, SwitchAccount

logger = logging.getLogger(__name__)

FALLBACK_NAME = re.compile(r'blab-\d+')
PASSWORD_LENGTH = 16
PASSWORD_SYMBOLS = '-.@%='  # AOS wants one non-alphanumeric character; these pass its CLI unquoted
RETRY_AFTER = timedelta(minutes=5)  # between two attempts on a Switch whose accounts failed
SWITCHES_PER_CYCLE = 3  # sync_due's share of a Switch worker cycle, so Cleanups don't wait long


def account_name(user: User) -> str:
    """The name of user's Switch accounts (see the module docstring)."""
    name = user.username
    try:
        check_account_name(name)
    except ValueError:
        return f'blab-{user.id}'
    return f'blab-{user.id}' if FALLBACK_NAME.fullmatch(name) else name


def new_password(name: str) -> str:
    """
    A random password that AOS's default password policy takes: an upper and a lower case
    letter, a digit and a symbol, and not containing the account name.
    """
    alphabet = string.ascii_letters + string.digits + PASSWORD_SYMBOLS
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))
        if (any(c.isupper() for c in password) and any(c.islower() for c in password)
                and any(c.isdigit() for c in password) and any(c in PASSWORD_SYMBOLS for c in password)
                and name.lower() not in password.lower()):
            return password


def entitled(switch: Switch) -> set:
    """The ids of the users who may work on the Switch now: none unless it is reserved."""
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return set()
    holder_id = Reservation.objects.filter(switch=switch).values_list('user_id', flat=True).first()
    return set() if holder_id is None else topology.users_who_may_work(holder_id)


def _out_of_step(wanted: set, accounts: List[SwitchAccount]) -> bool:
    """Whether some account is missing, not created yet, or no longer wanted."""
    have = {a.user_id for a in accounts}
    return bool(wanted - have) or any(a.user_id not in wanted or not a.created for a in accounts)


def sync(switch: Switch, now: Optional[datetime] = None) -> List[str]:
    """
    Creates the Switch accounts of the users who may work on the Switch and removes the
    others. Returns what failed, one message per account; whatever failed is kept in its row
    (error) for sync_due to retry. Never raises for a Switch that can't be reached.

    The rows are saved before BLab talks to the Switch, and no lock is held meanwhile: a
    crash then leaves a row that sync_due finishes (creating and removing again are both
    harmless), and a slow Switch never holds up a request writing to the database. If who may
    work on the Switch changed during the call, the row is left out of step for sync_due.
    """
    now = now or timezone.now()
    with transaction.atomic():
        # Short lock: two syncs must not both create the same user's row
        switch = Switch.objects.select_for_update().get(pk=switch.pk)
        wanted = entitled(switch)
        accounts = {a.user_id: a for a in switch.accounts.all()}
        for user in User.objects.filter(id__in=wanted - accounts.keys()):
            name = account_name(user)
            accounts[user.id] = SwitchAccount.objects.create(switch=switch, user=user, name=name,
                                                             password=new_password(name))
        to_create = [a for a in accounts.values() if a.user_id in wanted and not a.created]
        to_remove = [a for a in accounts.values() if a.user_id not in wanted]
        # An unwanted row whose name a wanted one now has (a deleted user's name taken again):
        # the account on the Switch is the new user's, so only the row goes
        wanted_names = {a.name for a in accounts.values() if a.user_id in wanted}
        for account in [a for a in to_remove if a.name in wanted_names]:
            account.delete()
            to_remove.remove(account)
    if not to_remove and not to_create:
        return []

    if switch.mngt_IP == NO_MANAGEMENT_IP:
        failed = {a.name: 'no management IP' for a in to_remove}
    else:
        try:
            failed = lab_switch(switch.mngt_IP).update_accounts(
                create={a.name: a.password for a in to_create}, remove=[a.name for a in to_remove])
        except LabSwitchError as e:
            failed = {a.name: str(e) for a in to_remove + to_create}

    failures = []
    still_wanted = entitled(switch)
    for account in to_remove + to_create:
        error = failed.get(account.name)
        rows = SwitchAccount.objects.filter(pk=account.pk)
        if error is None and account in to_remove:
            if account.user_id in still_wanted:  # wanted again meanwhile: sync_due creates it again
                rows.update(created=False, error=None, tried_at=now)
            else:
                rows.delete()
        elif error is None:
            if not rows.update(created=True, error=None, tried_at=now):
                _keep_track_of(account, now)
        else:
            rows.update(error=error, tried_at=now)
            verb = 'remove' if account in to_remove else 'create'
            failures.append(f"Couldn't {verb} the Switch account {account.name} on {switch.mngt_IP}: {error}")
    for failure in failures:
        logger.warning(failure)
    return failures


def _keep_track_of(account: SwitchAccount, now: datetime) -> None:
    """
    An account just created on the Switch whose row an overlapping sync deleted meanwhile:
    the row is made again, so the account is never forgotten. If that sync has made a new row
    for the same user (another password), it is marked not created: sync_due sends its
    password again, so the Switch ends up with the one BLab shows.
    """
    row, made = SwitchAccount.objects.get_or_create(
        switch_id=account.switch_id, user_id=account.user_id,
        defaults={'name': account.name, 'password': account.password, 'created': True, 'tried_at': now})
    if not made and row.password != account.password:
        SwitchAccount.objects.filter(pk=row.pk).update(created=False, error=None)
    logger.warning("Switch account %s on switch %s: its row was gone, made again", account.name, account.switch_id)


def sync_due(now: Optional[datetime] = None) -> List[str]:
    """
    The Switch worker's pass: syncs the Switches whose accounts are out of step (a few per
    call), and retries one that failed only RETRY_AFTER its last attempt. A Switch being
    Cleaned up is left to its Cleanup, which syncs it first. Returns what it did.
    """
    now = now or timezone.now()
    outcomes = []
    cleaning_up = set(PendingCleanup.objects.values_list('switch_id', flat=True))
    switches = (Switch.objects.filter(Q(reservation__isnull=False) | Q(accounts__isnull=False))
                .exclude(id__in=cleaning_up).distinct().order_by('id'))
    for switch in switches:
        if len(outcomes) >= SWITCHES_PER_CYCLE:
            break
        accounts = list(switch.accounts.all())
        if not _out_of_step(entitled(switch), accounts):
            continue
        if any(a.error and a.tried_at and now - a.tried_at < RETRY_AFTER for a in accounts):
            continue
        failures = sync(switch, now)
        outcomes.append(f"Switch accounts of {switch.mngt_IP}: "
                        + ('; '.join(failures) if failures else 'up to date'))
    return outcomes


def sync_topology(owner: User) -> List[str]:
    """Syncs every Switch the owner holds, as sharing their Topology changes who may work on them."""
    failures = []
    for switch in Switch.objects.filter(reservation__user=owner).order_by('id'):
        failures += sync(switch)
    return failures


def accounts_for(user: User) -> List[Dict]:
    """
    The user's Switch accounts, on every Switch they may work on now, with the password:
    for that user's eyes only. `state` is 'ready', 'pending' (BLab is creating it) or
    'failed' (BLab retries; `error` says why).
    """
    owners = topology.workable_owners(user)
    reservations = (Reservation.objects.filter(user_id__in=owners).exclude(switch__mngt_IP=NO_MANAGEMENT_IP)
                    .select_related('switch', 'user').order_by('switch__mngt_IP'))
    accounts = {a.switch_id: a for a in SwitchAccount.objects.filter(user=user)}
    result = []
    for reservation in reservations:
        account = accounts.get(reservation.switch_id)
        state = 'ready' if account and account.created else 'failed' if account and account.error else 'pending'
        result.append({
            'switch': reservation.switch_id,
            'mngt_IP': reservation.switch.mngt_IP,
            'holder': reservation.user.username,
            'name': account.name if account else account_name(user),
            'password': account.password if state == 'ready' else None,
            'state': state,
            'error': account.error if account and not account.created else None,
        })
    return result


def account_on(switch: Switch, user: User) -> Optional[Dict]:
    """user's Switch account on this Switch, as accounts_for shows it, or None."""
    return next((a for a in accounts_for(user) if a['switch'] == switch.id), None)
