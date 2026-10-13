"""
Projects (see CONTEXT.md, docs/adr/0007), behind the BLAB_PROJECTS stage setting: a Project
groups the Switches reserved into it and its members, who join through its Invite link.

Who may work on a reserved Switch, with Projects on: its holder, and every member of the
Project its Reservation is made into. A Reservation without a Project (made with Projects off,
or before the conversion, convert_shares_to_projects) keeps the old rule: its holder and the
users the holder shared their Topology with. Callers ask api.topology, which answers with
this rule when Projects are on. This module also keeps the Projects themselves: create, join,
leave, remove, renew the Invite link, archive.

Joining, leaving and being removed change who may work on the Project's Switches: the Switch
accounts follow at once (api.switch_accounts), and the Switch worker retries what failed.
"""
import secrets
from dataclasses import dataclass
from typing import Dict, List, Optional

from django.conf import settings
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from .models import Project, ProjectMember, ProjectSwitchPosition, Reservation, Switch, SwitchPosition, TopologyShare

NAME_LENGTH = Project._meta.get_field('name').max_length
REFERENCE_LENGTH = Project._meta.get_field('reference').max_length


def enabled() -> bool:
    """Whether this stage has Projects: the BLAB_PROJECTS stage setting."""
    return settings.BLAB_PROJECTS


class ProjectError(Exception):
    """A Project operation refused. The message is meant for the user."""
    status = 400


class NotAllowed(ProjectError):
    status = 403


class NotFound(ProjectError):
    status = 404


def default_name(user: User) -> str:
    """The name of the Project BLab makes for a user: their first Reserve, and the conversion."""
    return f"{user.username}'s lab"[:NAME_LENGTH]


def new_token() -> str:
    return secrets.token_urlsafe(24)


def clean_name(name, reference='') -> tuple:
    """(name, reference) stripped, or ProjectError if they can't be a Project's."""
    if not isinstance(name, str) or not name.strip():
        raise ProjectError("Give the Project a name.")
    if not isinstance(reference, str):
        raise ProjectError("The reference must be text.")
    name, reference = name.strip(), reference.strip()
    if len(name) > NAME_LENGTH:
        raise ProjectError(f"A Project's name is {NAME_LENGTH} characters at most.")
    if len(reference) > REFERENCE_LENGTH:
        raise ProjectError(f"A Project's reference is {REFERENCE_LENGTH} characters at most.")
    return name, reference


def create(owner: User, name: str, reference: str = '') -> Project:
    """A new Project, owned by owner, who is its first member."""
    name, reference = clean_name(name, reference)
    with transaction.atomic():
        project = Project.objects.create(name=name, reference=reference, owner=owner, invite_token=new_token())
        ProjectMember.objects.create(project=project, user=owner)
    return project


def mine(user: User):
    """The Projects user is a member of, archived ones included."""
    return Project.objects.filter(members__user=user)


def is_member(user: User, project: Project) -> bool:
    return ProjectMember.objects.filter(project=project, user=user).exists()


def _check_active(project: Project) -> None:
    if project.archived:
        raise ProjectError(f"{project.name} is archived: choose another Project.")


def _check_owner(actor: User, project: Project, what: str) -> None:
    if project.owner_id != actor.id:
        raise NotAllowed(f"Only the Project's owner may {what}.")


def member_project(user: User, project_id) -> Project:
    """The Project with this id, if user is a member; NotFound otherwise, so nobody learns of others' Projects."""
    project = mine(user).filter(pk=project_id).first() if str(project_id).isdigit() else None
    if project is None:
        raise NotFound("No such Project among yours.")
    return project


def last_used(user: User) -> Optional[Project]:
    """The active Project user last reserved into, else the one they joined last: what Reserve preselects."""
    membership = (ProjectMember.objects.filter(user=user, project__archived_at__isnull=True)
                  .select_related('project')
                  .order_by(F('last_reserved_at').desc(nulls_last=True), '-joined_at', '-id').first())
    return membership.project if membership else None


# --- Reserve: the Project a Reservation is made into ---

@dataclass
class Choice:
    """The Project a Reserve asked for: an existing one (project_id), a new one (new_name), or neither."""
    user: User
    project_id: Optional[int] = None
    new_name: Optional[str] = None
    new_reference: str = ''

    def resolve(self) -> Project:
        """
        The Project to reserve into, inside the Reserve's transaction, once the Switch is known
        to be free: the one chosen (locked, so it can't be archived meanwhile), a new one, or,
        with no choice, the user's last used one, or a new "<username>'s lab" for a first Reserve.
        """
        if self.new_name is not None:
            return create(self.user, self.new_name, self.new_reference)
        project_id = self.project_id
        if project_id is None:
            last = last_used(self.user)
            if last is None:
                return create(self.user, default_name(self.user))
            project_id = last.pk
        project = Project.objects.select_for_update().filter(pk=project_id, members__user=self.user).first()
        if project is None:
            raise NotFound("No such Project among yours.")
        _check_active(project)
        return project


def choice_from(user: User, data) -> Choice:
    """
    The Project choice of a Reserve request: "project" (the id of one of the user's Projects) or
    "new_project" ({"name", "reference"}). Checked now, so a refused one is said before anything
    is reserved; ProjectError otherwise.
    """
    data = data if hasattr(data, 'get') else {}
    new = data.get('new_project')
    if new is not None:
        if not isinstance(new, dict):
            raise ProjectError('"new_project" must be {"name", "reference"}.')
        name, reference = clean_name(new.get('name'), new.get('reference') or '')
        return Choice(user, new_name=name, new_reference=reference)
    project_id = data.get('project')
    if project_id in (None, ''):
        return Choice(user)
    project = member_project(user, project_id)
    _check_active(project)
    return Choice(user, project_id=project.pk)


def mark_used(user: User, project: Project) -> None:
    """Reserve preselects this Project next time."""
    ProjectMember.objects.filter(project=project, user=user).update(last_reserved_at=timezone.now())


# --- Who may work on a reserved Switch ---

def share_owners(user: User) -> set:
    """The holders who shared their Topology with user (the rule before Projects)."""
    return set(TopologyShare.objects.filter(target=user).values_list('owner_id', flat=True))


def workable_reservations(user: User):
    """Every Reservation user may work on: theirs, those of their Projects, and, for one without
    a Project, those of the holders who share their Topology with them."""
    return Reservation.objects.filter(
        Q(user=user) | Q(project__members__user=user)
        | Q(project__isnull=True, user_id__in=share_owners(user))).distinct()


def may_work_on(user: User, reservation: Reservation) -> bool:
    """Whether user may work on this reserved Switch (see the module docstring)."""
    if reservation.user_id == user.id:
        return True
    if reservation.project_id is not None:
        return ProjectMember.objects.filter(project_id=reservation.project_id, user=user).exists()
    return TopologyShare.objects.filter(owner_id=reservation.user_id, target=user).exists()


def users_who_may_work_on(reservation: Reservation) -> set:
    """The ids of the users may_work_on lets work on this reserved Switch."""
    if reservation.project_id is not None:
        members = ProjectMember.objects.filter(project_id=reservation.project_id).values_list('user_id', flat=True)
        return {reservation.user_id, *members}
    return {reservation.user_id,
            *TopologyShare.objects.filter(owner_id=reservation.user_id).values_list('target_id', flat=True)}


def topology_key(reservation: Optional[Reservation]):
    """Which Topology a reserved Switch is drawn on: its Project, or, without one, its holder's."""
    if reservation is None:
        return None
    return ('project', reservation.project_id) if reservation.project_id else ('holder', reservation.user_id)


def check_link(switch_a: Switch, switch_b: Switch) -> None:
    """ProjectError if a Link between these Switches would cross Projects: Connect refuses it."""
    held = {r.switch_id: r for r in Reservation.objects.filter(switch__in=[switch_a, switch_b])}
    a, b = topology_key(held.get(switch_a.id)), topology_key(held.get(switch_b.id))
    if a is None or a != b:
        raise ProjectError("Both Switches must be in the same Project: Links never cross Projects.")


# --- Members ---

def _sync_accounts(project: Project) -> List[str]:
    """Brings the Switch accounts of the Project's Switches in step with its members."""
    from . import switch_accounts  # here: switch_accounts imports api.topology, which imports this module
    failures = []
    for switch in Switch.objects.filter(reservation__project=project).order_by('id'):
        failures += switch_accounts.sync(switch)
    return failures


def preview(token: str) -> Project:
    """The Project an Invite link opens, for its confirm page; NotFound if it opens none (renewed, archived)."""
    project = (Project.objects.select_related('owner').filter(invite_token=token, archived_at__isnull=True).first()
               if token else None)
    if project is None:
        raise NotFound("This Invite link doesn't work: it may have been renewed, or its Project archived. "
                       "Ask the Project's owner for the current one.")
    return project


def join(user: User, token: str) -> tuple:
    """
    Makes user a member of the Project the Invite link opens, and gives them their Switch
    accounts on its Switches. Returns (project, joined, failures): joined is False if they
    already were a member; failures are the Switch accounts that couldn't be made yet.
    """
    project = preview(token)
    try:
        with transaction.atomic():
            # Locked, and read again: the link may have been renewed or the Project archived meanwhile
            if not Project.objects.select_for_update().filter(pk=project.pk, invite_token=token,
                                                              archived_at__isnull=True).exists():
                raise NotFound("This Invite link doesn't work any more: ask the Project's owner for the current one.")
            _, joined = ProjectMember.objects.get_or_create(project=project, user=user)
    except IntegrityError:  # a second click at the same time
        joined = False
    return project, joined, _sync_accounts(project) if joined else []


def invitation(user: User, project: Project) -> Dict:
    """What an Invite link's confirm page shows: the Project, its owner and Switches (model and id only)."""
    switches = Switch.objects.filter(reservation__project=project).order_by('model', 'id')
    return {
        'project': {
            'id': project.id, 'name': project.name, 'reference': project.reference,
            'owner': project.owner.username if project.owner else None,
            'members': project.members.count(),
            'switches': [{'id': s.id, 'model': s.model} for s in switches],
        },
        'member': is_member(user, project),
    }


def _lock(project: Project) -> Project:
    return Project.objects.select_for_update().get(pk=project.pk)


def _holds_in(user_id: int, project: Project) -> bool:
    return Reservation.objects.filter(project=project, user_id=user_id).exists()


def leave(user: User, project: Project) -> List[str]:
    """
    user leaves the Project, and their Switch accounts on its Switches go. The owner can't
    leave (they archive it), nor can a member who still holds one of its Switches.
    """
    if project.owner_id == user.id:
        raise ProjectError("You own this Project: you can't leave it. Archive it once it holds no Switch.")
    with transaction.atomic():
        _lock(project)  # Reserve locks it too: nothing is reserved into it by them meanwhile
        if _holds_in(user.id, project):
            raise ProjectError("You still hold Switches in this Project: Release them first.")
        if not ProjectMember.objects.filter(project=project, user=user).delete()[0]:
            raise NotFound("You are not a member of this Project.")
    return _sync_accounts(project)


def remove(actor: User, project: Project, user_id) -> List[str]:
    """The owner removes a member, whose Switch accounts on the Project's Switches go."""
    _check_owner(actor, project, 'remove members')
    if str(user_id) == str(actor.id):
        raise ProjectError("You own this Project: you can't remove yourself.")
    with transaction.atomic():
        _lock(project)  # Reserve locks it too: nothing is reserved into it by them meanwhile
        member = ProjectMember.objects.filter(project=project, user_id=user_id).select_related('user').first()
        if member is None:
            raise NotFound("This user is not a member of the Project.")
        if _holds_in(member.user_id, project):
            raise ProjectError(f"{member.user.username} still holds Switches in this Project: "
                               "they, or any member, must Release them first.")
        member.delete()
    return _sync_accounts(project)


def renew_invite(actor: User, project: Project) -> Project:
    """The owner gives the Project a new Invite link: the old one stops working."""
    _check_owner(actor, project, 'renew its Invite link')
    if project.archived:
        raise ProjectError("This Project is archived: it has no Invite link.")
    project.invite_token = new_token()
    project.save(update_fields=['invite_token'])
    return project


def archive(actor: User, project: Project) -> Project:
    """The owner archives a Project that holds no Reservation. Its Invite link stops working."""
    _check_owner(actor, project, 'archive it')
    with transaction.atomic():
        # Locked: Reserve locks the Project too, so nothing is reserved into it meanwhile
        project = _lock(project)
        if project.archived:
            return project
        if project.reservations.exists():
            raise ProjectError("This Project still holds Switches: Release them first.")
        project.archived_at = timezone.now()
        project.invite_token = None
        project.save(update_fields=['archived_at', 'invite_token'])
    return project


# --- What the API gives ---

def summaries(user: User, projects) -> List[Dict]:
    """The Projects as user sees them: their members, how many Switches they hold, and the
    Invite link for the owner only."""
    projects = list(projects.annotate(switch_count=Count('reservations', distinct=True))
                    .select_related('owner').order_by('name', 'id'))
    members = {}
    for m in ProjectMember.objects.filter(project__in=projects).select_related('user').order_by('joined_at', 'id'):
        members.setdefault(m.project_id, []).append({'id': m.user_id, 'username': m.user.username,
                                                     'joined_at': m.joined_at})
    return [{
        'id': p.id,
        'name': p.name,
        'reference': p.reference,
        'owner': {'id': p.owner_id, 'username': p.owner.username} if p.owner else None,
        'is_owner': p.owner_id == user.id,
        'created_at': p.created_at,
        'archived_at': p.archived_at,
        'switch_count': p.switch_count,
        'members': members.get(p.id, []),
        'invite_token': p.invite_token if p.owner_id == user.id else None,
    } for p in projects]


def summary(user: User, project: Project) -> Dict:
    return summaries(user, Project.objects.filter(pk=project.pk))[0]


# --- The Topology layout, per Project ---

def layout(project: Project, switch_ids) -> Dict:
    """The saved positions of these Switches on the Project's Topology: {"<switch id>": {"x", "y"}}."""
    return {str(p.switch_id): {'x': p.x, 'y': p.y}
            for p in ProjectSwitchPosition.objects.filter(project=project, switch_id__in=switch_ids)}


def save_layout(project: Project, positions: Dict) -> None:
    """Saves these positions ({switch id: (x, y)}); other Switches keep theirs. Unknown Switches are left out."""
    existing = set(Switch.objects.filter(id__in=positions).values_list('id', flat=True))
    ProjectSwitchPosition.objects.bulk_create(
        [ProjectSwitchPosition(project=project, switch_id=switch_id, x=x, y=y)
         for switch_id, (x, y) in positions.items() if switch_id in existing],
        update_conflicts=True, unique_fields=['project', 'switch'], update_fields=['x', 'y'],
    )


def forget_layout(project: Project) -> None:
    """Re-arrange: forgets every saved position of the Project's Topology."""
    ProjectSwitchPosition.objects.filter(project=project).delete()


# --- The conversion, once, at the maintenance window ---

@dataclass
class Conversion:
    projects_created: int = 0
    reservations_moved: int = 0
    members_added: int = 0
    positions_copied: int = 0


def convert_shares(log=lambda line: None) -> Conversion:
    """
    Turns Topology sharing into Projects (convert_shares_to_projects): each user holding a
    Reservation without a Project gets the Project "<username>'s lab", their Reservations are
    made into it, each user they shared their Topology with becomes a member, and their Topology
    layout is copied over. Safe to run again: only Reservations without a Project are moved, and
    nothing is added twice. Shares and the old layouts are left in place (removing them is a
    later step, docs/adr/0002).

    An active "<username>'s lab" of theirs is used again (one an earlier run made, or their first
    Reserve with Projects on) only if its members are all the holder or users they shared with,
    so nobody gains Switches they couldn't work on before. Otherwise a new one is made. Who may
    work on the converted Switches doesn't change; the Switch worker brings the Switch accounts
    of the rest in step within minutes.
    """
    done = Conversion()
    holders = (User.objects.filter(id__in=Reservation.objects.filter(project__isnull=True).values('user_id'))
               .order_by('id'))
    for holder in holders:
        with transaction.atomic():
            name = default_name(holder)
            allowed = {holder.id, *TopologyShare.objects.filter(owner=holder).values_list('target_id', flat=True)}
            project = next((p for p in Project.objects.filter(owner=holder, name=name, archived_at__isnull=True)
                            .order_by('id')
                            if set(p.members.values_list('user_id', flat=True)) <= allowed), None)
            if project is None:
                project = Project.objects.create(name=name, owner=holder, invite_token=new_token())
                done.projects_created += 1
            moved = Reservation.objects.filter(user=holder, project__isnull=True).update(project=project)
            done.reservations_moved += moved
            added = []
            for user_id in [holder.id, *TopologyShare.objects.filter(owner=holder).order_by('id')
                            .values_list('target_id', flat=True)]:
                if ProjectMember.objects.get_or_create(project=project, user_id=user_id)[1] and user_id != holder.id:
                    added.append(user_id)
            done.members_added += len(added)
            have = set(ProjectSwitchPosition.objects.filter(project=project).values_list('switch_id', flat=True))
            positions = [ProjectSwitchPosition(project=project, switch_id=p.switch_id, x=p.x, y=p.y)
                         for p in SwitchPosition.objects.filter(owner=holder).order_by('id')
                         if p.switch_id not in have]
            ProjectSwitchPosition.objects.bulk_create(positions)
            done.positions_copied += len(positions)
            names = ', '.join(User.objects.filter(id__in=added).order_by('username').values_list('username', flat=True))
            log(f"{holder.username}: {moved} Reservation(s) into \"{project.name}\" (#{project.id})"
                + (f", members added: {names}" if names else '') + f", layout of {len(positions)} Switch(es) copied")
    return done
