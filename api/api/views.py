from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework import status
from django.contrib.auth import authenticate

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
- Add Switch: Allows authenticated users to add a new switch to the system.
- List Switches: Enables users to retrieve a list of all switches in the system.
- Delete Switch: Allows administrators to delete a switch from the system.
- Add Port: Allows authenticated users to add a new port to a switch.
- Delete Port: Enables users to delete a port from the system.
- List Ports: Allows users to retrieve a list of all ports in the system.
- List Ports by Switch: Enables users to retrieve a list of ports belonging to a specific switch.
- Reserve Switch: Allows users to reserve a switch for their use.
- Release Switch: Enables users to release a previously reserved switch.
- List Reservations: Allows users to retrieve a list of all reservations made in the system.
- Connect Ports: Allows users to connect two ports belonging to different switches.
- Disconnect Ports: Enables users to disconnect two previously connected ports.
"""


# Utility function to generate unique SVLAN
def get_unique_svlan():
    all_svlans = set(Port.objects.exclude(svlan=None).values_list('svlan', flat=True))
    unique_svlan = 1001
    while unique_svlan in all_svlans:
        unique_svlan += 1
    return unique_svlan


# API endpoint for user login
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
        "user": "<user_id>"
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
        return Response({"detail": "Invalid credentials."}, status=status.HTTP_401_UNAUTHORIZED)

    # Generate or retrieve token
    token, created = Token.objects.get_or_create(user=user)
    return Response({"token": token.key, "user": user.id}, status=status.HTTP_202_ACCEPTED)


# API endpoint for user signup
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
        return Response({"token": token.key, "user": serializer.data},  status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# API endpoint for user logout
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
            token_key = authorization_header.split(' ')[1]
            token = Token.objects.get(key=token_key)
            token.delete()
            return Response({"detail": "Logout successful."}, status=status.HTTP_200_OK)
        except Token.DoesNotExist:
            return Response({"detail": "Invalid token."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        return Response({"detail": "Authorization header not provided."}, status=status.HTTP_400_BAD_REQUEST)


# API endpoint to list all users (admin only)
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
            "/add_switch",
            "/del_switch",
            "/list_switch",
            "/add_port",
            "/del_port",
            "/list_port",
            "/list_port_by_switch/<int:switch_id>",
            "/reserve",
            "/release",
            "/list_reservation",
            "/connect",
            "/disconnect"
        ]
    }
    return Response(api_urls)


# API endpoint to add a switch
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def add_switch(request):
    """
    Add Switch endpoint.
    Allows authenticated users to add a new switch to the system.
    """
    serializer = SwitchSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({"switch": serializer.data}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# API endpoint to list all switches
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
    return Response({"detail": "Success"}, status=status.HTTP_200_OK)


# API endpoint to add a port
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def add_port(request):
    """
    Add Port endpoint.
    Allows authenticated users to add a new port to a switch.

    Request Payload:
    {
        "switch": "<switch_id>",
        "port_switch": "<port_switch_value>",
        "backbone": "<backbone_value>",
        "port_backbone": "<port_backbone_value>"
    }

    Expected Response Payload (Successful):
    {
        "port": {
            "id": "<port_id>",
            "switch": "<switch_id>",
            "port_switch": "<port_switch_value>",
            "backbone": "<backbone_value>",
            "port_backbone": "<port_backbone_value>",
            "svlan": null
        }
    }
    """
    serializer = PortSerializer(data=request.data)
    if serializer.is_valid():
        serializer.save()
        return Response({"port": serializer.data}, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# API endpoint to delete a port (admin only)
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
    return Response({"detail": "Success"}, status=status.HTTP_200_OK)


# API endpoint to list all ports
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
@api_view(['POST'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def reserve(request):
    """
    Reserve Switch endpoint.
    Allows users to reserve a switch for their use.

    Request Payload:
    {
        "switch": "<switch_id>"
    }

    Expected Response Payload (Successful):
    {
        "detail": "Reservation successful."
    }
    """
    user = request.user
    switch_id = request.data.get('switch')
    switch = get_object_or_404(Switch, id=switch_id)

    existing_reservation = Reservation.objects.filter(switch=switch, user=user).first()
    if existing_reservation:
        return Response({"warning": "You have already reserved this switch."}, status=status.HTTP_400_BAD_REQUEST)

    existing_reservations = Reservation.objects.filter(switch=switch)
    if existing_reservations and request.data.get('confirmation') == 0:
        return Response({"warning": "This switch is already reserved."}, status=status.HTTP_400_BAD_REQUEST)

    Reservation.objects.create(switch=switch, user=user)
    if switch.changeBanner():
        return Response({"detail": "Reservation successful."}, status=status.HTTP_201_CREATED)
    else:
        return Response({"detail": "Reservation successful. Banner couldn't be changed"}, status=status.HTTP_201_CREATED)


# API endpoint to release a switch
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
        return Response({"warning": "You have not reserved this switch."}, status=status.HTTP_400_BAD_REQUEST)

    reservation.delete(request.user.username)
    if switch.changeBanner():
        return Response({"detail": "Release successful."}, status=status.HTTP_200_OK)
    else:
        return Response({"detail": "Release successful. Banner couldn't be changed"}, status=status.HTTP_201_CREATED)


# API endpoint to list all reservations
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

    if not (Reservation.objects.filter(switch=switchA, user=user).exists() and
            Reservation.objects.filter(switch=switchB, user=user).exists()):
        return Response({"detail": "One or both switches are not reserved by the user."}, status=status.HTTP_403_FORBIDDEN)

    svlan = get_unique_svlan()
    portA.svlan = svlan
    portB.svlan = svlan
    portA.save()
    portB.save()

    if portA.create_link(request.user.username) and portB.create_link(request.user.username):
        return Response({"detail": "Ports connected successfully with svlan {}".format(svlan)}, status=status.HTTP_200_OK)
    else:
        portA.svlan = None
        portB.svlan = None
        portA.save()
        portB.save()
        return Response({"detail": "Ports failed to connect"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# API endpoint to disconnect two ports
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

    if portA.delete_link(request.user.username) and portB.delete_link(request.user.username):
        portA.svlan = None
        portB.svlan = None
        portA.save()
        portB.save()
        return Response({"detail": "Ports disconnected successfully."}, status=status.HTTP_200_OK)
    else:
        return Response({"detail": "Ports failed to disconnect."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    

# API endpoint to list all switches
@api_view(['GET'])
@authentication_classes([SessionAuthentication, TokenAuthentication])
@permission_classes([IsAuthenticated])
def fetch_switch_info(request, switch_id):
    """
    List Switches endpoint.
    Enables users to retrieve a list of all switches in the system.
    """

    switch = get_object_or_404(Switch, id=switch_id)
    switch.fetch_info()

