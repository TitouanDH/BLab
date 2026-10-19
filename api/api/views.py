import functools
import logging  # Add logging import
import os
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework import status
from django.contrib.auth import authenticate, login as lg , logout as lgout
from django.db import transaction
from django.db.models import Count, Q
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from .models import PendingCleanup, Quarantine, Switch, SwitchEvent, Reservation, Port, User, TopologyShare
from . import links, projects, quarantine, reservations, restore, snapshots, sweep, switch_accounts, switch_password, topology
from .switch_details import Viewer, label
from . import my_lab as my_lab_module
from . import release as releasing
from .inspection import cables_left
from .lab_switch import LabSwitchError
from .permissions import HasEmail, HasEmailOnly
from .serializers import AccountSerializer, SwitchListSerializer, SwitchSerializer, ReservationSerializer, PortSerializer, UserSerializer
from django.shortcuts import get_object_or_404

"""
Features:

- Login: Allows users to authenticate themselves by providing their username and password.
- Signup: Enables users to create new accounts by providing a username and password.
- Logout: Allows authenticated users to log out of their accounts.
- Account: The user's own account; sets the email every account needs before anything else.
- Switch password: Chooses the password of all the user's Switch accounts (with BLAB_PROJECTS on).
- List Users: Allows administrators to retrieve a list of all users registered in the system.
- User Details: Enables users to retrieve details of a specific user account.
- Test Token: Allows users to test the validity of their authentication token.
- Welcome: Provides a welcome message along with a list of available API endpoints.
- List Switches: Enables users to retrieve a list of all switches in the system.
- Delete Switch: Allows administrators to delete a switch from the system.
- Delete Port: Enables users to delete a port from the system.
- List Ports: Allows users to retrieve a list of all ports in the system.
- List Ports by Switch: Enables users to retrieve a list of ports belonging to a specific switch.
- Reserve Switch: Allows users to reserve a switch for their use.
- Renew Reservation: Pushes a Reservation's end date back by a week, at most twice.
- Release Switch: Enables users to release a previously reserved switch.
- List Reservations: Allows users to retrieve a list of all reservations made in the system.
- Connect Ports: Allows users to connect two ports belonging to different switches.
- Disconnect Ports: Enables users to disconnect two previously connected ports.
- Traps: Handles various alerts sent by switches.
- Share Topology: Allows users to share their topology with other users.
- List Shared Topologies: Enables users to view topologies shared with them.
- Topology: Allows users to read their topology, or one shared with them.
- My lab: every Switch the user may work on, grouped by Project, with their Switch accounts.
- Projects (BLAB_PROJECTS on): list, create, join through the Invite link, leave, remove a
  member, renew the Invite link, archive; a Project's Topology and its layout; its Snapshots,
  Save, and Restore.
"""


# Configure logging to save logs to a file
LOG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(filename=os.path.join(LOG_DIR, 'api_views.log'), level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# HTTP status for each way a Link operation can be refused or fail
LINK_ERROR_STATUS = {
    links.SamePort: status.HTTP_400_BAD_REQUEST,
    links.PortsBusy: status.HTTP_400_BAD_REQUEST,
    links.NotLinked: status.HTTP_400_BAD_REQUEST,
    links.NoFreeSvlan: status.HTTP_409_CONFLICT,
    links.BackboneFailure: status.HTTP_422_UNPROCESSABLE_ENTITY,
    links.NotReserved: status.HTTP_409_CONFLICT,
}


def user_has_switch_access(user, switch):
    """Whether user may work on this switch: the rule is release.may_release."""
    reservation = Reservation.objects.filter(switch=switch).first()
    return reservation is not None and releasing.may_release(user, reservation)


# API endpoint for user login
@csrf_exempt
@api_view(['POST'])
def login(request):
    """
    User login endpoint.
    Expects JSON data with 'username' and 'password' fields.

    Request Payload:
    {
        "username": "<username>",
        "password": "<password>"
    }

    Expected Response Payload (Successful):
    {
        "token": "<generated_token>",
        "user": <the account, as GET account/ gives it>,
        "is_staff": boolean
    }

    An account without an email can log in, but must set it (account/) before anything else,
    and then, with BLAB_PROJECTS on, choose its Switch password (switch_password/).

    Expected Response Payload (Failed):
    {
        "detail": "Invalid credentials."
    }
    """
    username = request.data.get('username')
    password = request.data.get('password')
    # Authenticate user
    user = authenticate(request, username=username, password=password)
    if user is None:
        logger.warning(f"Login failed for username: {username}")
        return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)
    
    lg(request, user)

    # Generate or retrieve token
    token, created = Token.objects.get_or_create(user=user)
    logger.info(f"User {username} logged in successfully.")
    return Response({
        "token": token.key, 
        "user": account_data(user),
        "is_staff": user.is_staff
    }, status=status.HTTP_202_ACCEPTED)


# API endpoint for user signup
@csrf_exempt
@api_view(['POST'])
def signup(request):
    """
    User signup endpoint.
    Enables users to create new accounts by providing a username, an email and a password.

    Request Payload:
    {
        "username": "new_user",
        "email": "first.last@example.com",
        "password": "Password123"
    }

    Expected Response Payload (Successful):
    {
        "token": "<generated_token>",
        "user": {
            "id": "<user_id>",
            "username": "new_user",
            "email": "first.last@example.com"
        }
    }

    Expected Response Payload (Failed): the invalid fields and their errors
    {
        "username": ["<error_message>"]
    }

    Only the username, email and password are read; any other field (is_staff, ...) is ignored.
    """
    serializer = AccountSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        token, created = Token.objects.get_or_create(user=user)
        logger.info(f"User {user.username} signed up successfully.")
        return Response({"token": token.key, "user": serializer.data},  status=status.HTTP_201_CREATED)
    logger.warning(f"Signup failed with errors: {serializer.errors}")
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# API endpoint for user logout
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])  # no HasEmail: logging out needs no email
def logout(request):
    """
    User logout endpoint.
    Allows authenticated users to log out of their accounts.
    """
    authorization_header = request.headers.get('Authorization')
    if authorization_header:
        try:
            lgout(request)
            token_key = authorization_header.split(' ')[1]
            token = Token.objects.get(key=token_key)
            token.delete()
            response = Response({"detail": "Logout successful."}, status=status.HTTP_200_OK)
            response.delete_cookie('sessionid')
            logger.info(f"User {request.user.username} logged out successfully.")
            return response
        except Token.DoesNotExist:
            logger.warning(f"Invalid token provided for logout.")
            return Response({"detail": "Invalid token."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        logger.warning("Authorization header not provided for logout.")
        return Response({"detail": "Authorization header not provided."}, status=status.HTTP_400_BAD_REQUEST)


def account_data(user):
    """
    The user's own account: AccountSerializer's fields, the name of their Switch accounts, and
    whether this stage has users choose a Switch password (BLAB_PROJECTS) and whether they have.
    """
    return {**AccountSerializer(user).data,
            "switch_login": switch_accounts.account_name(user),
            "switch_password_enabled": switch_password.enabled(),
            "switch_password_set": switch_password.enabled() and switch_password.has_one(user)}


# API endpoint for the user's own account
@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])  # no HasEmail: this is where the email gets set
def account(request):
    """
    The logged-in user's own account: GET it, or POST {"email": "..."} to set or change the email.

    Response Payload: { "id": ..., "username": ..., "email": "<empty until set>", "switch_login": ...,
                        "switch_password_enabled": bool, "switch_password_set": bool }
    Only the email can change here (the Switch password through switch_password/); a refused
    email answers 400 with {"email": ["<why>"]}.
    """
    if request.method == 'POST':
        serializer = AccountSerializer(request.user, data={'email': request.data.get('email', '')}, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        logger.info(f"User {request.user.username} set their email.")
    return Response(account_data(request.user), status=status.HTTP_200_OK)


# API endpoint to choose the Switch password
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmailOnly])  # this is where the Switch password gets chosen
def choose_switch_password(request):
    """
    Chooses the caller's Switch password (see CONTEXT.md), with BLAB_PROJECTS on (404 when off):
    the password of all their Switch accounts, pushed at once to the ones they have.

    Request Payload: {"password": "<password>"}
    Responses: 200 {"detail": ..., "failed": [<Switch accounts it couldn't reach yet, as GET
    switch_accounts/ gives them; BLab keeps trying>]}; 400 {"password": ["<each rule it breaks>"]}.
    The password is never given back.
    """
    if not switch_password.enabled():
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    password = request.data.get('password')
    if not isinstance(password, str):
        return Response({"password": ["Choose a password."]}, status=status.HTTP_400_BAD_REQUEST)
    try:
        failed = switch_password.choose(request.user, password)
    except ValueError as e:
        return Response({"password": e.args[0]}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"detail": "Switch password saved.", "failed": failed}, status=status.HTTP_200_OK)


# API endpoint to list all users
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_user(request):
    """
    List Users endpoint.
    Allows users to retrieve a list of all users registered in the system for sharing purposes.
    """
    users = User.objects.all()
    serializer = UserSerializer(instance=users, many=True)
    return Response({"users": serializer.data}, status=status.HTTP_200_OK)


# API endpoint to get details of a specific user
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_user_by_id(request, user_id):
    """
    User Details endpoint.
    Enables users to retrieve details of a specific user account.
    """
    user = get_object_or_404(User, pk=user_id)
    serializer = UserSerializer(user)
    return Response(serializer.data, status=status.HTTP_200_OK)


# API endpoint to test authentication token
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def test_token(request):
    """
    Test Token endpoint.
    Allows users to test the validity of their authentication token.
    """
    return Response("This is {}'s Auth Token".format(request.user.username), status=status.HTTP_200_OK)


# API endpoint to display available functionalities
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def welcome(request):
    """
    Welcome endpoint.
    Provides a welcome message along with a list of available API endpoints.
    """

    api_urls = {
        "infos": "This is the list of the differents functionalities",
        "urls": [
            "/login",
            "/logout",
            "/account",
            "/signup",
            "/token",
            "/del_switch",
            "/list_switch",
            "/del_port",
            "/list_port",
            "/list_port_by_switch/<int:switch_id>",
            "/reserve",
            "/release",
            "/list_reservation",
            "/connect",
            "/disconnect",
            "/traps",
            "/share_topology",
            "/list_shared_topologies",
            "/unshare_topology/<int:share_id>",
            "/topology/<int:owner_id>",
            "/topology/<int:owner_id>/layout",
            "/lab_status",
            "/release_check/<int:switch_id>",
            "/recheck",
            "/renew",
            "/my_lab",
            "/projects",
            "/projects/<int:project_id>",
            "/projects/<int:project_id>/topology",
            "/projects/<int:project_id>/layout",
            "/projects/<int:project_id>/invite",
            "/projects/<int:project_id>/leave",
            "/projects/<int:project_id>/members/<int:user_id>",
            "/projects/<int:project_id>/archive",
            "/join/<token>"
        ]
    }
    return Response(api_urls)



# API endpoint to list all switches
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_switch(request):
    """
    List Switches endpoint: every Switch in the lab, with "cabled_ports" (its Ports paired with
    a UNI) and "unavailable". A Switch's management IP, console and hardware only come for the
    users who may work on it, and for admins (api.switch_details).
    """
    serializer = SwitchListSerializer(instance=Switch.objects.annotate(cabled_ports=Count('port')), many=True)
    # Why a Switch can't be reserved now (Out of service, Quarantine, Cleanup), or None
    unavailable = quarantine.unavailable()
    viewer = Viewer(request.user)
    # cabled_ports comes for everyone: it helps choose a Switch and says nothing about how to reach it
    switches = [viewer.redact_switch({**data, 'unavailable': viewer.redact_reason(unavailable.get(data['id']))})
                for data in serializer.data]
    return Response({"switchs": switches}, status=status.HTTP_200_OK)


HISTORY_LENGTH = 10  # events per Switch on the Lab status page


def serialize_switch_event(event, viewer):
    """One entry of a Switch's history, as the Lab status page shows it to viewer."""
    return {'kind': event.kind, 'at': event.at, 'ok': event.ok, 'reasons': viewer.redact_all(event.reasons),
            'warnings': viewer.redact_all(event.warnings), 'user': event.user.username if event.user else None}


def serialize_quarantine(quarantine, viewer):
    if quarantine is None:
        return None
    return {'holder': quarantine.holder.username if quarantine.holder else None,
            'holder_id': quarantine.holder_id,
            'opened_at': quarantine.opened_at, 'reasons': viewer.redact_all(quarantine.reasons)}


# The Lab status page: every Switch, who holds it, and what its last Inspection found
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def lab_status(request):
    """
    Every Switch with its holder (username, and holder_id), Reservation end date, last
    Inspection, open Quarantine, Out of service, whether it is being Cleaned up, and recent
    history (newest first); and the report of the last Sweep, only if it found something
    wrong. Every logged-in user sees the whole lab, but a Switch's management IP only comes for
    the users who may work on it, and for admins (api.switch_details): for the others BLab's
    texts name it by its label instead.
    """
    viewer = Viewer(request.user)
    held = {r.switch_id: r for r in Reservation.objects.select_related('user')}
    quarantines = {q.switch_id: q for q in Quarantine.objects.filter(lifted_at__isnull=True).select_related('holder')}
    cleaning_up = set(PendingCleanup.objects.values_list('switch_id', flat=True))
    switches = []
    for switch in Switch.objects.order_by('model', 'id'):
        reservation = held.get(switch.id)
        history = list(switch.events.select_related('user')[:HISTORY_LENGTH])
        inspection = next((e for e in history if e.kind == SwitchEvent.INSPECTION), None)
        if inspection is None and len(history) == HISTORY_LENGTH:  # older than the recent history
            inspection = switch.last_inspection()
        switches.append(viewer.redact_switch({
            'id': switch.id,
            'mngt_IP': switch.mngt_IP,
            'model': switch.model,
            'holder': reservation.user.username if reservation else None,
            'holder_id': reservation.user_id if reservation else None,
            'end_date': reservation.end_date if reservation else None,
            'renewals_left': reservations.renewals_left(reservation) if reservation else None,
            'admin_exception': reservation.admin_exception if reservation else None,
            'inspection': serialize_switch_event(inspection, viewer) if inspection else None,
            'quarantine': serialize_quarantine(quarantines.get(switch.id), viewer),
            'out_of_service': ({'reason': viewer.redact(switch.out_of_service_reason),
                                'since': switch.out_of_service_since}
                               if switch.out_of_service else None),
            'cleaning_up': switch.id in cleaning_up,
            'history': [serialize_switch_event(e, viewer) for e in history],
        }))
    report = sweep.last_report()
    report = report and {'started_at': report.started_at, 'finished_at': report.finished_at,
                         'summary': viewer.redact(report.summary), 'problems': viewer.redact_all(report.problems)}
    return Response({'switches': switches, 'sweep': report}, status=status.HTTP_200_OK)


# API endpoint to delete a switch (admin only)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAdminUser, HasEmail])
def del_switch(request):
    """
    Delete Switch endpoint.
    Allows administrators to delete a switch from the system.

    Request Payload:
    {
        "id": "<switch_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Success"
    }
    """
    switch = get_object_or_404(Switch, id=request.data["id"])
    switch.delete()
    logger.info(f"Switch {switch.id} deleted successfully.")
    return Response({"detail": "Success"}, status=status.HTTP_200_OK)


# API endpoint to delete a port (admin only)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def del_port(request):
    """
    Delete Port endpoint.
    Enables users to delete a port from the system.

    Request Payload:
    {
        "id": "<port_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Success"
    }
    """

    port = get_object_or_404(Port, id=request.data["id"])
    port.delete()
    logger.info(f"Port {port.id} deleted successfully.")
    return Response({"detail": "Success"}, status=status.HTTP_200_OK)


# API endpoint to list all ports
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_port(request):
    """
    List Ports endpoint.
    Allows users to retrieve a list of all ports in the system.
    """
    port = Port.objects.all()
    serializer = PortSerializer(instance=port, many=True)
    return Response({"ports": serializer.data}, status=status.HTTP_200_OK)


# API endpoint to list ports by switch
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_port_by_switch(request, switch_id):
    """
    List Ports by Switch endpoint.
    Enables users to retrieve a list of ports belonging to a specific switch.
    """
    ports = Port.objects.filter(switch=switch_id)
    serializer = PortSerializer(ports, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


# API endpoint to reserve a switch
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def reserve(request):
    """
    Reserve Switch endpoint.
    Allows users to reserve a switch for their use.
    No more admin force reservation - only available switches can be reserved.

    Request Payload: {"switch": "<switch_id>", "end_date": "<ISO 8601>"}, and with Projects on
    (BLAB_PROJECTS) the Project to reserve into: "project": <id of one of the caller's Projects>,
    or "new_project": {"name", "reference"}. With neither, the caller's last used Project, or,
    for their first Reserve, a new one named "<username>'s lab" (api.projects). The caller stays
    the holder.

    end_date is required, in the future, and at most 14 days from now (api.reservations).
    An admin may set any end date: beyond the limit, the Reservation is an admin exception.
    BLab then creates the Switch accounts of everyone who may work on the Switch (the holder,
    and the other members of the Project, or with Projects off whoever the holder shares their
    Topology with; api.switch_accounts); if that fails, the Reservation stands and BLab retries.
    Responses: 201 with "detail", "end_date", "admin_exception", "switch_account" (the
    caller's login on the Switch, as GET switch_accounts/ gives it) and, with Projects on,
    "project" ({"id", "name"}); 400 if refused, 404 for a Project that isn't the caller's.
    """
    user = request.user
    switch_id = request.data.get('switch')
    end_date = reservations.parse_end_date(request.data.get('end_date'))
    try:
        admin_exception = reservations.check_end_date(end_date, user, timezone.now())
    except reservations.LimitError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    choice = None
    if projects.enabled():
        try:
            choice = projects.choice_from(user, request.data)
        except projects.ProjectError as e:
            return Response({"detail": str(e)}, status=e.status)

    # A Switch has at most one Reservation: the row lock makes check-then-create atomic
    with transaction.atomic():
        switch = get_object_or_404(Switch.objects.select_for_update(), id=switch_id)
        holder = Reservation.objects.filter(switch=switch).values_list('user_id', flat=True).first()
        if holder is not None:
            logger.warning(f"User {user.username} attempted to reserve switch {switch_id}, which is already reserved.")
            message = "You have already reserved this switch." if holder == user.id else "This switch is already reserved."
            return Response({"warning": message}, status=status.HTTP_400_BAD_REQUEST)
        viewer = Viewer(user)
        unavailable = viewer.redact_reason(quarantine.unavailable().get(switch.id))
        if unavailable:
            return Response({"detail": f"This Switch can't be reserved. {unavailable['reason']}"},
                            status=status.HTTP_400_BAD_REQUEST)
        refused = quarantine.refusal_while_named(user, viewer.redact)
        if refused:
            return Response({"detail": refused}, status=status.HTTP_403_FORBIDDEN)
        project = None
        if choice is not None:
            try:
                project = choice.resolve()
            except projects.ProjectError as e:
                return Response({"detail": str(e)}, status=e.status)
        Reservation.objects.create(switch=switch, user=user, end_date=end_date, admin_exception=admin_exception,
                                   project=project)
        if project is not None:
            projects.mark_used(user, project)

    banner_updated = switch.changeBanner()
    switch_accounts.sync(switch)
    reserved = {"end_date": end_date, "admin_exception": admin_exception,
                "switch_account": switch_accounts.account_on(switch, user)}
    if project is not None:
        reserved["project"] = {"id": project.id, "name": project.name}
    if admin_exception:
        logger.info(f"Admin {user.username} reserved switch {switch_id} until {end_date}, beyond the limit.")
    if banner_updated:
        logger.info(f"User {user.username} reserved switch {switch_id} successfully.")
        return Response({"detail": "Reservation successful.", **reserved}, status=status.HTTP_201_CREATED)
    return Response({"detail": "Reservation successful, but failed to update the switch banner.", **reserved},
                    status=status.HTTP_201_CREATED)


# API endpoint to Renew a Reservation
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def renew(request):
    """
    Renewal: pushes the end date of the Switch's Reservation back by 7 days, at most twice.
    Whoever may work on the Switch may Renew (topology.may_work_on).

    Request Payload: {"switch": "<switch_id>"}

    Responses: 200 with "detail", "end_date" and "renewals_left"; 400 if the Switch isn't
    reserved or the Reservation can't be Renewed (no Renewals left, admin exception,
    expired); 403 if the user may not Renew it.
    """
    switch = get_object_or_404(Switch, id=request.data.get('switch'))
    reservation = Reservation.objects.filter(switch=switch).first()
    if reservation is None:
        return Response({"detail": "This switch is not reserved."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        reservation = reservations.renew(reservation, request.user)
    except reservations.NotAllowed as e:
        logger.warning(f"User {request.user.username} attempted to renew switch {switch.id} without access.")
        return Response({"detail": str(e)}, status=status.HTTP_403_FORBIDDEN)
    except reservations.LimitError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    left = reservations.renewals_left(reservation)
    logger.info(f"User {request.user.username} renewed switch {switch.id} until {reservation.end_date}.")
    return Response({"detail": f"Renewed for another {reservations.RENEWAL.days} days. Renewals left: {left}.",
                     "end_date": reservation.end_date, "renewals_left": left}, status=status.HTTP_200_OK)


# API endpoint to release a switch
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def release(request):
    """
    Release Switch endpoint: translates between HTTP and release.release().
    Whoever may work on the Switch may release it (topology.may_work_on).

    Every Release Cleans up: the Switch worker reloads the Switch, Inspects it, and Quarantines
    it in the holder's name if it isn't clean.

    Request Payload:
    {
        "switch": "<switch_id>"
    }

    Responses: 200 with a "detail" message, 400 if the switch isn't reserved, 403 if the user
    may not release it, and 422 if a Link can't be torn down (the switch then stays reserved).
    """
    user = request.user
    switch = get_object_or_404(Switch, id=request.data.get('switch'))

    reservation = Reservation.objects.filter(switch=switch).first()
    try:
        if reservation is None:
            raise releasing.AlreadyReleased()
        result = releasing.release(reservation, user)
    except releasing.AlreadyReleased:
        return Response({"detail": "This switch is not reserved."}, status=status.HTTP_400_BAD_REQUEST)
    except releasing.NotAllowed:
        logger.warning(f"User {user.username} attempted to release switch {switch.id} without access.")
        return Response({"detail": "You don't have access to this switch."}, status=status.HTTP_403_FORBIDDEN)

    if not result.released:
        return Response({"detail": "The switch is still reserved: some links couldn't be disconnected. "
                                   + " ".join(result.failures)},
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return Response({"detail": "Released. BLab is now Cleaning the Switch up: it restores the init config, "
                               "reloads it, then Inspects it."}, status=status.HTTP_200_OK)


# Before a Release: the cables to unplug, or the Switch is Quarantined in the holder's name
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def release_check(request, switch_id):
    """
    The ports of a reserved Switch whose link is up and that would count as Unwanted cables
    once it is released. Only reads the Switch. Whoever may release it may ask.

    Response: {"unwanted_cables": ["1/1/5", ...]}, or {"unwanted_cables": null, "detail": why}
    when the Switch can't be read.
    """
    switch = get_object_or_404(Switch, id=switch_id)
    if not user_has_switch_access(request.user, switch):
        return Response({"detail": "You don't have access to this switch."}, status=status.HTTP_403_FORBIDDEN)
    try:
        return Response({"unwanted_cables": cables_left(switch)}, status=status.HTTP_200_OK)
    except LabSwitchError as e:
        logger.warning("Release check of %s: %s", switch.mngt_IP, e)
        return Response({"unwanted_cables": None, "detail": "BLab couldn't read the Switch's ports."},
                        status=status.HTTP_200_OK)


# Re-check: an Inspection that lifts a Quarantine if the Switch is clean. Anyone may ask.
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def recheck(request):
    """
    Request Payload: {"switch": "<switch_id>"}

    Responses: 200 with {"clean", "reasons", "detail"}, 400 if the Switch isn't in Quarantine
    or is being Cleaned up.
    """
    switch = get_object_or_404(Switch, id=request.data.get('switch'))
    try:
        event = quarantine.recheck(switch, request.user)
    except quarantine.NotQuarantined:
        return Response({"detail": "This Switch is not in Quarantine."}, status=status.HTTP_400_BAD_REQUEST)
    except quarantine.CleanupInProgress:
        return Response({"detail": "This Switch is being Cleaned up: BLab Inspects it itself once done."},
                        status=status.HTTP_400_BAD_REQUEST)
    # Anyone may ask: the reasons name the Switch by its label unless the caller may see its IP
    reasons = Viewer(request.user).redact_all(event.reasons)
    if event.ok:
        detail = "Clean: the Quarantine is lifted."
    else:
        detail = "Still not clean: " + "; ".join(reasons)
    return Response({"clean": event.ok, "reasons": reasons, "detail": detail}, status=status.HTTP_200_OK)



# API endpoint to list all reservations
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_reservation(request):
    """
    List Reservations endpoint: every Reservation in the lab. Each row also carries the
    holder's "username", and "may_work": whether the caller may Renew or Release it
    (topology.may_work_on). With Projects on, a row also has "project" and "project_name" for
    the Reservations the caller may work on (null for the others).
    """
    all_reservations = list(Reservation.objects.select_related('user', 'project'))
    serializer = ReservationSerializer(all_reservations, many=True)
    workable = set(topology.workable_reservations(request.user).values_list('id', flat=True))
    rows = []
    for data, reservation in zip(serializer.data, all_reservations):
        row = {**data, 'username': reservation.user.username, 'may_work': reservation.id in workable}
        if projects.enabled():
            mine = reservation.id in workable and reservation.project is not None
            row.update(project=reservation.project_id if mine else None,
                       project_name=reservation.project.name if mine else None)
        rows.append(row)
    return Response(rows, status=status.HTTP_200_OK)


# API endpoint to connect two ports
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def connect(request):
    """
    Connect Ports endpoint.
    Allows users to connect two ports belonging to different switches, or a Switch's port to a
    free Tool port (api.tools): the user must be able to work on every Switch end, and a Tool
    end belongs to no Project, so only the Switch ends must share one. Two Tools are refused.

    Request Payload:
    {
        "portA": "<port_id>",
        "portB": "<port_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Ports connected successfully with svlan <svlan>"
    }
    """
    portA = get_object_or_404(Port, id=request.data.get('portA'))
    portB = get_object_or_404(Port, id=request.data.get('portB'))

    user = request.user
    switchA = portA.switch
    switchB = portB.switch

    switch_ends = [s for s in (switchA, switchB) if not s.is_tool]
    if not switch_ends:
        return Response({"detail": "A Link joins a Tool to a Switch, not to another Tool."},
                        status=status.HTTP_400_BAD_REQUEST)
    # Check if user has access to both switches (owns or shared); a Tool is everyone's
    if not all(user_has_switch_access(user, s) for s in switch_ends):
        logger.warning(f"User {user.username} attempted to connect ports on switches they don't have access to.")
        return Response({"detail": "You don't have access to one or both switches."}, status=status.HTTP_403_FORBIDDEN)
    if projects.enabled() and len(switch_ends) == 2:
        try:
            projects.check_link(switchA, switchB)
        except projects.ProjectError as e:
            logger.warning(f"User {user.username} tried to connect ports {portA.id} and {portB.id} across Projects.")
            return Response({"detail": str(e)}, status=e.status)

    try:
        # Checked again as the Link is recorded: a Release may have ended a Reservation since.
        link = links.connect(portA, portB, may_work_on=lambda reservation: topology.may_work_on(user, reservation))
    except links.LinkError as e:
        logger.warning(f"User {user.username} could not connect ports {portA.id} and {portB.id}: {e}")
        return Response({"detail": str(e)}, status=LINK_ERROR_STATUS.get(type(e), status.HTTP_422_UNPROCESSABLE_ENTITY))
    logger.info(f"Ports {portA.id} and {portB.id} connected successfully with svlan {link.svlan}.")
    return Response({"detail": "Ports connected successfully with svlan {}".format(link.svlan)}, status=status.HTTP_200_OK)


# API endpoint to disconnect two ports
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def disconnect(request):
    """
    Disconnect Ports endpoint.
    Asks for the Link between two ports to be torn down and returns at once (202): the link
    worker does it. Asking again for a Link whose teardown failed makes it try again now.

    Request Payload:
    {
        "portA": "<port_id>",
        "portB": "<port_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Disconnecting the ports."
    }
    """
    portA_id = request.data.get('portA')
    portB_id = request.data.get('portB')

    try:
        portA = Port.objects.get(id=portA_id)
        portB = Port.objects.get(id=portB_id)
    except Port.DoesNotExist:
        return Response({"detail": "One or both ports do not exist."}, status=status.HTTP_404_NOT_FOUND)

    user = request.user
    switchA = portA.switch
    switchB = portB.switch

    # A Link belongs to the Topology of each of its ends, so access to either end is enough,
    # as for a Release, which tears down every Link of the Switch whatever is at the other end
    if not (user_has_switch_access(user, switchA) or user_has_switch_access(user, switchB)):
        logger.warning(f"User {user.username} attempted to disconnect ports on switches they don't have access to.")
        return Response({"detail": "You don't have access to either switch."}, status=status.HTTP_403_FORBIDDEN)

    try:
        links.request_disconnect(links.link_between(portA, portB))
    except links.LinkError as e:
        logger.warning(f"User {user.username} could not disconnect ports {portA.id} and {portB.id}: {e}")
        return Response({"detail": str(e)}, status=LINK_ERROR_STATUS.get(type(e), status.HTTP_422_UNPROCESSABLE_ENTITY))
    logger.info(f"User {user.username} asked to disconnect ports {portA.id} and {portB.id}.")
    return Response({"detail": "Disconnecting the ports."}, status=status.HTTP_202_ACCEPTED)


def with_account_failures(detail: str, owner, done: str) -> str:
    """Syncs the Switch accounts of owner's Topology, and adds to detail whether some couldn't be."""
    if switch_accounts.sync_topology(owner):
        detail += f" Some Switch accounts couldn't be {done} yet; BLab keeps trying."
    return detail


# With Projects on, Topology sharing is replaced by Projects (docs/adr/0007)
SHARING_REPLACED = {"detail": "Topology sharing is replaced by Projects: invite people to your Project instead."}


# API endpoint to share topology with another user
@api_view(['POST'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def share_topology(request):
    """
    Partage la topologie de l'utilisateur courant avec un autre utilisateur.
    Payload: { "target_username": "bob" }
    """
    if projects.enabled():
        return Response(SHARING_REPLACED, status=status.HTTP_404_NOT_FOUND)
    target_username = request.data.get('target_username')
    if not target_username:
        return Response({"detail": "Target username required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        target_user = User.objects.get(username=target_username)
        if TopologyShare.objects.filter(owner=request.user, target=target_user).exists():
            return Response({"detail": "Topology already shared with this user."}, status=status.HTTP_409_CONFLICT)
        TopologyShare.objects.create(owner=request.user, target=target_user)
    except User.DoesNotExist:
        return Response({"detail": "Target user does not exist."}, status=status.HTTP_404_NOT_FOUND)
    # They may now work on every Switch of the Topology: each gets their Switch account
    return Response({"detail": with_account_failures("Topology shared successfully.", request.user, 'created')},
                    status=status.HTTP_201_CREATED)


# API endpoint to list topologies shared with the user
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_shared_topologies(request):
    """
    Liste les topologies partagées avec l'utilisateur courant et celles qu'il a partagées.
    """
    if projects.enabled():
        return Response(SHARING_REPLACED, status=status.HTTP_404_NOT_FOUND)
    # Topologies shared WITH me (where I'm the target)
    shared_with_me = TopologyShare.objects.filter(target=request.user)
    shared_with_me_data = [{
        "id": s.id,
        "owner_id": s.owner.id, 
        "owner_username": s.owner.username, 
        "shared_at": s.created_at,
        "direction": "received"
    } for s in shared_with_me]
    
    # Topologies I shared (where I'm the owner)
    shared_by_me = TopologyShare.objects.filter(owner=request.user)
    shared_by_me_data = [{
        "id": s.id,
        "target_id": s.target.id,
        "target_username": s.target.username,
        "shared_at": s.created_at,
        "direction": "shared"
    } for s in shared_by_me]
    
    return Response({
        "shared_with_me": shared_with_me_data,
        "shared_by_me": shared_by_me_data
    }, status=status.HTTP_200_OK)


# API endpoint to unshare a topology
@api_view(['DELETE'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def unshare_topology(request, share_id):
    """
    Supprime un partage de topologie.
    L'utilisateur peut supprimer un partage qu'il a créé ou dont il est la cible.
    """
    if projects.enabled():
        return Response(SHARING_REPLACED, status=status.HTTP_404_NOT_FOUND)
    try:
        # Allow deletion if user is either owner or target of the share
        share = TopologyShare.objects.filter(
            id=share_id
        ).filter(
            Q(owner=request.user) | Q(target=request.user)
        ).first()
        
        if not share:
            return Response({"detail": "Share not found or permission denied."}, status=status.HTTP_404_NOT_FOUND)
            
        owner = share.owner
        share.delete()
    except Exception as e:
        return Response({"detail": "Error unsharing topology."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    # They may no longer work on the Topology's Switches: their Switch accounts go
    return Response({"detail": with_account_failures("Topology unshared successfully.", owner, 'removed')},
                    status=status.HTTP_200_OK)


# API endpoint to read a user's Topology
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def get_topology(request, owner_id):
    """
    A user's Topology: their Switches, those Switches' Ports, and every Link with an end
    on them (see api.topology). For the owner, or, with Projects off, a user the owner shares
    it with (with Projects on, a Project's Topology is projects/<id>/topology/).
    "may_work" says whether the caller may connect, disconnect and release in it.
    """
    owner = get_object_or_404(User, id=owner_id)
    if not topology.may_see(request.user, owner.id):
        return Response({"detail": "This topology is not shared with you."}, status=status.HTTP_403_FORBIDDEN)
    return Response(dict(topology.read(owner, Viewer(request.user)),
                         may_work=topology.may_work(request.user, owner.id)),
                    status=status.HTTP_200_OK)


# API endpoint to arrange a user's Topology: where its Switches are drawn
@api_view(['PUT', 'DELETE'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def topology_layout(request, owner_id):
    """
    The Topology layout, read with the Topology ("layout" in topology/<owner_id>/), the same
    for everyone viewing it. For whoever may work on the Topology.

    PUT {"positions": {"<switch id>": {"x": number, "y": number}}} saves these Switches'
    positions; the others keep theirs. DELETE forgets them all (Re-arrange).
    """
    owner = get_object_or_404(User, id=owner_id)
    if not topology.may_work(request.user, owner.id):
        return Response({"detail": "This topology is not shared with you."}, status=status.HTTP_403_FORBIDDEN)
    if request.method == 'DELETE':
        topology.forget_layout(owner)
        return Response({"detail": "Layout forgotten."}, status=status.HTTP_200_OK)
    try:
        positions = topology.parse_positions(request.data.get('positions') if isinstance(request.data, dict) else None)
    except ValueError as error:
        return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
    topology.save_layout(owner, positions)
    return Response({"detail": "Layout saved."}, status=status.HTTP_200_OK)


# The caller's Switch accounts, with their passwords: only ever the caller's own
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def list_switch_accounts(request):
    """
    The caller's Switch accounts (see CONTEXT.md), on every Switch they may work on now: the
    ones they hold and those of the Topologies shared with them. Users log in to those
    Switches by SSH with "name" and "password"; `admin` is BLab's.

    Response: {"switch_accounts": [{"switch", "mngt_IP", "holder", "name", "password",
    "switch_password", "state", "error"}]}, where state is "ready" (password set), "pending" or
    "failed" (BLab retries; error says why). The password is only given once the account is
    ready, and never when switch_password is true: the account opens with the Switch password
    the user chose (switch_password/), which is never shown again.
    """
    return Response({"switch_accounts": switch_accounts.accounts_for(request.user)}, status=status.HTTP_200_OK)


# My lab: every Switch the caller may work on, grouped by Project, and the Quarantines naming them
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
def my_lab(request):
    """
    My lab (api.my_lab), with Projects on or off. Response: {"projects": bool, "groups": [{"kind":
    "project", "project": <as GET projects/ gives each>, "switches"} or {"kind": "holder", "holder":
    {"id", "username"}, "switches"}], "quarantines": [{"id", "model", "mngt_IP" (only if the caller
    may still work on it), "quarantine": {"opened_at", "reasons"}, "cleaning_up"}]}. Each Switch:
    {"id", "model", "mngt_IP", "holder", "holder_id", "end_date", "renewals_left",
    "admin_exception", "links", "ghost_links", "account": the caller's Switch account on it, as
    switch_accounts/ gives each, or null}.
    """
    return Response(my_lab_module.read(request.user), status=status.HTTP_200_OK)

# --- Projects (see CONTEXT.md, docs/adr/0007), with BLAB_PROJECTS on: 404 everywhere when off ---

def projects_endpoint(view):
    """Answers 404 while this stage has Projects off, and a refused Project operation with its status."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if not projects.enabled():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            return view(request, *args, **kwargs)
        except projects.ProjectError as e:
            return Response({"detail": str(e)}, status=e.status)
    return wrapped


def with_failures(detail: str, failures) -> str:
    """Adds to detail that some Switch accounts couldn't be brought in step yet."""
    if failures:
        detail += " Some Switch accounts couldn't be updated yet; BLab keeps trying."
    return detail


@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_list(request):
    """
    GET: the caller's Projects, archived ones included: {"projects": [{"id", "name", "reference",
    "owner": {"id", "username"}, "is_owner", "created_at", "archived_at", "switch_count",
    "members": [{"id", "username", "joined_at"}], "invite_token" (the owner's only, else null)}],
    "last_used": <the id Reserve preselects, or null>}.
    POST {"name", "reference"}: a new Project, owned by the caller (201, as one item of GET).
    """
    if request.method == 'POST':
        project = projects.create(request.user, request.data.get('name'), request.data.get('reference') or '')
        logger.info(f"User {request.user.username} created the Project {project.id}.")
        return Response(projects.summary(request.user, project), status=status.HTTP_201_CREATED)
    last = projects.last_used(request.user)
    return Response({"projects": projects.summaries(request.user, projects.mine(request.user)),
                     "last_used": last.id if last else None}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_detail(request, project_id):
    """One of the caller's Projects, as GET projects/ gives each; 404 for any other."""
    return Response(projects.summary(request.user, projects.member_project(request.user, project_id)),
                    status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_topology(request, project_id):
    """
    A Project's Topology, for its members: the Switches reserved into it, their Ports, every
    Link with an end on them and the layout, as topology/<owner_id>/ gives a user's, with
    "may_work" (true) and "project" (as GET projects/<id>/ gives it).
    """
    project = projects.member_project(request.user, project_id)
    return Response(dict(topology.read_project(project, Viewer(request.user)), may_work=True,
                         project=projects.summary(request.user, project)), status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['PUT', 'DELETE'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_layout(request, project_id):
    """
    A Project's Topology layout, for any member: PUT {"positions": {"<switch id>": {"x", "y"}}}
    saves these Switches' positions; DELETE forgets them all (Re-arrange).
    """
    project = projects.member_project(request.user, project_id)
    if request.method == 'DELETE':
        projects.forget_layout(project)
        return Response({"detail": "Layout forgotten."}, status=status.HTTP_200_OK)
    try:
        positions = topology.parse_positions(request.data.get('positions') if isinstance(request.data, dict) else None)
    except ValueError as error:
        return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
    projects.save_layout(project, positions)
    return Response({"detail": "Layout saved."}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_invite(request, project_id):
    """The owner renews the Invite link: the old one stops working. 200 with the Project."""
    project = projects.renew_invite(request.user, projects.member_project(request.user, project_id))
    logger.info(f"User {request.user.username} renewed the Invite link of the Project {project.id}.")
    return Response(projects.summary(request.user, project), status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_leave(request, project_id):
    """The caller leaves the Project; their Switch accounts on its Switches go."""
    project = projects.member_project(request.user, project_id)
    failures = projects.leave(request.user, project)
    logger.info(f"User {request.user.username} left the Project {project.id}.")
    return Response({"detail": with_failures(f"You left {project.name}.", failures)}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['DELETE'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_member(request, project_id, user_id):
    """The owner removes a member; their Switch accounts on the Project's Switches go."""
    project = projects.member_project(request.user, project_id)
    failures = projects.remove(request.user, project, user_id)
    logger.info(f"User {request.user.username} removed user {user_id} from the Project {project.id}.")
    return Response({"detail": with_failures("Member removed.", failures),
                     "project": projects.summary(request.user, project)}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_archive(request, project_id):
    """The owner archives a Project that holds no Switch; its Invite link stops working."""
    project = projects.archive(request.user, projects.member_project(request.user, project_id))
    logger.info(f"User {request.user.username} archived the Project {project.id}.")
    return Response(projects.summary(request.user, project), status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_switches(request, project_id):
    """
    POST {"switch": <id>}: moves that reserved Switch into this Project, without a Release
    (projects.move). 200 with "detail" and "project" (as GET projects/<id>/ gives it); 400 if
    refused (Links, holder not a member, archived...), 403 if the caller may not work on the
    Switch, 404 for a Project that isn't the caller's.
    """
    project = projects.member_project(request.user, project_id)
    switch = get_object_or_404(Switch, id=request.data.get('switch') if isinstance(request.data, dict) else None)
    failures = projects.move(request.user, switch, project)
    logger.info(f"User {request.user.username} moved switch {switch.id} into the Project {project.id}.")
    return Response({"detail": with_failures(f"The Switch moved to {project.name}.", failures),
                     "project": projects.summary(request.user, project)}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_snapshots(request, project_id):
    """
    A Project's Snapshots (api.snapshots), for its members. GET: {"snapshots": [{"id", "taken_at",
    "taken_by", "reason" ("save" or "release"), "note", "switches": [{"id", "model", "aos_release",
    "read_at", "read_error", "restorable"}], "links": <how many>}], newest first, "kept": <how many are
    kept>, "restore": the Project's latest Restore, as POST .../restore/ gives it, or null}.
    POST {"note"}: Save, a Snapshot of all its Switches now (201, as one item of GET).
    """
    project = projects.member_project(request.user, project_id)
    if request.method == 'POST':
        snapshot = snapshots.save(project, request.user, request.data.get('note') or '')
        return Response(snapshots.describe(snapshot), status=status.HTTP_201_CREATED)
    last = restore.latest(project)
    return Response({"snapshots": snapshots.listing(project), "kept": snapshots.KEPT,
                     "restore": restore.describe(last) if last else None}, status=status.HTTP_200_OK)


@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def project_restore(request, project_id, snapshot_id):
    """
    Restore onto the same Switches (api.restore), for a Project's members. GET: what it would do,
    {"switches": [{"id", "model", "state" (free, in_project, busy, unavailable or no_config),
    "detail", "free_on", "snapshot_release", "current_release", "release_differs"}], "links"}.
    POST {"switches": [ids], optional: all it can}: starts it, 201 with {"id", "snapshot", "started_by",
    "started_at", "finished_at", "switches": [{"id", "model", "state" (left_out, waiting,
    reloading, done or failed), "detail", "warnings", "free_on", "updated_at"}], "links":
    [{"ends", "outcome", "detail"}] once done}. 403 while a Quarantine names the caller.
    """
    project = projects.member_project(request.user, project_id)
    snapshot = snapshots.of_project(project, snapshot_id)
    if request.method == 'GET':
        return Response(restore.plan(project, snapshot), status=status.HTTP_200_OK)
    chosen = request.data.get('switches') if isinstance(request.data, dict) else None
    if chosen is not None and not isinstance(chosen, list):
        return Response({"detail": '"switches" must be a list of Switch ids.'}, status=status.HTTP_400_BAD_REQUEST)
    started = restore.start(project, snapshot, request.user, chosen)
    return Response(restore.describe(started), status=status.HTTP_201_CREATED)


@csrf_exempt
@api_view(['GET', 'POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated, HasEmail])
@projects_endpoint
def join(request, token):
    """
    An Invite link (/join/<token>). GET: what the confirm page shows: {"project": {"id", "name",
    "reference", "owner", "members", "switches": [{"id", "model"}]}, "member": bool}. POST: joins
    it (the caller gets Switch accounts on its Switches), 200 with the same and a "detail".
    404 for a link that doesn't work (renewed, or the Project archived).
    """
    if request.method == 'GET':
        return Response(projects.invitation(request.user, projects.preview(token)), status=status.HTTP_200_OK)
    project, joined, failures = projects.join(request.user, token)
    if joined:
        logger.info(f"User {request.user.username} joined the Project {project.id}.")
    detail = with_failures(f"You joined {project.name}." if joined
                           else f"You are already a member of {project.name}.", failures)
    return Response({**projects.invitation(request.user, project), "detail": detail}, status=status.HTTP_200_OK)
