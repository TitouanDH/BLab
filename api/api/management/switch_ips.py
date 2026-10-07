"""The --ips / --file arguments of the management commands that work on a list of switches."""
from django.core.management.base import CommandError


def add_ip_arguments(parser) -> None:
    """Either one is enough; ips_from() reads them back."""
    parser.add_argument(
        '--ips',
        type=str,
        help='Comma-separated list of IP addresses (e.g., "192.168.1.1,192.168.1.2")'
    )
    parser.add_argument(
        '--file',
        type=str,
        help='Path to a file containing IP addresses (one per line)'
    )


def ips_from(options) -> list:
    """The IPs given by --ips, or else read from --file (blank lines and # comments skipped)."""
    if options['ips']:
        ips = [ip.strip() for ip in options['ips'].split(',')]
    elif options['file']:
        try:
            with open(options['file'], 'r') as f:
                ips = [line.strip() for line in f
                       if line.strip() and not line.strip().startswith('#')]
        except FileNotFoundError:
            raise CommandError(f"File not found: {options['file']}")
    else:
        raise CommandError("Please provide either --ips or --file parameter")

    if not ips:
        raise CommandError("No IP addresses provided")
    return ips
