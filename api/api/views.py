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
from django.db.models import Q
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone

from .models import PendingCleanup, Quarantine, Switch, SwitchEvent, Reservation, Port, User, TopologyShare
from . import links, quarantine, reservations, topology
from . import release as releasing
from .inspection import cables_left
from .lab_switch import LabSwitchError
from .serializers import SwitchSerializer, ReservationSerializer, PortSerializer, UserSerializer
from django.shortcuts import get_object_or_404

"""
Features:

- Login: Allows users to authenticate themselves by providing their username and password.
- Signup: Enables users to create new accounts by providing a username and password.
- Logout: Allows authenticated users to log out of their accounts.
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
        "user": { "id": ..., "username": ... },
        "is_staff": boolean
    }

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
    serializer = UserSerializer(instance=user)
    logger.info(f"User {username} logged in successfully.")
    return Response({
        "token": token.key, 
        "user": serializer.data,
        "is_staff": user.is_staff
    }, status=status.HTTP_202_ACCEPTED)


# API endpoint for user signup
@csrf_exempt
@api_view(['POST'])
def signup(request):
    """
    User signup endpoint.
    Enables users to create new accounts by providing a username and password.

    Request Payload:
    {
        "username": "new_user",
        "password": "Password123"
    }

    Expected Response Payload (Successful):
    {
        "token": "<generated_token>",
        "user": {
            "id": "<user_id>",
            "username": "new_user"
        }
    }

    Expected Response Payload (Failed): the invalid fields and their errors
    {
        "username": ["<error_message>"]
    }

    Only the username and password are read; any other field (is_staff, ...) is ignored.
    """
    serializer = UserSerializer(data=request.data)
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
@permission_classes([IsAuthenticated])
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


# API endpoint to list all users
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])  # Changed from IsAdminUser to IsAuthenticated
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
            "/lab_status",
            "/release_check/<int:switch_id>",
            "/recheck",
            "/renew"
        ]
    }
    return Response(api_urls)



# API endpoint to list all switches
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def list_switch(request):
    """
    List Switches endpoint.
    Enables users to retrieve a list of all switches in the system.
    """
    switch = Switch.objects.all()
    serializer = SwitchSerializer(instance=switch, many=True)
    # Why a Switch can't be reserved now (Out of service, Quarantine, Cleanup), or None
    unavailable = quarantine.unavailable()
    switches = [{**data, 'unavailable': unavailable.get(data['id'])} for data in serializer.data]
    return Response({"switchs": switches}, status=status.HTTP_200_OK)


HISTORY_LENGTH = 10  # events per Switch on the Lab status page


def serialize_switch_event(event):
    """One entry of a Switch's history, as the Lab status page shows it."""
    return {'kind': event.kind, 'at': event.at, 'ok': event.ok, 'reasons': event.reasons,
            'warnings': event.warnings, 'user': event.user.username if event.user else None}


def serialize_quarantine(quarantine):
    if quarantine is None:
        return None
    return {'holder': quarantine.holder.username if quarantine.holder else None,
            'holder_id': quarantine.holder_id,
            'opened_at': quarantine.opened_at, 'reasons': quarantine.reasons}


# The Lab status page: every Switch, who holds it, and what its last Inspection found
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def lab_status(request):
    """
    Every Switch with its holder (username, and holder_id), Reservation end date, last
    Inspection, open Quarantine, Out of service, whether it is being Cleaned up, and recent
    history (newest first). Every logged-in user sees the whole lab.
    """
    held = {r.switch_id: r for r in Reservation.objects.select_related('user')}
    quarantines = {q.switch_id: q for q in Quarantine.objects.filter(lifted_at__isnull=True).select_related('holder')}
    cleaning_up = set(PendingCleanup.objects.values_list('switch_id', flat=True))
    switches = []
    for switch in Switch.objects.order_by('mngt_IP'):
        reservation = held.get(switch.id)
        history = list(switch.events.select_related('user')[:HISTORY_LENGTH])
        inspection = next((e for e in history if e.kind == SwitchEvent.INSPECTION), None)
        if inspection is None and len(history) == HISTORY_LENGTH:  # older than the recent history
            inspection = switch.last_inspection()
        switches.append({
            'id': switch.id,
            'mngt_IP': switch.mngt_IP,
            'model': switch.model,
            'holder': reservation.user.username if reservation else None,
            'holder_id': reservation.user_id if reservation else None,
            'end_date': reservation.end_date if reservation else None,
            'renewals_left': reservations.renewals_left(reservation) if reservation else None,
            'admin_exception': reservation.admin_exception if reservation else None,
            'inspection': serialize_switch_event(inspection) if inspection else None,
            'quarantine': serialize_quarantine(quarantines.get(switch.id)),
            'out_of_service': ({'reason': switch.out_of_service_reason, 'since': switch.out_of_service_since}
                               if switch.out_of_service else None),
            'cleaning_up': switch.id in cleaning_up,
            'history': [serialize_switch_event(e) for e in history],
        })
    return Response({'switches': switches}, status=status.HTTP_200_OK)


# API endpoint to delete a switch (admin only)
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAdminUser])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
def reserve(request):
    """
    Reserve Switch endpoint.
    Allows users to reserve a switch for their use.
    No more admin force reservation - only available switches can be reserved.

    Request Payload: {"switch": "<switch_id>", "end_date": "<ISO 8601>"}

    end_date is required, in the future, and at most 14 days from now (api.reservations).
    An admin may set any end date: beyond the limit, the Reservation is an admin exception.
    Responses: 201 with "detail", "end_date" and "admin_exception"; 400 if refused.
    """
    user = request.user
    switch_id = request.data.get('switch')
    end_date = reservations.parse_end_date(request.data.get('end_date'))
    try:
        admin_exception = reservations.check_end_date(end_date, user, timezone.now())
    except reservations.LimitError as e:
        return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # A Switch has at most one Reservation: the row lock makes check-then-create atomic
    with transaction.atomic():
        switch = get_object_or_404(Switch.objects.select_for_update(), id=switch_id)
        holder = Reservation.objects.filter(switch=switch).values_list('user_id', flat=True).first()
        if holder is not None:
            logger.warning(f"User {user.username} attempted to reserve switch {switch_id}, which is already reserved.")
            message = "You have already reserved this switch." if holder == user.id else "This switch is already reserved."
            return Response({"warning": message}, status=status.HTTP_400_BAD_REQUEST)
        unavailable = quarantine.unavailable().get(switch.id)
        if unavailable:
            return Response({"detail": f"This Switch can't be reserved. {unavailable['reason']}"},
                            status=status.HTTP_400_BAD_REQUEST)
        named = quarantine.quarantine_naming(user)
        if named:
            logger.warning(f"User {user.username} is named in the Quarantine of {named.switch.mngt_IP}: reservation refused.")
            return Response({"detail": f"You can't make new Reservations while the Quarantine of "
                                       f"{named.switch.mngt_IP} names you ({'; '.join(named.reasons)}). "
                                       "Fix it, then press Re-check on the Lab status page."},
                            status=status.HTTP_403_FORBIDDEN)
        Reservation.objects.create(switch=switch, user=user, end_date=end_date, admin_exception=admin_exception)

    reserved = {"end_date": end_date, "admin_exception": admin_exception}
    if admin_exception:
        logger.info(f"Admin {user.username} reserved switch {switch_id} until {end_date}, beyond the limit.")
    if switch.changeBanner():
        logger.info(f"User {user.username} reserved switch {switch_id} successfully.")
        return Response({"detail": "Reservation successful.", **reserved}, status=status.HTTP_201_CREATED)
    return Response({"detail": "Reservation successful, but failed to update the switch banner.", **reserved},
                    status=status.HTTP_201_CREATED)


# API endpoint to Renew a Reservation
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def renew(request):
    """
    Renewal: pushes the end date of the Switch's Reservation back by 7 days, at most twice.
    The holder, or a user the holder shares their Topology with, may Renew.

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
@permission_classes([IsAuthenticated])
def release(request):
    """
    Release Switch endpoint: translates between HTTP and release.release().
    The holder, or a user the holder shares their topology with, may release the switch.

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
@permission_classes([IsAuthenticated])
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
@permission_classes([IsAuthenticated])
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
    if event.ok:
        detail = "Clean: the Quarantine is lifted."
    else:
        detail = "Still not clean: " + "; ".join(event.reasons)
    return Response({"clean": event.ok, "reasons": event.reasons, "detail": detail}, status=status.HTTP_200_OK)



# API endpoint to list all reservations
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def list_reservation(request):
    """
    List Reservations endpoint: every Reservation in the lab. Each row also carries the
    holder's "username", and "may_work": whether the caller may Renew or Release it (the
    holder, or a user the holder shares their Topology with).
    """
    all_reservations = Reservation.objects.select_related('user')
    serializer = ReservationSerializer(all_reservations, many=True)
    workable = topology.workable_owners(request.user)
    rows = [{**data, 'username': reservation.user.username, 'may_work': reservation.user_id in workable}
            for data, reservation in zip(serializer.data, all_reservations)]
    return Response(rows, status=status.HTTP_200_OK)


# API endpoint to connect two ports
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def connect(request):
    """
    Connect Ports endpoint.
    Allows users to connect two ports belonging to different switches.

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

    # Check if user has access to both switches (owns or shared)
    if not (user_has_switch_access(user, switchA) and user_has_switch_access(user, switchB)):
        logger.warning(f"User {user.username} attempted to connect ports on switches they don't have access to.")
        return Response({"detail": "You don't have access to one or both switches."}, status=status.HTTP_403_FORBIDDEN)

    try:
        link = links.connect(portA, portB)
    except links.LinkError as e:
        logger.warning(f"User {user.username} could not connect ports {portA.id} and {portB.id}: {e}")
        return Response({"detail": str(e)}, status=LINK_ERROR_STATUS.get(type(e), status.HTTP_422_UNPROCESSABLE_ENTITY))
    logger.info(f"Ports {portA.id} and {portB.id} connected successfully with svlan {link.svlan}.")
    return Response({"detail": "Ports connected successfully with svlan {}".format(link.svlan)}, status=status.HTTP_200_OK)


# API endpoint to disconnect two ports
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
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


# API endpoint to share topology with another user
@api_view(['POST'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def share_topology(request):
    """
    Partage la topologie de l'utilisateur courant avec un autre utilisateur.
    Payload: { "target_username": "bob" }
    """
    target_username = request.data.get('target_username')
    if not target_username:
        return Response({"detail": "Target username required."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        target_user = User.objects.get(username=target_username)
        if TopologyShare.objects.filter(owner=request.user, target=target_user).exists():
            return Response({"detail": "Topology already shared with this user."}, status=status.HTTP_409_CONFLICT)
        TopologyShare.objects.create(owner=request.user, target=target_user)
        return Response({"detail": "Topology shared successfully."}, status=status.HTTP_201_CREATED)
    except User.DoesNotExist:
        return Response({"detail": "Target user does not exist."}, status=status.HTTP_404_NOT_FOUND)


# API endpoint to list topologies shared with the user
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def list_shared_topologies(request):
    """
    Liste les topologies partagées avec l'utilisateur courant et celles qu'il a partagées.
    """
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
@permission_classes([IsAuthenticated])
def unshare_topology(request, share_id):
    """
    Supprime un partage de topologie.
    L'utilisateur peut supprimer un partage qu'il a créé ou dont il est la cible.
    """
    try:
        # Allow deletion if user is either owner or target of the share
        share = TopologyShare.objects.filter(
            id=share_id
        ).filter(
            Q(owner=request.user) | Q(target=request.user)
        ).first()
        
        if not share:
            return Response({"detail": "Share not found or permission denied."}, status=status.HTTP_404_NOT_FOUND)
            
        share.delete()
        return Response({"detail": "Topology unshared successfully."}, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({"detail": "Error unsharing topology."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# API endpoint to read a user's Topology
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_topology(request, owner_id):
    """
    A user's Topology: their Switches, those Switches' Ports, and every Link with an end
    on them (see api.topology). For the owner, or a user the owner shares it with.
    "may_work" says whether the caller may connect, disconnect and release in it.
    """
    owner = get_object_or_404(User, id=owner_id)
    if not topology.may_see(request.user, owner.id):
        return Response({"detail": "This topology is not shared with you."}, status=status.HTTP_403_FORBIDDEN)
    return Response(dict(topology.read(owner), may_work=topology.may_work(request.user, owner.id)),
                    status=status.HTTP_200_OK)
