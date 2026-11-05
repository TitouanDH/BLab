"""
Guest logins (see CONTEXT.md, docs/adr/0004, #96), behind the BLAB_GUEST_LOGINS stage setting
(with BLAB_PROJECTS): a member adds a Guest (a name and a password) to a Project, for someone
without a BLab account. The Guest gets a read-write Switch account, SU allowed, on every Switch
reserved into the Project, a Switch reserved into it later included; it is removed at Release,
when the Switch leaves the Project, or when the Guest is deleted. The member who added the Guest
is accountable for it: what names an account's holder names them.

Any member of the Project may add a Guest; the member who added it, a holder of one of the
Project's Switches, or an admin may delete it. Its name is the Switch login, so it is one AOS
takes, and no BLab user's or other Guest's (a Switch login is one per Switch), nor a lab
manager's nor of the blab-<number> form. Its password follows the rules of a Switch password,
and is stored encrypted the same way (api.switch_password), so that BLab can make a lost account
again (the Login check, #92).

sync() brings a Switch's Guest accounts in step, as api.switch_accounts.sync does for users',
which calls it; sync_due's pass retries what failed. A GuestAccount row stays until its account
is removed from the Switch, so an account left on a Switch is never forgotten. With the setting
off, no Guest is wanted anywhere: any Guest account left is removed.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import InvalidToken
from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import clean_state, projects, switch_password
from .lab_switch import LabSwitchError, check_account_name, lab_switch
from .models import NO_MANAGEMENT_IP, Guest, GuestAccount, Project, Reservation, Switch, SwitchAccount

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=5)  # as api.switch_accounts.RETRY_AFTER
FALLBACK_NAME = re.compile(r'blab-\d+', re.IGNORECASE)  # BLab's names for users AOS wouldn't take as is


def enabled() -> bool:
    """Whether members may add Guests: BLAB_GUEST_LOGINS, with Projects on."""
    return settings.BLAB_GUEST_LOGINS and projects.enabled()


def name_problem(name: str) -> Optional[str]:
    """Why name can't be a new Guest's, or None."""
    try:
        check_account_name(name)
    except ValueError:
        return "A guest's name must be 1 to 63 English letters, digits or ._@+-, and not admin or default."
    if FALLBACK_NAME.fullmatch(name) or name.lower() in {login.lower() for login in clean_state.extras().logins}:
        return f"{name} is reserved: choose another name."
    # A deleted user's or Guest's account still to be removed keeps its name until it is
    if (User.objects.filter(username__iexact=name).exists() or taken_by_guest(name)
            or SwitchAccount.objects.filter(name__iexact=name).exists()):
        return f"{name} is already taken by a BLab user or another guest: choose another name."
    return None


def taken_by_guest(username: str) -> bool:
    """Whether a Guest, or a Guest account still on a Switch, has this name: a new BLab user can't take it."""
    return (Guest.objects.filter(name__iexact=username).exists()
            or GuestAccount.objects.filter(name__iexact=username).exists())


def add(actor: User, project: Project, name: str, password: str) -> Tuple[Guest, List[str]]:
    """
    actor, a member, adds a Guest to the Project; its Switch accounts are made at once on the
    Project's Switches. Returns it and what couldn't be done yet (BLab retries). Raises
    ProjectError if it can't be added, with each broken password rule.
    """
    if not enabled():
        raise projects.NotFound("Guests are off on this stage.")
    if project.archived:
        raise projects.ProjectError(f"{project.name} is archived.")
    name = (name or '').strip()
    password = password or ''
    problem = name_problem(name)
    if problem:
        raise projects.ProjectError(problem)
    broken = switch_password.rules_broken(password, {name}, "the guest's name")
    if broken:
        raise projects.ProjectError(' '.join(broken))
    try:
        with transaction.atomic():
            guest = Guest.objects.create(project=project, name=name, added_by=actor,
                                         encrypted=switch_password.encrypt(password))
    except IntegrityError:
        raise projects.ProjectError(f"{name} is already taken by another guest: choose another name.")
    logger.info("User %s added the guest %s to the Project %s", actor.username, name, project.id)
    return guest, _sync_project(project)


def may_delete(user: User, guest: Guest) -> bool:
    """The member who added it, a holder of one of its Project's Switches, or an admin."""
    return (guest.added_by_id == user.id or user.is_staff
            or Reservation.objects.filter(project_id=guest.project_id, user=user).exists())


def delete(actor: User, project: Project, guest_id) -> List[str]:
    """actor deletes one of the Project's Guests: its Switch accounts go at once. What failed (retried)."""
    guest = Guest.objects.filter(project=project, pk=guest_id).first() if str(guest_id).isdigit() else None
    if guest is None:
        raise projects.NotFound("No such guest in this Project.")
    if not may_delete(actor, guest):
        raise projects.NotAllowed("Only the member who added this guest, a holder of one of the Project's Switches, "
                                  "or an admin may delete it.")
    guest.delete()  # its GuestAccount rows stay, without it, until their accounts are removed
    logger.info("User %s deleted the guest %s of the Project %s", actor.username, guest.name, project.id)
    failures = []
    for switch in Switch.objects.filter(guest_accounts__name=guest.name).distinct().order_by('id'):
        failures += sync(switch)
    return failures


def summaries(user: User, project_ids) -> Dict[int, List[Dict]]:
    """The Guests of these Projects, by Project id, as their members see them."""
    holders = {}
    for project_id, user_id in Reservation.objects.filter(project_id__in=project_ids).values_list('project_id', 'user_id'):
        holders.setdefault(project_id, set()).add(user_id)
    guests = Guest.objects.filter(project_id__in=project_ids)
    names = dict(User.objects.filter(id__in=guests.values('added_by_id')).values_list('id', 'username'))
    result = {}
    for guest in guests.order_by('added_at', 'id'):
        result.setdefault(guest.project_id, []).append({
            'id': guest.id,
            'name': guest.name,
            'added_by': {'id': guest.added_by_id, 'username': names.get(guest.added_by_id)} if guest.added_by_id else None,
            'added_at': guest.added_at,
            'may_delete': (guest.added_by_id == user.id or user.is_staff
                           or user.id in holders.get(guest.project_id, ())),
        })
    return result


def _sync_project(project: Project) -> List[str]:
    failures = []
    for switch in Switch.objects.filter(reservation__project=project).order_by('id'):
        failures += sync(switch)
    return failures


def wanted(switch: Switch) -> Dict[str, Guest]:
    """The Guests that have an account on the Switch now, by name: its Project's, while it is reserved."""
    if not enabled() or switch.mngt_IP == NO_MANAGEMENT_IP:
        return {}
    reservation = Reservation.objects.filter(switch=switch).first()
    if reservation is None or reservation.project_id is None:
        return {}
    return {g.name: g for g in Guest.objects.filter(project_id=reservation.project_id)}


def _out_of_step(switch: Switch) -> Tuple[Dict[str, Guest], List[GuestAccount], List[GuestAccount], List[Guest]]:
    """The wanted Guests; the rows to create (again) and to remove; the wanted Guests without a row."""
    guests = wanted(switch)
    rows = {a.name: a for a in GuestAccount.objects.filter(switch=switch)}
    create = [a for name, a in rows.items() if name in guests and (not a.created or a.guest_id != guests[name].id)]
    remove = [a for name, a in rows.items() if name not in guests]
    new = [g for name, g in guests.items() if name not in rows]
    return guests, create, remove, new


def due(switch: Switch, now: datetime) -> bool:
    """Whether the Switch's Guest accounts are out of step and not waiting RETRY_AFTER a failure (sync_due)."""
    if not enabled() and not GuestAccount.objects.filter(switch=switch).exists():
        return False
    _, create, remove, new = _out_of_step(switch)
    if new:
        return True
    rows = create + remove
    return bool(rows) and not any(a.error and a.tried_at and now - a.tried_at < RETRY_AFTER for a in rows)


def sync(switch: Switch, now: Optional[datetime] = None) -> List[str]:
    """
    Creates the Switch accounts of the Guests the Switch has now (wanted) and removes the others,
    as api.switch_accounts.sync does: the rows are saved first, whatever failed is kept in its row
    for sync_due, and a Switch that can't be reached never raises. Talks to the Switch only if
    something is out of step. Returns what failed, one message per account.
    """
    if not enabled() and not GuestAccount.objects.filter(switch=switch).exists():
        return []  # off, and none left: nothing to lock
    now = now or timezone.now()
    with transaction.atomic():
        switch = Switch.objects.select_for_update().get(pk=switch.pk)
        guests, to_create, to_remove, new = _out_of_step(switch)
        for account in to_create:
            if account.guest_id != guests[account.name].id:  # deleted and added again: its new password
                account.guest, account.added_by_id, account.created = guests[account.name], guests[account.name].added_by_id, False
                account.save(update_fields=['guest', 'added_by', 'created'])
        for guest in new:
            to_create.append(GuestAccount.objects.create(switch=switch, guest=guest, name=guest.name,
                                                         added_by_id=guest.added_by_id))
    if not to_create and not to_remove:
        return []

    failed, passwords, failures = {}, {}, []
    for account in to_create:
        try:
            passwords[account.name] = switch_password.decrypt(guests[account.name].encrypted)
        except InvalidToken:
            failed[account.name] = 'no key of BLAB_SWITCH_PASSWORD_KEY opens its password'
    through = {}
    if switch.mngt_IP == NO_MANAGEMENT_IP:
        failed.update({a.name: 'no management IP' for a in to_remove})
    else:
        try:
            update = lab_switch(switch.mngt_IP).update_accounts(create=passwords, remove=[a.name for a in to_remove])
        except LabSwitchError as e:
            failed.update({a.name: str(e) for a in to_remove + to_create})
        else:
            failed.update(update.failed)
            if update.not_saved:
                failures.append(f"The guest accounts of {switch.mngt_IP} were changed but not saved: {update.not_saved}")
            # A VC slave: an account removed is its master's to remove; one to create must be there
            on_slave = [a for a in to_create if a.name in update.on_slave]
            if on_slave:
                through, waiting = _made_on_master(switch, on_slave)
                failed.update(waiting)

    for account in to_remove + to_create:
        error = failed.get(account.name)
        rows = GuestAccount.objects.filter(pk=account.pk)
        if error is None and account in to_remove:
            rows.delete()
        elif error is None:
            rows.update(created=True, error=None, tried_at=now, vc_master=through.get(account.name))
        else:
            rows.update(error=error, tried_at=now)
            verb = 'remove' if account in to_remove else 'create'
            failures.append(f"Couldn't {verb} the guest account {account.name} on {switch.mngt_IP}: {error}")
    for failure in failures:
        logger.warning(failure)
    return failures


def _made_on_master(slave: Switch, accounts: List[GuestAccount]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """As api.switch_accounts._made_on_master: those made on the VC master, and why the others aren't yet."""
    from .switch_accounts import vc_master  # here: switch_accounts imports this module
    master, why = vc_master(slave)
    made, waiting = {}, {}
    there = set(GuestAccount.objects.filter(switch=master, created=True).values_list('name', flat=True)) if master else set()
    for account in accounts:
        if master is None:
            waiting[account.name] = why
        elif account.name in there:
            made[account.name] = master.mngt_IP
        else:
            waiting[account.name] = f"{slave.mngt_IP} is a VC slave: waiting for the account on its master {master.mngt_IP}"
    return made, waiting


def names(switch: Switch) -> Dict[str, str]:
    """
    The Guest accounts BLab keeps on the Switch, name -> how messages name them: with the member
    accountable for them, as `<name> (guest of <member>)`.
    """
    rows = list(GuestAccount.objects.filter(switch=switch).values_list('name', 'added_by_id'))
    members = dict(User.objects.filter(id__in=[i for _, i in rows if i]).values_list('id', 'username'))
    return {name: f"{name} (guest of {members.get(by, 'a deleted member')})" for name, by in rows}
