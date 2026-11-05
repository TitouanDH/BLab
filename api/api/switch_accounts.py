"""
Switch accounts (see CONTEXT.md, docs/adr/0004): the logins BLab creates on a Switch for each
user who may work on it (topology.users_who_may_work_on: the holder and the other members of
its Project, or, with Projects off, each user the holder's Topology is shared with), so that
nobody but BLab and the admins needs `admin`.

sync() makes a Switch's accounts match who may work on it now. Reserve, Share, Unshare, and
joining, leaving or being removed from a Project call it at once; the Switch worker calls it
before each Cleanup (so a Release removes them first)
and, through sync_due(), retries whatever failed, so a Switch that can't be reached never
blocks a Reservation, a Share or a Release. A SwitchAccount row stays until its account is
removed from the Switch, so an account left on a Switch is never forgotten.

Each change is saved on the Switch (write memory), so that in a VC every chassis holds the
accounts and a new master still has them after a takeover (#60). A VC slave refuses local
users: the master holds those of the whole VC. So on a slave, an account to create is done
once BLab makes it on the master (the master is reserved into the same Project, or by the same
holder, and the user may work on it: vc_master), and one to remove is the master's to remove.

Read-only accounts (#91, with BLAB_READ_ONLY_ACCOUNTS on): every user with a Switch password
also has a read-only account, with that password, on every Switch they may not work on, reserved
or not, so they can look at any lab Switch. It is the same account, under the same name: it
becomes read-write while they may work on the Switch, and read-only again at Release or when they
leave its Project. What is due at once (a read-write account to make, change or remove, and one
that must become read-only) is done at once; the read-only accounts to make, remove or give a new
Switch password wait for sync_due, which does a few of them on one Switch per cycle, so the lab's
Switches aren't all written to at once.

Quarantine access (#118, api.quarantine_access): the user an open Quarantine names may work on its
Switch (entitled), so sync() makes their read-write account and removes it once the access ends.

Guest accounts (#96, api.guest_logins): sync() brings a Switch's in step too, and sync_due
retries them; a Release change and a re-creation treat them as the users'.

Account names (docs/adr/0004): the BLab username as is when AOS takes it (1 to 63 ASCII
letters, digits and ._@+-), otherwise `blab-<user id>`. A username that is itself of the
form blab-<number>, or that is `admin` or `default` in any case, also gets `blab-<user id>`,
so two users never share a name and nobody is ever given `admin`.
"""
import logging
import re
import secrets
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from django.conf import settings
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import clean_state, guest_logins, quarantine_access, switch_password, topology
from .lab_switch import LabSwitchError, check_account_name, lab_switch
from .models import NO_MANAGEMENT_IP, GuestAccount, PendingCleanup, Quarantine, Reservation, Switch, SwitchAccount

logger = logging.getLogger(__name__)

FALLBACK_NAME = re.compile(r'blab-\d+')
PASSWORD_LENGTH = 16
PASSWORD_SYMBOLS = '-.@%='  # AOS wants one non-alphanumeric character; these pass its CLI unquoted
RETRY_AFTER = timedelta(minutes=5)  # between two attempts on a Switch whose accounts failed
SWITCHES_PER_CYCLE = 3  # sync_due's share of a Switch worker cycle, so Cleanups don't wait long
# Read-only accounts (#91) are made a few at a time, on one Switch per cycle at most (one cycle
# every 5 s or so), so that turning them on, or a user's new Switch password, doesn't write to
# every lab Switch at once; one that failed is tried again only after READ_ONLY_RETRY_AFTER
READ_ONLY_SWITCHES_PER_CYCLE = 1
READ_ONLY_PER_PASS = 10
READ_ONLY_RETRY_AFTER = timedelta(hours=1)


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
    """
    The ids of the users who may work on the Switch now: those of its Reservation, or the holder
    an open Quarantine of it names (#118, api.quarantine_access).
    """
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        return set()
    reservation = Reservation.objects.filter(switch=switch).first()
    if reservation is not None:
        return topology.users_who_may_work_on(reservation)
    holder = quarantine_access.holder_id(switch)
    return set() if holder is None else {holder}


def read_only_users() -> Dict[int, switch_password.Chosen]:
    """
    The users who get a read-only account (#91) on every Switch they may not work on, with their
    Switch password (switch_password.current), by user id: each active Account with a Switch
    password BLab can open, while BLAB_READ_ONLY_ACCOUNTS is on. Not a user whose account name is
    a lab manager's login (a BaselineExtra): BLab never changes those.
    """
    if not settings.BLAB_READ_ONLY_ACCOUNTS:
        return {}
    chosen = switch_password.current(User.objects.filter(is_active=True).values('id'))
    managers = _managers_logins()
    return {user.id: chosen[user.id] for user in User.objects.filter(id__in=list(chosen))
            if account_name(user).lower() not in managers}


def _managers_logins() -> Set[str]:
    """The lab managers' logins (BaselineExtra), lower case: never a read-only account's name."""
    return {login.lower() for login in clean_state.extras().logins}


def read_only_login(user: User) -> Optional[str]:
    """The name of user's read-only accounts (#91), if they get them, else None."""
    if not settings.BLAB_READ_ONLY_ACCOUNTS or not switch_password.current([user.id]):
        return None
    name = account_name(user)
    return None if name.lower() in _managers_logins() else name


def read_only_names(switch: Switch) -> Set[str]:
    """The names of the Switch's read-only accounts (#91), which a Switch nobody holds keeps."""
    return set(SwitchAccount.objects.filter(switch=switch, read_only=True).values_list('name', flat=True))


def _stale(account: SwitchAccount, chosen: Dict) -> bool:
    """Whether the account lacks its user's latest Switch password (chosen: switch_password.current)."""
    return account.user_id in chosen and account.switch_password_at != chosen[account.user_id].changed_at


@dataclass
class _Work:
    """
    What a Switch's accounts need (see _work_on). Due at once: the read-write accounts to make
    (new users' too), to give a password, or to turn read-write; those to turn read-only; and the
    read-write ones to remove. The read-only work may wait for sync_due.
    """
    create: List[SwitchAccount] = field(default_factory=list)
    new: List[int] = field(default_factory=list)  # users to give a read-write account, who have no row
    remove: List[SwitchAccount] = field(default_factory=list)
    read_only_create: List[SwitchAccount] = field(default_factory=list)
    read_only_new: List[int] = field(default_factory=list)  # users to give a read-only account, who have no row
    read_only_remove: List[SwitchAccount] = field(default_factory=list)

    def due_now(self) -> bool:
        return bool(self.create or self.new or self.remove)

    def read_only_count(self) -> int:
        return len(self.read_only_create) + len(self.read_only_new) + len(self.read_only_remove)


def _work_on(wanted: set, read_only: set, accounts: Dict[int, SwitchAccount], chosen: Dict) -> _Work:
    """
    What the accounts (by user id) need so that the users in `wanted` have a read-write account,
    those in `read_only` (none of `wanted`) a read-only one, and nobody else any. `chosen`: the
    Switch passwords of both (switch_password.current).
    """
    work = _Work()
    for user_id in sorted(wanted - accounts.keys()):
        work.new.append(user_id)
    for user_id in sorted(read_only - accounts.keys()):
        work.read_only_new.append(user_id)
    for account in accounts.values():
        if account.user_id in wanted:
            if not account.created or _stale(account, chosen) or account.read_only:
                work.create.append(account)
        elif account.user_id in read_only:
            if not account.read_only:  # it may no longer work on it: read-only at once
                work.create.append(account)
            elif not account.created or _stale(account, chosen):
                work.read_only_create.append(account)
        elif account.read_only:
            work.read_only_remove.append(account)
        else:
            work.remove.append(account)
    return work


def _read_only_wanted(switch: Switch, wanted: set, users: Dict) -> set:
    """Who gets a read-only account on the Switch: read_only_users() but those who may work on it."""
    return set() if switch.mngt_IP == NO_MANAGEMENT_IP else set(users) - wanted


def remove_before_release_change(switch: Switch) -> List[str]:
    """
    Removes the Switch accounts BLab made on the Switch from the AOS release it runs, right
    before it reloads into another (#49). That release keeps its local users in a table of its
    own (#74): left there, they would come back, with their passwords, when the Cleanup brings
    that release back, after the Release removed them from the other's. recreate() makes them
    again once the Switch is up. What couldn't be removed, in plain words.
    """
    names = list(SwitchAccount.objects.filter(switch=switch, created=True).values_list('name', flat=True))
    guests = list(GuestAccount.objects.filter(switch=switch, created=True).values_list('name', flat=True))
    if not names and not guests:
        return []
    try:
        update = lab_switch(switch.mngt_IP).update_accounts({}, names + guests)
    except LabSwitchError as e:
        return [f"Couldn't remove the Switch accounts from the release it ran: {e}"]
    SwitchAccount.objects.filter(switch=switch, name__in=names).exclude(name__in=update.failed).update(created=False)
    GuestAccount.objects.filter(switch=switch, name__in=guests).exclude(name__in=update.failed).update(created=False)
    return [f"Couldn't remove the Switch account {name} from the release it ran: {why}"
            for name, why in update.failed.items()]


def recreate(switch: Switch, now: Optional[datetime] = None, names: Optional[Iterable[str]] = None) -> List[str]:
    """
    Makes every Switch account of the Switch again (only those named in `names`, if given), as
    sync does: once it is up in another AOS release, which keeps its local users in a table of
    its own (#74, #49, #92), the accounts made in the release it ran may be gone. Creating one
    that is there is harmless. Its read-only accounts too, all at once: they are on this Switch alone.
    """
    for model in (SwitchAccount, GuestAccount):  # Guest accounts (#96) too
        rows = model.objects.filter(switch=switch)
        if names is not None:
            rows = rows.filter(name__in=list(names))
        rows.update(created=False)
    return sync(switch, now, read_only_batch=None)


def recreate_mine(switch: Switch, user: User) -> bool:
    """
    My lab's Re-create my account (#92): the user's Switch account on the Switch is made again
    by the Switch worker's next pass (sync_due), for an account lost on its own. Only on a Switch
    the user may work on, with a management IP. Returns whether it was asked for.
    """
    if switch.mngt_IP == NO_MANAGEMENT_IP or user.id not in entitled(switch):
        return False
    # Out of step now, and due at once: sync_due makes it within a cycle (no row: it makes one)
    SwitchAccount.objects.filter(switch=switch, user=user).update(created=False, error=None, tried_at=None)
    return True


def sync(switch: Switch, now: Optional[datetime] = None, read_only_batch: Optional[int] = 0) -> List[str]:
    """
    Brings the Switch accounts of the Switch in step (_sync_users), then its Guest accounts
    (#96, guest_logins.sync). Returns what failed, one message per account.
    """
    now = now or timezone.now()
    return _sync_users(switch, now, read_only_batch) + guest_logins.sync(switch, now)


def _sync_users(switch: Switch, now: datetime, read_only_batch: Optional[int]) -> List[str]:
    """
    Creates the Switch accounts of the users who may work on the Switch and removes the
    others. Returns what failed, one message per account; whatever failed is kept in its row
    (error) for sync_due to retry. Never raises for a Switch that can't be reached.

    With read-only accounts (#91), the others keep theirs, read-only: what must change at once
    is done (an account turns read-only as soon as its user may no longer work on the Switch);
    of the read-only accounts to make, remove or give a new Switch password, only the first
    `read_only_batch` are (all of them for None): the others wait for sync_due.

    The rows are saved before BLab talks to the Switch, and no lock is held meanwhile: a
    crash then leaves a row that sync_due finishes (creating and removing again are both
    harmless), and a slow Switch never holds up a request writing to the database. If who may
    work on the Switch changed during the call, the row is left out of step for sync_due.

    An account is created with its user's Switch password if they chose one (BLAB_PROJECTS),
    else with the row's random one; one without its user's latest Switch password is given it
    (by sync_due too, so a password chosen while the Switch was out of reach gets there later).
    """
    read_only_users_now = read_only_users()
    with transaction.atomic():
        # Short lock: two syncs must not both create the same user's row
        switch = Switch.objects.select_for_update().get(pk=switch.pk)
        wanted = entitled(switch)
        quarantine_access.note_given(switch, now)  # before the account is made (#118)
        read_only = _read_only_wanted(switch, wanted, read_only_users_now)
        accounts = {a.user_id: a for a in switch.accounts.all()}
        chosen = {**switch_password.current(wanted), **{i: read_only_users_now[i] for i in read_only}}
        work = _work_on(wanted, read_only, accounts, chosen)
        later = ([('create', a) for a in work.read_only_create] + [('new', i) for i in work.read_only_new]
                 + [('remove', a) for a in work.read_only_remove])
        now_too = later if read_only_batch is None else later[:read_only_batch]
        to_create = work.create + [a for what, a in now_too if what == 'create']
        to_remove = work.remove + [a for what, a in now_too if what == 'remove']
        new = [(i, False) for i in work.new] + [(i, True) for what, i in now_too if what == 'new']
        users = User.objects.in_bulk([i for i, _ in new])
        for user_id, its_read_only in new:
            if user_id not in users:  # deleted meanwhile
                continue
            name = account_name(users[user_id])
            account = SwitchAccount.objects.create(switch=switch, user_id=user_id, name=name,
                                                   password=new_password(name), read_only=its_read_only)
            accounts[user_id] = account
            to_create.append(account)
        # An unwanted row whose name a wanted one now has (a deleted user's name taken again):
        # the account on the Switch is the new user's, so only the row goes
        wanted_names = {a.name for a in accounts.values() if a.user_id in wanted | read_only}
        for account in [a for a in to_remove if a.name in wanted_names]:
            account.delete()
            to_remove.remove(account)
    if not to_remove and not to_create:
        return []

    failures = []
    through = {}  # name -> the VC master's management IP, for an account made there
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        failed = {a.name: 'no management IP' for a in to_remove}
    else:
        try:
            update = lab_switch(switch.mngt_IP).update_accounts(
                create={a.name: chosen[a.user_id].password if a.user_id in chosen else a.password for a in to_create},
                remove=[a.name for a in to_remove],
                read_only=[a.name for a in to_create if a.user_id not in wanted])
        except LabSwitchError as e:
            failed = {a.name: str(e) for a in to_remove + to_create}
        else:
            failed = update.failed
            if update.not_saved:
                failures.append(f"The Switch accounts of {switch.mngt_IP} were changed but not saved: "
                                f"{update.not_saved}")
            # A VC slave: an account removed is the master's to remove; one to create must be
            # there. A read-only one is: every lab Switch has them
            on_slave = [a for a in to_create if a.name in update.on_slave and a.user_id in wanted]
            if on_slave:
                through, waiting = _made_on_master(switch, on_slave, chosen)
                failed.update(waiting)

    still_wanted = entitled(switch) | _read_only_wanted(switch, set(), read_only_users())
    for account in to_remove + to_create:
        error = failed.get(account.name)
        rows = SwitchAccount.objects.filter(pk=account.pk)
        if error is None and account in to_remove:
            if account.user_id in still_wanted:  # wanted again meanwhile: sync_due creates it again
                rows.update(created=False, error=None, tried_at=now, vc_master=None)
            else:
                rows.delete()
        elif error is None:
            given = chosen[account.user_id].changed_at if account.user_id in chosen else None
            master_ip = through.get(account.name)
            its_read_only = account.user_id not in wanted
            if not rows.update(created=True, error=None, tried_at=now, switch_password_at=given, vc_master=master_ip,
                               read_only=its_read_only):
                _keep_track_of(account, given, now, master_ip, its_read_only)
        else:
            rows.update(error=error, tried_at=now)
            verb = 'remove' if account in to_remove else 'create'
            failures.append(f"Couldn't {verb} the Switch account {account.name} on {switch.mngt_IP}: {error}")
    for failure in failures:
        logger.warning(failure)
    return failures


def _made_on_master(slave: Switch, accounts: List[SwitchAccount], chosen: Dict) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    For accounts a VC slave refused: those BLab has made on its master, with the same Switch
    password (name -> the master's management IP), and why each other one isn't yet (name ->
    why). One isn't made there yet when the master's own row isn't created, or lacks the
    password; the slave's is retried once it is.
    """
    master, why = vc_master(slave)
    made, waiting = {}, {}
    theirs = {a.user_id: a for a in SwitchAccount.objects.filter(switch=master)} if master else {}
    for account in accounts:
        there = theirs.get(account.user_id)
        given = chosen[account.user_id].changed_at if account.user_id in chosen else None
        if master is None:
            waiting[account.name] = why
        elif there is not None and there.created and there.switch_password_at == given:
            made[account.name] = master.mngt_IP
        else:
            waiting[account.name] = f"{slave.mngt_IP} is a VC slave: waiting for the account on its master {master.mngt_IP}"
    return made, waiting


def vc_master(slave: Switch) -> Tuple[Optional[Switch], Optional[str]]:
    """
    The Switch that is the VC master of `slave` (a VC slave, which refuses local users), among
    the Switches reserved into the same Project as it or by the same holder: the one that
    show virtual-chassis topology says is a master, with the master's MAC the slave names.
    Only reads. Returns it, or None and why it wasn't found.
    """
    reservation = Reservation.objects.filter(switch=slave).first()
    if reservation is None:
        return None, f"{slave.mngt_IP} is a VC slave and isn't reserved"
    try:
        master = lab_switch(slave.mngt_IP).vc_topology().master
    except LabSwitchError as e:
        return None, f"{slave.mngt_IP} is a VC slave, and its master can't be told: {e}"
    if master is None:
        return None, f"{slave.mngt_IP} is a VC slave, and its VC topology shows no master"
    same = Q(user_id=reservation.user_id)
    if reservation.project_id:
        same |= Q(project_id=reservation.project_id)
    candidates = (Switch.objects.filter(reservation__in=Reservation.objects.filter(same))
                  .exclude(pk=slave.pk).exclude(mngt_IP=NO_MANAGEMENT_IP).distinct().order_by('id'))
    for candidate in candidates:
        try:
            me = lab_switch(candidate.mngt_IP).vc_topology().me
        except LabSwitchError:
            continue
        if me is not None and me.is_master and me.mac == master.mac:
            return candidate, None
    return None, (f"{slave.mngt_IP} is a VC slave, and its master (chassis {master.chassis}, {master.mac}) isn't a "
                  f"Switch reserved into the same Project: its Switch accounts can only be made there")


def _keep_track_of(account: SwitchAccount, given: Optional[datetime], now: datetime,
                   master_ip: Optional[str] = None, read_only: bool = False) -> None:
    """
    An account just created on the Switch whose row an overlapping sync deleted meanwhile:
    the row is made again, so the account is never forgotten. If that sync has made a new row
    for the same user (another password), it is marked not created: sync_due sends its
    password again, so the Switch ends up with the one BLab shows. `given` is the Switch
    password the account was just given (its changed_at), or None for the row's random one;
    `master_ip` the VC master it was made on, for a VC slave.
    """
    row, made = SwitchAccount.objects.get_or_create(
        switch_id=account.switch_id, user_id=account.user_id,
        defaults={'name': account.name, 'password': account.password, 'created': True, 'tried_at': now,
                  'switch_password_at': given, 'vc_master': master_ip, 'read_only': read_only})
    if not made and (row.password != account.password or row.switch_password_at != given
                     or row.read_only != read_only):
        SwitchAccount.objects.filter(pk=row.pk).update(created=False, error=None)
    logger.warning("Switch account %s on switch %s: its row was gone, made again", account.name, account.switch_id)


def sync_due(now: Optional[datetime] = None, skip: Iterable[int] = ()) -> List[str]:
    """
    The Switch worker's pass: syncs the Switches whose accounts are out of step (a few per
    call), and retries one that failed only RETRY_AFTER its last attempt. A Switch being
    Cleaned up is left to its Cleanup, which syncs it first. An Out of service Switch is left
    alone (#72): its rows wait until it is back in service. So are the Switches in `skip`, whose
    Restore step runs on another thread (#39): that step syncs them. Returns what it did.

    Read-only accounts (#91) are done READ_ONLY_PER_PASS at a time, on at most
    READ_ONLY_SWITCHES_PER_CYCLE Switch whose accounts need nothing else, and one that failed is
    tried again after READ_ONLY_RETRY_AFTER.
    """
    now = now or timezone.now()
    outcomes = []
    read_only_passes = 0
    users = read_only_users()
    cleaning_up = set(PendingCleanup.objects.values_list('switch_id', flat=True)) | set(skip)
    switches = Switch.objects.filter(Switch.in_service()).exclude(id__in=cleaning_up)
    if not users:
        switches = switches.filter(Q(reservation__isnull=False) | Q(accounts__isnull=False)
                                   | Q(guest_accounts__isnull=False)
                                   | Q(quarantines__lifted_at__isnull=True, quarantines__holder__isnull=False))
    for switch in switches.distinct().order_by('id'):
        if len(outcomes) >= SWITCHES_PER_CYCLE:
            break
        accounts = {a.user_id: a for a in switch.accounts.all()}
        wanted = entitled(switch)
        read_only = _read_only_wanted(switch, wanted, users)
        chosen = {**switch_password.current(wanted), **{i: users[i] for i in read_only}}
        work = _work_on(wanted, read_only, accounts, chosen)
        guests_due = guest_logins.due(switch, now)
        if not work.due_now() and not work.read_only_count() and not guests_due:
            continue

        def waits(account: SwitchAccount, after: timedelta) -> bool:
            return bool(account.error and account.tried_at and now - account.tried_at < after)

        if work.due_now():
            # A read-only account that failed is tried again with it, but doesn't hold it up
            users_due = not any(waits(a, RETRY_AFTER) for a in accounts.values()
                                if a.user_id in wanted or not a.read_only)
        else:
            users_due = bool(work.read_only_count()) and read_only_passes < READ_ONLY_SWITCHES_PER_CYCLE and not any(
                waits(a, READ_ONLY_RETRY_AFTER) for a in accounts.values())
            read_only_passes += users_due
        if users_due:
            failures = sync(switch, now, read_only_batch=READ_ONLY_PER_PASS)
        elif guests_due:  # the users' accounts wait: only the Guests' (#96)
            failures = guest_logins.sync(switch, now)
        else:
            continue
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
    for that user's eyes only. `state` is 'ready', 'pending' (BLab is creating it, or giving
    it the user's latest Switch password) or 'failed' (BLab retries; `error` says why).
    `switch_password` says the account opens with the user's Switch password, which is never
    shown: `password` is then None. Only BLab's random passwords are given. `quarantine` says it
    is their Quarantine access (#118) to a Switch an open Quarantine names them in, not reserved.
    """
    reservations = (topology.workable_reservations(user).exclude(switch__mngt_IP=NO_MANAGEMENT_IP)
                    .select_related('switch', 'user'))
    workable = [(r.switch, r.user.username, False) for r in reservations]
    for quarantine in Quarantine.objects.filter(holder=user, lifted_at__isnull=True).select_related('switch'):
        if quarantine_access.holder_id(quarantine.switch) == user.id:
            workable.append((quarantine.switch, user.username, True))
    accounts = {a.switch_id: a for a in SwitchAccount.objects.filter(user=user)}
    chosen = switch_password.current([user.id])
    result = []
    for switch, holder, quarantined in sorted(workable, key=lambda w: w[0].mngt_IP):
        account = accounts.get(switch.id)
        done = account is not None and account.created and not account.read_only and not _stale(account, chosen)
        state = 'ready' if done else 'failed' if account and account.error else 'pending'
        uses_switch_password = user.id in chosen or bool(account and account.switch_password_at)
        result.append({
            'switch': switch.id,
            'mngt_IP': switch.mngt_IP,
            'holder': holder,
            'quarantine': quarantined,
            'name': account.name if account else account_name(user),
            'password': account.password if done and not uses_switch_password else None,
            'switch_password': uses_switch_password,
            'state': state,
            'error': account.error if account and not done else None,
            'vc_master': account.vc_master if done else None,  # made on this VC master, the Switch being a slave
        })
    return result


def account_on(switch: Switch, user: User) -> Optional[Dict]:
    """user's Switch account on this Switch, as accounts_for shows it, or None."""
    return next((a for a in accounts_for(user) if a['switch'] == switch.id), None)
