import itertools
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
from django.views.decorators.csrf import csrf_exempt

from .models import Switch, Reservation, Port, User
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
        "user": "<user_id>",
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
    logger.info(f"User {username} logged in successfully.")
    return Response({
        "token": token.key, 
        "user": user.id,
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


# API endpoint to list all users (admin only)
@csrf_exempt
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAdminUser])
def list_user(request):
    """
    List Users endpoint.
    Allows administrators to retrieve a list of all users registered in the system.
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
            "/traps"
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
    Admins can take over existing reservations with confirmation=1.
    """
    user = request.user
    switch_id = request.data.get('switch')
    confirmation = request.data.get('confirmation', 0)
    switch = get_object_or_404(Switch, id=switch_id)

    # Check if the switch is already reserved by this user
    if Reservation.objects.filter(switch=switch, user=user).exists():
        logger.warning(f"User {user.username} attempted to reserve an already reserved switch {switch_id}.")
        return Response({"warning": "You have already reserved this switch."}, status=status.HTTP_400_BAD_REQUEST)

    # Check if the switch is reserved by someone else
    existing_reservation = Reservation.objects.filter(switch=switch).first()
    if existing_reservation:
        if confirmation == 1 and user.is_staff:  # Using Django's built-in admin check
            # Delete the existing reservation and create a new one for admin
            existing_reservation.delete(user.username)
            Reservation.objects.create(switch=switch, user=user)
            if switch.changeBanner():
                logger.info(f"Admin {user.username} took over reservation for switch {switch_id}.")
                return Response({"detail": "Previous reservation deleted and new reservation created successfully."}, 
                              status=status.HTTP_200_OK)
            else:
                return Response({"detail": "Reservation created, but failed to update the switch banner."}, 
                              status=status.HTTP_200_OK)
        else:
            logger.warning(f"Switch {switch_id} is already reserved by another user.")
            return Response({"warning": "This switch is already reserved."}, status=status.HTTP_400_BAD_REQUEST)

    # Create a new reservation if switch is not reserved
    Reservation.objects.create(switch=switch, user=user)
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
    Enables users to release a previously reserved switch.

    Request Payload:
    {
        "switch": "<switch_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Release successful."
    }
    """
    user = request.user
    switch_id = request.data.get('switch')
    switch = get_object_or_404(Switch, id=switch_id)

    reservation = Reservation.objects.filter(switch=switch, user=user).first()
    if not reservation:
        logger.warning(f"User {user.username} attempted to release a switch {switch_id} not reserved by them.")
        return Response({"warning": "You have not reserved this switch."}, status=status.HTTP_400_BAD_REQUEST)

    reservation.delete(user.username)
    if switch.changeBanner():
        logger.info(f"User {user.username} released switch {switch_id} successfully.")
        return Response({"detail": "Release successful."}, status=status.HTTP_200_OK)
    else:
        logger.warning(f"Switch {switch_id} released but failed to update the banner.")
        return Response({"detail": "Release successful. Banner couldn't be changed"}, status=status.HTTP_201_CREATED)


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

    # Check if both switches are reserved by the user
    if not (Reservation.objects.filter(switch=switchA, user=user).exists() and Reservation.objects.filter(switch=switchB, user=user).exists()):
        logger.warning(f"User {user.username} attempted to connect ports on unreserved switches.")
        return Response({"detail": "One or both switches are not reserved by the user."}, status=status.HTTP_403_FORBIDDEN)

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

    if Port.delete_link(portA, portB, request.user.username):
        max_retries = 3
        for attempt in range(max_retries):
            if portA.verify_configuration(portA.svlan, 0):
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


@api_view(['GET'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def save_topology(request):
    """
    Save a new topology with connections between ports that have the same svlan and are reserved by the logged user.
    """
    try:
        topology_data = {
            "connections": []
        }
        user_reservations = Reservation.objects.filter(user=request.user)
        user_ports = Port.objects.filter(switch__in=user_reservations.values('switch'))
        
        svlan_groups = user_ports.values_list('svlan', flat=True).distinct()

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
    except Exception as e:
        logger.error(f"Exception occurred while saving topology: {str(e)}")
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    

@api_view(['POST'])
@csrf_exempt
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def load_topology(request):
    """
    Load a Topology from a Json file. Reserve the Switches and Connect the Ports.
    """
    topology_data = request.data.get('topology')
    if not topology_data:
        logger.warning("Topology data is required but not provided.")
        return Response({"detail": "Topology data is required."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            conflicts = []
            warnings = []
            for connection in topology_data['connections']:
                port1_id = connection['port1_id']
                port2_id = connection['port2_id']
                svlan = connection['svlan']

                port1 = get_object_or_404(Port, id=port1_id)
                port2 = get_object_or_404(Port, id=port2_id)

                # Retrieve switches
                switch1 = port1.switch
                switch2 = port2.switch

                # Check if switches are already reserved by another user
                if Reservation.objects.filter(switch=switch1).exclude(user=request.user).exists():
                    conflicts.append({"switch_id": switch1.id, "message": "Switch is already reserved by another user."})
                if Reservation.objects.filter(switch=switch2).exclude(user=request.user).exists():
                    conflicts.append({"switch_id": switch2.id, "message": "Switch is already reserved by another user."})

                if conflicts:
                    # Abort the transaction if there are any conflicts
                    logger.warning(f"Conflicts encountered while loading topology: {conflicts}")
                    return Response({"detail": "There were issues loading the topology.", "conflicts": conflicts}, status=status.HTTP_409_CONFLICT)

                # Reserve switches if not already reserved by this user
                reservation1, created1 = Reservation.objects.get_or_create(switch=switch1, user=request.user)
                reservation2, created2 = Reservation.objects.get_or_create(switch=switch2, user=request.user)

                # Update switch banner if the reservation was just created
                if created1 and not switch1.changeBanner():
                    warnings.append({"switch_id": switch1.id, "message": "Failed to update switch banner."})
                if created2 and not switch2.changeBanner():
                    warnings.append({"switch_id": switch2.id, "message": "Failed to update switch banner."})

                # Set SVLAN on ports
                port1.svlan = svlan
                port2.svlan = svlan
                port1.save()
                port2.save()

                # Connect ports as in the connect() method
                if not (port1.create_link(request.user.username) and port2.create_link(request.user.username)):
                    port1.svlan = None
                    port2.svlan = None
                    port1.save()
                    port2.save()
                    conflicts.append({"port1_id": port1_id, "port2_id": port2_id, "message": "Failed to connect ports."})

            if conflicts:
                # If there are any conflicts after all operations, return an error response
                logger.warning(f"Conflicts encountered while loading topology: {conflicts}")
                return Response({"detail": "There were issues loading the topology.", "conflicts": conflicts}, status=status.HTTP_202_ACCEPTED)

            response_data = {"detail": "Topology loaded successfully."}
            if warnings:
                response_data["warnings"] = warnings

            logger.info("Topology loaded successfully.")
            return Response(response_data, status=status.HTTP_200_OK)
    except Exception as e:
        # Log the exception for debugging
        logger.error(f"Exception occurred while loading topology: {str(e)}")
        return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
