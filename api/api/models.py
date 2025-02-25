import time
import logging
from typing import Any
from django.db import models  # type: ignore
from django.contrib.auth.models import User  # type: ignore
import requests
import paramiko

from requests.packages.urllib3.exceptions import InsecureRequestWarning  # type: ignore

# Configure logging to save logs to a file
logging.basicConfig(filename='/app/logs/api_views.log', level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Suppress SSL warnings (use with caution in production)
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Credentials - consider loading these from environment variables or a secure config
SWITCH_USERNAME = "admin"
SWITCH_PASSWORD = "switch"

class APIRequestError(Exception):
    """Exception raised for errors in API requests."""
    def __init__(self, message: str = "API request failed"):
        self.message = message
        super().__init__(self.message)

COOKIE_CACHE = {}  # Dictionary to store cookies per switch IP

def get_cookie(ip: str, retries: int = 3, delay: float = 1.0) -> str:
    """
    Authenticate and retrieve a session cookie for a given switch.

    Args:
        ip (str): IP address of the network device.
        retries (int): Number of retry attempts.
        delay (float): Delay between retries.

    Returns:
        str: The session cookie.

    Raises:
        APIRequestError: If authentication fails.
    """
    global COOKIE_CACHE
    auth_url = f"https://{ip}?domain=auth&username={SWITCH_USERNAME}&password={SWITCH_PASSWORD}"
    headers = {'Accept': 'application/vnd.alcatellucentaos+json; version=1.0'}

    for attempt in range(retries):
        try:
            response = requests.get(auth_url, headers=headers, verify=False, timeout=5)
            response.raise_for_status()

            # Extract cookie more robustly
            set_cookie = response.headers.get('Set-Cookie')
            if set_cookie:
                # Attempt to find cookie value from header
                cookie_pair = set_cookie.split(';')[0]
                if '=' in cookie_pair:
                    _, cookie_value = cookie_pair.split('=', 1)
                    COOKIE_CACHE[ip] = cookie_value
                    logger.info(f"Authenticated on {ip}; cookie obtained.")
                    return cookie_value
            logger.warning(f"Authentication on {ip} did not return a cookie.")
        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt+1}/{retries}: Authentication failed for {ip}: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise APIRequestError(f"Authentication failed for {ip}: {e}")
    raise APIRequestError(f"Authentication failed for {ip} after {retries} attempts.")

def cli(ip: str, cmd: str, retries: int = 3, delay: float = 1.0) -> Any:
    """
    Executes a CLI command on a network device using HTTPS requests with retries.

    Args:
        ip (str): IP address of the network device.
        cmd (str): CLI command to be executed.
        retries (int): Number of retry attempts.
        delay (float): Delay between retries.

    Returns:
        Any: Output of the CLI command.

    Raises:
        APIRequestError: If the API request fails.
    """
    global COOKIE_CACHE
    payload = {}
    headers = {'Accept': 'application/vnd.alcatellucentaos+json; version=1.0'}

    # Ensure we have a valid cookie
    if ip not in COOKIE_CACHE:
        COOKIE_CACHE[ip] = get_cookie(ip)
    headers['Cookie'] = f"wv_sess={COOKIE_CACHE[ip]}"

    for attempt in range(retries):
        url = "https://{}?domain=cli&cmd={}".format(ip, cmd)
        try:
            response = requests.get(url, headers=headers, data=payload, verify=False, timeout=5)
            if response.status_code != 200:
                try:
                    error_message = response.json().get("error", response.text)
                except ValueError:
                    error_message = response.text

                # Check if the error message indicates that the XXXX already exists.
                if "already" in error_message:
                    logger.warning(f"Command '{cmd}' on {ip} returned a known error: {error_message}")
                else:
                    raise APIRequestError(f"Request to {ip} failed with status {response.status_code}: {error_message}")
            
            data = response.json()
            result = data.get("result", {})
            if result.get("error") == "You must login first":
                logger.info(f"Cookie expired on {ip}, re-authenticating.")
                COOKIE_CACHE[ip] = get_cookie(ip)
                headers['Cookie'] = f"wv_sess={COOKIE_CACHE[ip]}"
                continue

            output = result.get("output")
            if output is None:
                raise APIRequestError("Unexpected response format: 'output' missing.")
            return output

        except requests.exceptions.RequestException as e:
            logger.error(f"Attempt {attempt+1}/{retries}: Request to {ip} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            raise APIRequestError(f"Request to {ip} failed: {e}")

    raise APIRequestError(f"CLI command failed on {ip} after {retries} attempts.")

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
        return f"{self.model}_{self.mngt_IP}"

    def delete(self):
        """
        Deletes the switch and its associated ports.
        """
        logger.info(f"Deleting switch {self.mngt_IP} and its associated ports.")
        ports = Port.objects.filter(switch=self.id)
        for port in ports:
            port.delete()
        return super().delete()

    def changeBanner(self) -> bool:
        """
        Changes the banner of the switch.

        Returns:
            bool: True if the banner is successfully changed, False otherwise.
        """
        if self.mngt_IP == "Not available":
            logger.info(f"Skipping banner update for switch with management IP: {self.mngt_IP}")
            return True

        reservations = Reservation.objects.filter(switch=self)
        user_names = ', '.join(reservation.user.username for reservation in reservations) if reservations.exists() else "nobody"

        text = f"""
***************** LAB RESERVATION SYSTEM ******************
This switch is reserved by : {user_names}
If you access this switch without reservation, please contact admin

To cleanup the switch:
cp init/vc* working
reload from working no rollback-timeout
"""
        logger.info("Updating banner for switch %s", self.mngt_IP)
        try:
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.mngt_IP, username=SWITCH_USERNAME, password=SWITCH_PASSWORD, port=22, timeout=5)
                with ssh.open_sftp() as sftp:
                    # Open file in write mode; adjust path if necessary
                    with sftp.file('switch/pre_banner.txt', "w") as file:
                        file.write(text)
                logger.info("Banner updated successfully for switch %s", self.mngt_IP)
                return True
        except paramiko.SSHException as ssh_exception:
            logger.error("SSH Connection Error on %s: %s", self.mngt_IP, ssh_exception)
            return False
        except Exception as e:
            logger.error("Unexpected error in changeBanner for %s: %s", self.mngt_IP, e)
            return False

    def cleanup(self) -> bool:
        """
        Cleans up the switch configuration by copying files from init to working directory.
        Only performs cleanup if the switch is not currently reserved.

        Returns:
            bool: True if cleanup was successful, False otherwise.
        """
        logger.info("Attempting to clean up switch %s", self.mngt_IP)
        if Reservation.objects.filter(switch=self).exists():
            logger.info("Switch %s is reserved. Skipping cleanup.", self.mngt_IP)
            return False

        try:
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(self.mngt_IP, username=SWITCH_USERNAME, password=SWITCH_PASSWORD, port=22, timeout=5)

                # Execute copy command
                stdin, stdout, stderr = ssh.exec_command("cp init/vc* working")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.error("Copy command failed on %s with exit status %s: %s", self.mngt_IP, exit_status, error_output)
                    return False

                # Execute reload command with pseudo-tty for interactive confirmation
                stdin, stdout, stderr = ssh.exec_command("reload from working no rollback-timeout", get_pty=True)
                time.sleep(1)  # Wait for the prompt
                stdin.write('y\n')
                stdin.flush()
                logger.info("Successfully initiated reload for switch %s", self.mngt_IP)
                return True
        except (paramiko.SSHException, Exception) as e:
            logger.error("Error during cleanup for switch %s: %s", self.mngt_IP, e)
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
        return f"{self.switch}_{self.user}"

    def delete(self, username):
        """
        Deletes the reservation and releases associated ports.
        Attempts to clean up the switch if it's the last reservation.

        Args:
            username (str): Username of the user making the deletion.

        Returns:
            bool: True if the reservation was successfully deleted, False otherwise.
        """
        logger.info(f"Deleting reservation for user {username} on switch {self.switch.mngt_IP}.")
        failure_on_port_release = False
        ports = Port.objects.filter(switch=self.switch)
        for port in ports:
            if port.svlan is not None:
                connected = Port.objects.filter(svlan=port.svlan)
                for conn in connected:
                    if conn.delete_link(username):
                        conn.svlan = None
                        conn.save()
                    else:
                        failure_on_port_release = True

        if not failure_on_port_release:
            # Delete the reservation
            super().delete()
            logger.info(f"Reservation for user {username} on switch {self.switch.mngt_IP} deleted successfully.")
            
            # Check if there are any remaining reservations for the switch
            remaining_reservations = Reservation.objects.filter(switch=self.switch)
            if not remaining_reservations.exists():
                # Attempt to clean up the switch, but don't block if it fails
                cleanup_success = self.switch.cleanup()
                if not cleanup_success:
                    logger.warning(f"Failed to clean up switch {self.switch.mngt_IP} after releasing last reservation")
            else:
                logger.info(f"Skipping cleanup for switch {self.switch.mngt_IP} as there are remaining reservations")

            return True
        else:
            logger.error(f"Failed to release all ports for switch {self.switch.mngt_IP}")
            return False


class Port(models.Model):
    """
    Represents a port on a switch.

    Attributes:
        switch (Switch): Switch to which the port belongs.
        port_switch (str): Port identifier on the switch.
        backbone (str): Backbone information.
        port_backbone (str): Port identifier on the backbone.
        svlan (int): Service VLAN associated with the port.
        status (str): Status of the port, either 'UP' or 'DOWN'.
    """
    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    port_switch = models.CharField(max_length=255)
    backbone = models.CharField(max_length=255)
    port_backbone = models.CharField(max_length=255)
    svlan = models.IntegerField(default=None, blank=True, null=True)
    status = models.CharField(
        max_length=10, 
        default='DOWN', 
        null=True, 
        blank=True, 
        choices=[('UP', 'Up'), ('DOWN', 'Down')]
    )

    def __str__(self):
        return f"{self.switch}_{self.port_backbone}"

    def up(self) -> bool:
        try:
            cli(self.backbone, f"interfaces {self.port_backbone} admin-state enable")
            self.status = 'UP'
            self.save()
            logger.info("Port %s brought up successfully", self.port_backbone)
            return True
        except APIRequestError as e:
            logger.error("Failed to bring up port %s: %s", self.port_backbone, e)
            return False

    def down(self) -> bool:
        try:
            cli(self.backbone, f"interfaces {self.port_backbone} admin-state disable")
            self.status = 'DOWN'
            self.save()
            logger.info("Port %s brought down successfully", self.port_backbone)
            return True
        except APIRequestError as e:
            logger.error("Failed to bring down port %s: %s", self.port_backbone, e)
            return False

    def create_link(self, user_name: str) -> bool:
        """
        Creates a link configuration on the switch.

        Args:
            user_name (str): The username for naming the service.

        Returns:
            bool: True if link creation is successful, False otherwise.
        """
        svlan_str = str(self.svlan)
        service_name = f"{user_name}_{svlan_str}"
        try:
            cli(self.backbone, f"ethernet-service svlan {svlan_str} admin-state enable")
            cli(self.backbone, f"ethernet-service service-name {service_name} svlan {svlan_str}")
            cli(self.backbone, f"ethernet-service sap {svlan_str} service-name {service_name}")
            cli(self.backbone, f"ethernet-service sap {svlan_str} uni port {self.port_backbone}")
            cli(self.backbone, f"ethernet-service sap {svlan_str} cvlan all")
            return self.up()  # Bring the port up after link creation
        except APIRequestError as e:
            logger.error("Failed to create link on port %s: %s", self.port_backbone, e)
            return False

    def delete_link(self, user_name: str) -> bool:
        """
        Deletes the link configuration on the switch.

        Args:
            user_name (str): The username for naming the service.

        Returns:
            bool: True if link deletion is successful, False otherwise.
        """
        if self.svlan is None:
            return True

        svlan_str = str(self.svlan)
        service_name = f"{user_name}_{svlan_str}"
        try:
            if not self.down():
                return False
            cli(self.backbone, f"no ethernet-service sap {svlan_str} uni port {self.port_backbone}")
            cli(self.backbone, f"no ethernet-service sap {svlan_str}")
            cli(self.backbone, f"no ethernet-service service-name {service_name} svlan {svlan_str}")
            cli(self.backbone, f"no ethernet-service svlan {svlan_str}")
            logger.info("Link deleted successfully on port %s", self.port_backbone)
            return True
        except APIRequestError as e:
            logger.error("Failed to delete link on port %s: %s", self.port_backbone, e)
            return False

    def verify_configuration(self, svlan: str, expected_lines: int = 4) -> bool:
        """
        Verifies the configuration of the link.

        Args:
            svlan (str): Service VLAN to verify.
            expected_lines (int): Number of expected lines in the configuration.

        Returns:
            bool: True if the configuration is correct, False otherwise.
        """
        logger.info("Verifying configuration for VLAN %s on port %s", svlan, self.port_backbone)
        config = cli(self.backbone, "show configuration snapshot vlan")
        config_lines = [line.strip() for line in config.splitlines()]
        sap_lines = [line for line in config_lines if f"sap {svlan}" in line]

        if len(sap_lines) == expected_lines:
            logger.info("Configuration verified successfully for port %s", self.port_backbone)
            return True
        else:
            logger.warning("Configuration verification failed for port %s: expected %s lines, got %s",
                           self.port_backbone, expected_lines, len(sap_lines))
            return False
