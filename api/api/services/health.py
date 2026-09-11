"""Non-destructive ALE lab health inspection."""

from django.utils import timezone

from ..devices.factory import get_profile
from ..models import HealthCheck, HealthFinding, Switch


def inspect_switch(switch: Switch, requested_by=None) -> HealthCheck:
    """Record an ALE profile inspection without assuming missing commands are safe."""
    adapter = switch.platform
    check = HealthCheck.objects.create(
        switch=switch,
        requested_by=requested_by,
        adapter=adapter,
        status='RUNNING',
    )

    result = get_profile(adapter).inspect(switch)
    check.status = 'ERROR' if result.status == 'UNSUPPORTED' else result.status
    check.error_message = result.error_message
    findings = result.findings
    evidence = result.evidence

    check.evidence = evidence
    check.completed_at = timezone.now()
    check.save(update_fields=['status', 'error_message', 'evidence', 'completed_at'])

    for finding in findings:
        HealthFinding.objects.create(
            switch=switch,
            health_check=check,
            category=finding['category'],
            severity=finding['severity'],
            code=finding['code'],
            message=finding['message'],
        )

    switch.last_health_check = check.completed_at
    switch.health_summary = check.error_message
    if check.status == 'UNREACHABLE':
        switch.health_state = 'UNREACHABLE'
    elif result.status == 'UNSUPPORTED':
        switch.health_state = 'UNKNOWN'
    elif findings:
        switch.health_state = 'DIRTY'
    else:
        switch.health_state = 'UNKNOWN'
    switch.save(update_fields=['last_health_check', 'health_summary', 'health_state'])
    return check
