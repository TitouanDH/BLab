"""
Onboards, in bulk, the Switches of the Management network that BLab doesn't record yet (#55,
api.onboarding): records them, gives admin the lab's secret password, installs their Golden init
and Cleans them up, reads their Ports by LLDP, and puts in service each one its first Inspection
finds clean. Without --apply it only scans and says what it would do. Run it again to go on.
"""
from django.core.management.base import BaseCommand

from api import management_network
from api.management.switch_ips import add_ip_arguments, ips_from
from api.onboarding import Onboarder


class Command(BaseCommand):
    help = ('Scans the Management network and onboards the Switches BLab does not record yet '
            '(only says what it would do without --apply)')

    def add_arguments(self, parser):
        parser.add_argument('--subnet', action='append', default=[],
                            help='A subnet to scan, e.g. 10.69.144.128/26 (repeatable; default: BLAB_MANAGEMENT_NETWORKS)')
        add_ip_arguments(parser)
        parser.add_argument('--backbones', type=str, default='',
                            help='Comma-separated Backbones to read Ports from, besides those BLab knows by its Ports')
        parser.add_argument('--apply', action='store_true',
                            help='Onboard them; without it, only scan and say what it would do')

    def handle(self, *args, **options):
        ips = ips_from(options, required=False)
        subnets = options['subnet'] or ([] if ips else [str(s.network) for s in management_network.subnets()])
        if not options['apply']:
            self.stdout.write('Dry run: BLab only reads. Run again with --apply to onboard.')
        backbones = [ip.strip() for ip in options['backbones'].split(',') if ip.strip()]
        outcomes = Onboarder(say=self.stdout.write, backbones=backbones).run(subnets, ips, apply=options['apply'])
        self.stdout.write('\n' + '=' * 70)
        for outcome in outcomes:
            self.stdout.write(str(outcome))
        if not outcomes:
            self.stdout.write('Nothing to onboard.')
        self.stdout.write('=' * 70)
