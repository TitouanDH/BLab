"""
Prepares newly installed lab switches: builds init/ (the config a Cleanup restores) and,
with --reload, runs the Cleanup itself through LabSwitch, the same sequence as a Release.
"""
import logging
from typing import NamedTuple

from django.core.management.base import BaseCommand

from api.backbone import SWITCH_PASSWORD, SWITCH_USERNAME
from api.fake_devices import require_real_devices
from api.lab_switch import LabSwitch, LabSwitchError, Session, ssh
from api.management.switch_ips import add_ip_arguments, ips_from
from api.models import Switch

logger = logging.getLogger(__name__)

OLD_FILES = ('swlog*', 'vcboot.cfg*', 'ovng*')


class InitPart(NamedTuple):
    name: str
    marker: str  # how it shows in an ls listing
    copy: str    # the copy command, formatted with the source directory


# What init/ is built from: each part is copied from the first of working/, certified/ that has it
INIT_PARTS = (
    InitPart('image files', '.img', 'cp {}/*.img init/'),
    InitPart('pkg directory', 'pkg', 'cp -r {}/pkg init/'),
)
SOURCES = ('working', 'certified')


def config_text(switch_model: str) -> str:
    """init/vcboot.cfg: names the switch and turns on the LLDP TLVs populate_ports discovers with."""
    return f'''system name "{switch_model}"
session prompt default "{switch_model}"
session cli timeout 5555

health threshold memory 80

aaa authentication console "local"
aaa authentication ssh "local"

lldp nearest-bridge chassis tlv management port-description enable system-name enable system-description enable
lldp nearest-bridge chassis tlv management management-address enable
lldp nearest-bridge chassis tlv dot3 mac-phy enable

ip static-route 10.0.0.0/8 gateway 10.69.144.129 metric 1
ip static-route 10.69.0.0/16 gateway 10.69.144.129 metric 1
ip static-route 135.118.225.0/24 gateway 10.69.144.129 metric 1

auto-fabric admin-state disable
mvrp disable
command-log enable
'''


class Command(BaseCommand):
    help = 'Prepares newly installed switches by setting up init folder and basic configuration'

    def add_arguments(self, parser):
        add_ip_arguments(parser)
        parser.add_argument(
            '--username',
            type=str,
            default=SWITCH_USERNAME,
            help=f'SSH username (default: {SWITCH_USERNAME})'
        )
        parser.add_argument(
            '--password',
            type=str,
            default=SWITCH_PASSWORD,
            help=f'SSH password (default: {SWITCH_PASSWORD})'
        )
        parser.add_argument(
            '--skip-cleanup',
            action='store_true',
            help='Skip removing old logs and configs (swlog*, vcboot.cfg*, ovng*)'
        )
        parser.add_argument(
            '--skip-init',
            action='store_true',
            help='Skip creating init folder and copying files'
        )
        parser.add_argument(
            '--skip-config',
            action='store_true',
            help='Skip creating vcboot.cfg configuration'
        )
        parser.add_argument(
            '--reload',
            action='store_true',
            help='Run a Cleanup (init -> working, reload) to apply the configuration (required for LLDP to work)'
        )

    def handle(self, *args, **options):
        require_real_devices('prepare_switches')
        ips = ips_from(options)
        connect = ssh(options['username'], options['password'])

        self.stdout.write(f'Preparing {len(ips)} switch(es)...')

        successful = 0
        failed = 0

        for ip in ips:
            try:
                self.prepare_switch(ip, connect, options)
                successful += 1
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f'✗ Failed to prepare {ip}: {e}'))
                logger.error("Failed to prepare switch %s: %s", ip, e)

        self.stdout.write('\n' + '='*50)
        self.stdout.write('Summary:')
        self.stdout.write(f'  Successfully prepared: {successful}')
        self.stdout.write(f'  Failed: {failed}')
        self.stdout.write('='*50)

    def prepare_switch(self, ip, connect, options):
        """Builds init/ over one session; --reload then runs the Cleanup a Release runs."""
        self.stdout.write(f'\nPreparing switch: {ip}')
        switch_model = self.get_switch_model(ip)

        self.stdout.write(f'  Connecting to {ip}...')
        with connect(ip) as session:
            if not options['skip_cleanup']:
                self.stdout.write('  Removing old logs and configs...')
                self.remove_old_files(session, ip)
            if not options['skip_init']:
                self.stdout.write('  Setting up init folder...')
                self.setup_init_folder(session, ip)
            if not options['skip_config']:
                self.stdout.write(f'  Creating init/vcboot.cfg with model: {switch_model}')
                session.write_file('init/vcboot.cfg', config_text(switch_model))

        if options['reload']:
            self.stdout.write('  Cleanup: restoring init to working and reloading...')
            LabSwitch(ip, connect).restore_init_and_reload()
            self.stdout.write('    ⚠ Switch will reboot - LLDP configuration will be active after restart')
        else:
            self.stdout.write('  ⚠ Configuration created but not applied. Use --reload to activate LLDP configuration.')

        self.stdout.write(self.style.SUCCESS(f'✓ Successfully prepared switch {ip}'))
        logger.info("Successfully prepared switch %s", ip)

    def get_switch_model(self, ip):
        """The model recorded by populate_switches, or a name made from the IP."""
        switch = Switch.objects.filter(mngt_IP=ip).first()
        if switch and switch.model:
            return switch.model
        return f"OS6900-{ip.replace('.', '_')}"

    def remove_old_files(self, session: Session, ip):
        """Old logs and configs only get in the way, and missing ones are no failure."""
        for pattern in OLD_FILES:
            result = session.run(f'rm {pattern}')
            if result.status != 0 and 'No such file or directory' not in result.error:
                logger.warning("'rm %s' on %s returned %s: %s", pattern, ip, result.status, result.error.strip())

    def setup_init_folder(self, session: Session, ip):
        """Builds a fresh init/ from the firmware in working/, or certified/ for what working/ lacks."""
        listings = {}
        for source in SOURCES:
            result = session.run(f'ls {source}/')
            listings[source] = result.output if result.status == 0 else ''
            self.stdout.write(f'    {source}/: ' + ', '.join(
                f'{part.marker}={part.marker in listings[source]}' for part in INIT_PARTS))
        for part in INIT_PARTS:
            if not any(part.marker in listing for listing in listings.values()):
                raise LabSwitchError(f"No {part.name} ({part.marker}) found in working or certified directories")

        result = session.run('rm -rf init')
        if result.status != 0:
            logger.warning("Failed to remove existing init directory on %s: %s", ip, result.error.strip())
        result = session.run('mkdir -p init')
        if result.status != 0:
            raise LabSwitchError(f"Failed to create init directory: {result.error.strip()}")

        for part in INIT_PARTS:
            for source in [s for s in SOURCES if part.marker in listings[s]]:
                result = session.run(part.copy.format(source))
                if result.status == 0:
                    self.stdout.write(f'    ✓ {part.name} copied from {source}')
                    break
                logger.warning("Failed to copy %s from %s on %s: %s", part.name, source, ip, result.error.strip())
            else:
                raise LabSwitchError(f"Failed to copy {part.name} from working or certified")

        result = session.run('ls init/')
        if result.status != 0:
            raise LabSwitchError(f"Could not verify init directory: {result.error.strip()}")
        self.stdout.write(f"    init/: {', '.join(result.output.split())}")
        for part in INIT_PARTS:
            if part.marker not in result.output:
                raise LabSwitchError(f"No {part.name} found in init directory after setup")
