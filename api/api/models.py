from django.utils import timezone
import time
import logging
from typing import Any
from django.db import models  # type: ignore
from django.contrib.auth.models import User  # type: ignore
import requests
import paramiko
import re

from requests.packages.urllib3.exceptions import InsecureRequestWarning  # type: ignore

# Configure logging to save logs to a file
logging.basicConfig(filename='/app/logs/api_models.log', level=logging.INFO, 
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

                logger.error(f"Request to {ip} failed with status {response.status_code}: {error_message}")
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
        Cleans up the switch configuration by restoring clean state from init directory.
        Only performs cleanup if the switch is not currently reserved.
        This provides a clean slate after users release their reservations.

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

                # Clean working directory completely
                logger.info("Cleaning working directory on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("rm -rf working/*")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning("Working directory cleanup on %s returned status %s: %s", self.mngt_IP, exit_status, error_output)

                # Copy all clean files from init to working
                logger.info("Restoring clean configuration from init directory on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("cp -r init/* working/")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.error("Copy from init to working failed on %s with exit status %s: %s", self.mngt_IP, exit_status, error_output)
                    return False

                # Verify essential files are present before reload
                logger.info("Verifying essential files are present before reload on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("ls working/")
                if stdout.channel.recv_exit_status() == 0:
                    working_contents = stdout.read().decode('utf-8').strip()
                    logger.info("Working directory contents: %s", working_contents)
                    
                    # Check for essential files
                    if '.img' not in working_contents:
                        logger.error("No image files found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    if 'pkg' not in working_contents:
                        logger.error("No pkg directory found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    if 'vcboot.cfg' not in working_contents:
                        logger.error("No vcboot.cfg found in working directory on switch %s", self.mngt_IP)
                        return False
                    
                    logger.info("All essential files verified in working directory on switch %s", self.mngt_IP)
                else:
                    logger.error("Could not verify working directory contents on switch %s", self.mngt_IP)
                    return False

                # Also update certified directory as backup
                logger.info("Updating certified directory backup on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("rm -rf certified/*")
                stdin, stdout, stderr = ssh.exec_command("cp -r init/* certified/")
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_output = stderr.read().decode('utf-8')
                    logger.warning("Copy to certified failed on %s: %s", self.mngt_IP, error_output)

                # Execute reload command with pseudo-tty for interactive confirmation
                logger.info("Initiating reload for clean state on switch %s", self.mngt_IP)
                stdin, stdout, stderr = ssh.exec_command("reload from working no rollback-timeout", get_pty=True)
                time.sleep(1)  # Wait for the prompt
                stdin.write('y\n')
                stdin.flush()
                logger.info("Successfully initiated cleanup reload for switch %s", self.mngt_IP)
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
        creation_date (datetime): Date and time when the reservation was created.
        end_date (datetime): Date and time when the reservation ends.
    """
    switch = models.ForeignKey(Switch, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    creation_date = models.DateTimeField(auto_now_add=True)
    end_date = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.switch}_{self.user}"

    @classmethod
    def cleanup_expired_reservations(cls):
        """
        Cleans up expired reservations automatically.
        """
        from django.utils import timezone
        
        logger.info("Cleaning up expired reservations...")
        expired_reservations = cls.objects.filter(end_date__lt=timezone.now()).exclude(end_date__isnull=True)
        
        cleaned_count = 0
        for reservation in expired_reservations:
            logger.info(f"Found expired reservation: {reservation.user.username} on switch {reservation.switch.mngt_IP}")
            try:
                # Use cleanup=True for expired reservations to clean up automatically
                if reservation.delete(reservation.user.username, cleanup_switch=True):
                    cleaned_count += 1
                    logger.info(f"Successfully cleaned up expired reservation for {reservation.user.username}")
                else:
                    logger.error(f"Failed to cleanup expired reservation for {reservation.user.username}")
            except Exception as e:
                logger.error(f"Error cleaning up reservation for {reservation.user.username}: {e}")
        
        logger.info(f"Cleanup completed. Cleaned {cleaned_count} expired reservations")
        return cleaned_count

    def delete(self, username, cleanup_switch=False):
        """
        Deletes the reservation and releases associated ports.
        Optionally cleans up the switch if it's the last reservation.

        Args:
            username (str): Username of the user making the deletion.
            cleanup_switch (bool): Whether to cleanup the switch after releasing

        Returns:
            bool: True if the reservation was successfully deleted, False otherwise.
        """
        logger.info(f"Deleting reservation for user {username} on switch {self.switch.mngt_IP}.")
        failure_on_port_release = False
        ports = Port.objects.filter(switch=self.switch)
        
        # First, disconnect all links for this switch
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
            
            # Only cleanup if explicitly requested and it's the last reservation
            remaining_reservations = Reservation.objects.filter(switch=self.switch)
            if not remaining_reservations.exists() and cleanup_switch:
                cleanup_success = self.switch.cleanup()
                if not cleanup_success:
                    logger.warning(f"Failed to clean up switch {self.switch.mngt_IP} after releasing last reservation")
                else:
                    logger.info(f"Switch {self.switch.mngt_IP} cleaned up successfully")
            elif not remaining_reservations.exists():
                logger.info(f"Switch {self.switch.mngt_IP} is free but cleanup was not requested")
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

    @staticmethod
    def create_link(portA, portB, user_name: str) -> bool:
        """
        Creates a link configuration between two ports.

        Args:
            portA (Port): The first port.
            portB (Port): The second port.
            user_name (str): The username for naming the service.

        Returns:
            bool: True if link creation is successful, False otherwise.
        """
        svlan_str = str(portA.svlan)
        service_name = f"{user_name}_{svlan_str}"
        try:
            cli(portA.backbone, f"ethernet-service svlan {svlan_str} admin-state enable")
            cli(portA.backbone, f"ethernet-service service-name {service_name} svlan {svlan_str}")
            cli(portA.backbone, f"ethernet-service sap {svlan_str} service-name {service_name}")
            cli(portA.backbone, f"ethernet-service sap {svlan_str} uni port {portA.port_backbone}")
            cli(portA.backbone, f"ethernet-service sap {svlan_str} uni port {portB.port_backbone}")
            cli(portA.backbone, f"ethernet-service sap {svlan_str} cvlan all")
            return portA.up() and portB.up()  # Bring both ports up after link creation
        except APIRequestError as e:
            logger.error("Failed to create link between ports %s and %s: %s", portA.port_backbone, portB.port_backbone, e)
            return False

    @staticmethod
    def delete_link(portA, portB, user_name: str) -> bool:
        """
        Deletes the link configuration between two ports.

        Args:
            portA (Port): The first port.
            portB (Port): The second port.
            user_name (str): The username for naming the service.

        Returns:
            bool: True if link deletion is successful, False otherwise.
        """
        if portA.svlan is None:
            return True

        svlan_str = str(portA.svlan)
        service_name = f"{user_name}_{svlan_str}"
        try:
            if not portA.down() or not portB.down():
                return False
            cli(portA.backbone, f"no ethernet-service sap {svlan_str} uni port {portA.port_backbone}")
            cli(portA.backbone, f"no ethernet-service sap {svlan_str} uni port {portB.port_backbone}")
            cli(portA.backbone, f"no ethernet-service sap {svlan_str}")
            cli(portA.backbone, f"no ethernet-service service-name {service_name} svlan {svlan_str}")
            cli(portA.backbone, f"no ethernet-service svlan {svlan_str}")
            logger.info("Link deleted successfully between ports %s and %s", portA.port_backbone, portB.port_backbone)
            return True
        except APIRequestError as e:
            logger.error("Failed to delete link between ports %s and %s: %s", portA.port_backbone, portB.port_backbone, e)
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

        # Expand port ranges in sap_lines
        expanded_sap_lines = []
        for line in sap_lines:
            parts = line.split()
            for i, part in enumerate(parts):
                if "port" in part:
                    port_range = parts[i + 1]
                    expanded_ports = expand_port_range(port_range)
                    for port in expanded_ports:
                        new_line = line.replace(port_range, port)
                        expanded_sap_lines.append(new_line)
                    break
            else:
                expanded_sap_lines.append(line)

        if len(expanded_sap_lines) == expected_lines:
            logger.info("Configuration verified successfully for port %s", self.port_backbone)
            return True
        else:
            logger.warning("Configuration verification failed for port %s: expected %s lines, got %s",
                           self.port_backbone, expected_lines, len(expanded_sap_lines))
            return False

def expand_port_range(port_range: str) -> list:
    """
    Expands port ranges into individual ports.

    Args:
        port_range (str): The port range string (e.g., "1/1/1-2").

    Returns:
        list: A list of individual port strings.
    """
    match = re.match(r"(\d+/\d+/\d+)-(\d+)", port_range)
    if not match:
        return [port_range]
    base_port, end_port = match.groups()
    slot, sub_slot, start_port = map(int, base_port.split('/'))
    return [f"{slot}/{sub_slot}/{port}" for port in range(start_port, int(end_port) + 1)]

class TopologyShare(models.Model):
    """
    Represents a topology sharing between two users.
    """
    owner = models.ForeignKey(User, related_name='shared_topologies', on_delete=models.CASCADE)
    target = models.ForeignKey(User, related_name='received_topologies', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Topology of {self.owner.username} shared with {self.target.username}"
