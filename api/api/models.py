import time
from typing import Any
from django.db import models
from django.contrib.auth.models import User
import requests
import paramiko

from requests.packages.urllib3.exceptions import InsecureRequestWarning #type: ignore

# Suppress SSL warnings
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

COOKIE = None


class APIRequestError(Exception):
    """Exception raised for errors in API requests."""

    def __init__(self, message="API request failed"):
        self.message = message
        super().__init__(self.message)


def cli(ip: str, cmd: str) -> Any:
    """
    Executes a CLI command on a network device using HTTPS requests.

    Args:
        ip (str): IP address of the network device.
        cmd (str): CLI command to be executed.

    Returns:
        Any: Output of the CLI command.

    Raises:
        APIRequestError: If the API request fails.
    """
    global COOKIE
    payload = {}
    headers = {
        'Accept': 'application/vnd.alcatellucentaos+json; version=1.0',
    }
    # If COOKIE is set, add it to the headers else authenticate
    if COOKIE:
        headers['Cookie'] = 'wv_sess={}'.format(COOKIE)
    else:
        auth_url = "https://{}?domain=auth&username=admin&password=switch".format(ip)

        # Send authentication request
        try:
            auth_response = requests.get(auth_url, headers=headers, data=payload, verify=False)

            # Get the cookie from the response headers
            if 'Set-Cookie' in auth_response.headers:
                COOKIE = auth_response.headers['Set-Cookie'].split(';')[0].split('=')[1]
                headers['Cookie'] = 'wv_sess={}'.format(COOKIE)
        except requests.exceptions.RequestException as e:
            print(f"API request failed: {e}")
            raise APIRequestError()

    # Test CLI
    url = "https://{}?domain=cli&cmd={}".format(ip, cmd)

    try:
        response = requests.get(url, headers=headers, data=payload, verify=False)

        # Check if not logged in
        if response.json()["result"]["error"] == "You must login first":
            auth_url = "https://{}?domain=auth&username=admin&password=switch".format(ip)

            # Send authentication request
            auth_response = requests.get(auth_url, headers=headers, data=payload, verify=False)

            # Get the cookie from the response headers
            if 'Set-Cookie' in auth_response.headers:
                COOKIE = auth_response.headers['Set-Cookie'].split(';')[0].split('=')[1]

            # Resend CLI request with the updated cookie
            if COOKIE:
                headers['Cookie'] = 'wv_sess={}'.format(COOKIE)
                response = requests.get(url, headers=headers, data=payload, verify=False)

        return response.json()["result"]["output"]
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        raise APIRequestError()
    
def cli_with_retry(ip: str, cmd: str, retries: int = 3, delay: float = 1.0):
    for attempt in range(retries):
        try:
            result = cli(ip, cmd)
            return result  # La commande a réussi, on retourne le résultat
        except APIRequestError:
            if attempt < retries - 1:
                time.sleep(delay)  # Attendre avant de réessayer
            else:
                raise  # Lever l'erreur si toutes les tentatives échouent


class Switch(models.Model):
    """
    Represents a network switch.

    Attributes:
        mngt_IP (str): Management IP address of the switch.
        model (str): Model of the switch.
        console (str): Type of console used for the switch.
        part_number (str): Part number of the switch.
        hardware_revision (str): Hardware revision of the switch.
        serial_number (str): Serial number of the switch.
    """
    mngt_IP = models.CharField(max_length=255)
    model = models.CharField(max_length=255)
    console = models.CharField(max_length=255)
    part_number = models.CharField(max_length=255)
    hardware_revision = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.model}_{self.mngt_IP}"  # Return a string representation of the model for admin interface

    def delete(self):
        """
        Deletes the switch and its associated ports.
        """
        ports = Port.objects.filter(switch=self.id)
        for port in ports:
            port.delete()
        return super().delete()


    def changeBanner(self):
        """
        Changes the banner of the switch.

        Args:
            self: Switch instance.

        Returns:
            bool: True if the banner is successfully changed, False otherwise.
        """
        if self.mngt_IP == "Not available":
            print(f"Skipping banner update for switch with management IP: {self.mngt_IP}")
            return True

        reservations = Reservation.objects.filter(switch=self)
        if reservations.exists():
            # Concatenate the names of all users who have reserved the switch
            user = ', '.join(reservation.user.username for reservation in reservations)
        else:
            user = "nobody"

        text = """
        ***************** LAB RESERVATION SYSTEM ******************
        This switch is reserved by : {}
        If you access this switch without reservation, please contact admin

        To cleanup the switch:
        cp init/vc* working
        reload from working no rollback-timeout

        """.format(user)
        print("change_banner")
        try:
            with paramiko.SSHClient() as ssh:
                # Set policy to automatically add unknown hosts
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                # Connect to the SSH server
                ssh.connect(self.mngt_IP, username='admin', password='switch', port=22, timeout=1)

                # Open an SFTP session
                with ssh.open_sftp() as ftp:
                    # Open the pre_banner.txt file for writing
                    # adjust write right for pre_banner file !
                    with ftp.file('switch/pre_banner.txt', "w") as file:
                        # Write the text content to the file
                        file.write(text)
                    ftp.close()
                    ssh.close()
                    return True
        except paramiko.SSHException as ssh_exception:
            # Handle SSH connection errors
            print(f"SSH Connection Error: {ssh_exception}")
            return False
        except Exception as e:
            # Handle any other exceptions that occur
            print(f"Error: {e}")
            return False


class Reservation(models.Model):
    """
    Represents a reservation for a switch.

    Attributes:
        switch (Switch): Switch associated with the reservation.
        user (User): User who made the reservation.
    """

    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    
    def __str__(self):
        return f"{self.switch}_{self.user}"  # Return a string representation of the model for admin interface

    def delete(self, username):
        """
        Deletes the reservation and releases associated ports.

        Args:
            username (str): Username of the user making the deletion.
        """
        failure_on_port_release = 0
        ports = Port.objects.filter(switch=self.switch)
        for port in ports:
            if port.svlan != None:
                connected = Port.objects.filter(svlan=port.svlan)
                for conn in connected:
                    if conn.delete_link(username):
                        conn.svlan = None
                        conn.save()
                    else:
                        failure_on_port_release = 1

        if not failure_on_port_release:
            super().delete()


class Port(models.Model):
    """
    Represents a port on a switch.

    Attributes:
        switch (Switch): Switch to which the port belongs.
        port_switch (str): Port identifier on the switch.
        backbone (str): Backbone information.
        port_backbone (str): Port identifier on the backbone.
        svlan (int): Service VLAN associated with the port.
    """

    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    port_switch = models.CharField(max_length=255)
    backbone = models.CharField(max_length=255)
    port_backbone = models.CharField(max_length=255)
    svlan = models.IntegerField(default=None, blank=True, null=True)
    status = models.CharField(max_length=10, default='DOWN', choices=[('UP', 'Up'), ('DOWN', 'Down')])

    def __str__(self):
        return f"{self.switch}_{self.port_backbone}"  # Return a string representation of the model for admin interface

    def up(self) -> bool:
        try:
            cli_with_retry(self.backbone, f"interfaces {self.port_backbone} admin-state enable")
            self.status = 'UP'
            self.save()
            return True
        except APIRequestError:
            return False

    def down(self) -> bool:
        try:
            cli_with_retry(self.backbone, f"interfaces {self.port_backbone} admin-state disable")
            self.status = 'DOWN'
            self.save()
            return True
        except APIRequestError:
            return False

    def create_link(self, user_name: str) -> bool:
        svlan = str(self.svlan)
        service_name = f"{user_name}_{svlan}"
        try:
            cli_with_retry(self.backbone, f"ethernet-service svlan {svlan} admin-state enable")
            cli_with_retry(self.backbone, f"ethernet-service service-name {service_name} svlan {svlan}")
            cli_with_retry(self.backbone, f"ethernet-service sap {svlan} service-name {service_name}")
            cli_with_retry(self.backbone, f"ethernet-service sap {svlan} uni port {self.port_backbone}")
            cli_with_retry(self.backbone, f"ethernet-service sap {svlan} cvlan all")
            return self.up()  # Set port to UP after creating the link
        except APIRequestError:
            return False

    def delete_link(self, user_name: str) -> bool:
        if self.svlan is None:
            return True
        
        svlan = str(self.svlan)
        service_name = f"{user_name}_{svlan}"
        try:
            success = self.down()  # Set port to DOWN before deleting the link
            if not success:
                return False
            cli_with_retry(self.backbone, f"no ethernet-service sap {svlan} uni port {self.port_backbone}")
            cli_with_retry(self.backbone, f"no ethernet-service sap {svlan}")
            cli_with_retry(self.backbone, f"no ethernet-service service-name {service_name} svlan {svlan}")
            cli_with_retry(self.backbone, f"no ethernet-service svlan {svlan}")
            return True
        except APIRequestError:
            return False