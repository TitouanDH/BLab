import time
import logging  # Add logging import
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
from django.utils.dateparse import parse_datetime

from .models import Switch, Reservation, Port, User, TopologyShare
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
- Release Switch: Enables users to release a previously reserved switch.
- List Reservations: Allows users to retrieve a list of all reservations made in the system.
- Connect Ports: Allows users to connect two ports belonging to different switches.
- Disconnect Ports: Enables users to disconnect two previously connected ports.
- Traps: Handles various alerts sent by switches.
- Share Topology: Allows users to share their topology with other users.
- List Shared Topologies: Enables users to view topologies shared with them.
- Get Shared Topology: Allows users to retrieve a specific shared topology.
"""


# Configure logging to save logs to a file
logging.basicConfig(filename='/app/logs/api_views.log', level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Utility function to generate unique SVLAN
def get_unique_svlan():
    all_svlans = set(Port.objects.exclude(svlan=None).values_list('svlan', flat=True))
    unique_svlan = 1001
    while unique_svlan in all_svlans:
        unique_svlan += 1
    return unique_svlan


# Utility function to check if user has access to a switch (owns or shared with them)
def user_has_switch_access(user, switch):
    """
    Check if a user has access to a switch either by:
    1. Having a reservation on the switch, OR
    2. Having the switch owner's topology shared with them
    """
    # Check if user directly reserved the switch
    if Reservation.objects.filter(switch=switch, user=user).exists():
        return True
    
    # Check if the switch is reserved by someone who shared their topology with this user
    switch_reservation = Reservation.objects.filter(switch=switch).first()
    if switch_reservation:
        switch_owner = switch_reservation.user
        # Check if the switch owner shared their topology with the current user
        if TopologyShare.objects.filter(owner=switch_owner, target=user).exists():
            return True
    
    return False


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

    Expected Response Payload (Failed):
    {
        "error": "<error_message>"
    }
    """
    serializer = UserSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        user.set_password(request.data['password'])
        user.save()
        token, created = Token.objects.get_or_create(user=user)
        serializer = UserSerializer(instance=user)
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
            "/get_shared_topology/<int:owner_id>"
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
    return Response({"switchs": serializer.data}, status=status.HTTP_200_OK)


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
    Accepts optional end_date (ISO 8601 string).
    """
    user = request.user
    switch_id = request.data.get('switch')
    end_date_str = request.data.get('end_date')
    end_date = parse_datetime(end_date_str) if end_date_str else None
    switch = get_object_or_404(Switch, id=switch_id)

    # Check if the switch is already reserved by this user
    if Reservation.objects.filter(switch=switch, user=user).exists():
        logger.warning(f"User {user.username} attempted to reserve an already reserved switch {switch_id}.")
        return Response({"warning": "You have already reserved this switch."}, status=status.HTTP_400_BAD_REQUEST)

    # Check if the switch is reserved by someone else
    existing_reservation = Reservation.objects.filter(switch=switch).first()
    if existing_reservation:
        logger.warning(f"Switch {switch_id} is already reserved by another user.")
        return Response({"warning": "This switch is already reserved."}, status=status.HTTP_400_BAD_REQUEST)

    # Create a new reservation if switch is not reserved
    Reservation.objects.create(switch=switch, user=user, end_date=end_date)
    if switch.changeBanner():
        logger.info(f"User {user.username} reserved switch {switch_id} successfully.")
        return Response({"detail": "Reservation successful."}, status=status.HTTP_201_CREATED)
    else:
        return Response({"detail": "Reservation successful, but failed to update the switch banner."}, 
                       status=status.HTTP_201_CREATED)


# API endpoint to release a switch
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def release(request):
    """
    Release Switch endpoint.
    Enables users to release a previously reserved switch or shared switch.

    Request Payload:
    {
        "switch": "<switch_id>",
        "cleanup": true/false (optional, default: false)
    }

    Expected Response Payload (Successful):
    {
        "detail": "Release successful."
    }
    """
    user = request.user
    switch_id = request.data.get('switch')
    cleanup_switch = request.data.get('cleanup', False)  # Default to no cleanup
    
    logger.info(f"Release request: user={user.username}, switch_id={switch_id}, cleanup={cleanup_switch}")
    
    try:
        switch = get_object_or_404(Switch, id=switch_id)
        logger.info(f"Found switch: {switch.mngt_IP} (id={switch.id})")
    except Exception as e:
        logger.error(f"Switch not found for id={switch_id}: {e}")
        return Response({"error": f"Switch with id {switch_id} not found"}, status=status.HTTP_404_NOT_FOUND)

    # Check if user has access to this switch (owns or shared)
    if not user_has_switch_access(user, switch):
        logger.warning(f"User {user.username} attempted to release a switch {switch_id} they don't have access to.")
        return Response({"warning": "You don't have access to this switch."}, status=status.HTTP_403_FORBIDDEN)

    # Find the actual reservation (might be from the owner, not necessarily the current user)
    reservation = Reservation.objects.filter(switch=switch).first()
    if not reservation:
        logger.warning(f"No reservation found for switch {switch_id}.")
        return Response({"warning": "This switch is not reserved."}, status=status.HTTP_400_BAD_REQUEST)

    # Check if this is the last reservation on the switch
    is_last_reservation = Reservation.objects.filter(switch=switch).count() == 1
    
    if reservation.delete(user.username, cleanup_switch):
        message = "Release successful."
        if cleanup_switch and is_last_reservation:
            message += " Switch cleanup performed."
        elif cleanup_switch and not is_last_reservation:
            message += " Cleanup skipped - other reservations exist."
        elif not cleanup_switch and is_last_reservation:
            message += " Switch ready for manual cleanup if needed."
            
        if switch.changeBanner():
            logger.info(f"User {user.username} released switch {switch_id} successfully (cleanup: {cleanup_switch}).")
            return Response({"detail": message}, status=status.HTTP_200_OK)
        else:
            logger.warning(f"Switch {switch_id} released but failed to update the banner.")
            return Response({"detail": message + " Banner couldn't be changed"}, status=status.HTTP_201_CREATED)
    else:
        return Response({"error": "Failed to release switch. Some ports may still be connected."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# API endpoint to list all reservations
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def list_reservation(request):
    """
    List Reservations endpoint.
    Allows users to retrieve a list of all reservations made in the system.
    """
    reservations = Reservation.objects.all()
    serializer = ReservationSerializer(reservations, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


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

    # Validate that ports are not already connected
    if portA.svlan is not None or portB.svlan is not None:
        logger.warning(f"User {user.username} attempted to connect ports that are already linked.")
        return Response({"detail": "One or both ports are already connected. Disconnect them first."}, status=status.HTTP_400_BAD_REQUEST)

    svlan = get_unique_svlan()
    portA.svlan = svlan
    portB.svlan = svlan
    portA.save()
    portB.save()

    if Port.create_link(portA, portB, request.user.username):
        max_retries = 3
        for attempt in range(max_retries):
            if portA.verify_configuration(portA.svlan, 4):
                logger.info(f"Ports {portA.id} and {portB.id} connected successfully with svlan {svlan}.")
                return Response({"detail": "Ports connected successfully with svlan {}".format(svlan)}, status=status.HTTP_200_OK)
            else:
                print(f"Verification failed on attempt {attempt + 1}/{max_retries}. Retrying...")
                time.sleep(2)  # Wait before retrying

        # If all retries fail
        portA.svlan = None
        portB.svlan = None
        portA.save()
        portB.save()
        return Response({"detail": "Ports failed to connect - Verification fail"}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    else:
        logger.error(f"Failed to connect ports {portA.id} and {portB.id}.")
        portA.svlan = None
        portB.svlan = None
        portA.save()
        portB.save()
        return Response({"detail": "Ports failed to connect"}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


# API endpoint to disconnect two ports
@csrf_exempt
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def disconnect(request):
    """
    Disconnect Ports endpoint.
    Enables users to disconnect two previously connected ports.

    Request Payload:
    {
        "portA": "<port_id>",
        "portB": "<port_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Ports disconnected successfully."
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

    # Check if user has access to both switches (owns or shared)
    if not (user_has_switch_access(user, switchA) and user_has_switch_access(user, switchB)):
        logger.warning(f"User {user.username} attempted to disconnect ports on switches they don't have access to.")
        return Response({"detail": "You don't have access to one or both switches."}, status=status.HTTP_403_FORBIDDEN)

    # Validate that ports are actually connected (same SVLAN)
    if portA.svlan is None or portB.svlan is None or portA.svlan != portB.svlan:
        logger.warning(f"User {user.username} attempted to disconnect ports that are not linked.")
        return Response({"detail": "These ports are not connected to each other."}, status=status.HTTP_400_BAD_REQUEST)

    # Store SVLAN before deletion for verification
    original_svlan = portA.svlan
    
    if Port.delete_link(portA, portB, request.user.username):
        max_retries = 3
        for attempt in range(max_retries):
            # Verify the link is actually deleted by checking for 0 configuration lines
            if portA.verify_configuration(str(original_svlan), 0):
                portA.svlan = None
                portB.svlan = None
                portA.save()
                portB.save()
                logger.info(f"Ports {portA.id} and {portB.id} disconnected successfully.")
                return Response({"detail": "Ports disconnected successfully."}, status=status.HTTP_200_OK)
            else:
                print(f"Verification failed on attempt {attempt + 1}/{max_retries}. Retrying...")
                time.sleep(2)  # Wait before retrying

        # If all retries fail
        return Response({"detail": "Ports failed to disconnect - Verification fail"}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    else:
        logger.error(f"Failed to disconnect ports {portA.id} and {portB.id}.")
        return Response({"detail": "Ports failed to disconnect."}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)


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


# API endpoint to get a specific shared topology
@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_shared_topology(request, owner_id):
    """
    Récupère la topologie d'un autre utilisateur si elle a été partagée avec l'utilisateur courant.
    """
    try:
        owner = User.objects.get(id=owner_id)
        if not TopologyShare.objects.filter(owner=owner, target=request.user).exists():
            return Response({"detail": "No shared topology from this user."}, status=status.HTTP_403_FORBIDDEN)
        # On réutilise la logique de save_topology mais pour l'utilisateur owner
        topology_data = {
            "connections": []
        }
        user_reservations = Reservation.objects.filter(user=owner)
        user_ports = Port.objects.filter(switch__in=user_reservations.values('switch'))
        svlan_groups = user_ports.values_list('svlan', flat=True).distinct()
        import itertools
        for svlan in svlan_groups:
            ports_with_same_svlan = list(user_ports.filter(svlan=svlan))
            if len(ports_with_same_svlan) > 1:
                connections = list(itertools.combinations(ports_with_same_svlan, 2))
                for port1, port2 in connections:
                    topology_data["connections"].append({
                        "port1_id": port1.id,
                        "port2_id": port2.id,
                        "svlan": svlan
                    })
        return Response(topology_data, status=status.HTTP_200_OK)
    except User.DoesNotExist:
        return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
