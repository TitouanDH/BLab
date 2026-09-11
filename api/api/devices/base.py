"""ALE device profile contract used by health and cleanup services."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeviceInspection:
    status: str
    evidence: dict[str, Any]
    findings: list[dict[str, Any]]
    error_message: str = ''


class ALEDeviceProfile:
    platform = 'UNKNOWN'

    def inspect(self, switch) -> DeviceInspection:
        return DeviceInspection(
            status='UNSUPPORTED',
            evidence={'platform': self.platform},
            findings=[{
                'category': 'CONFIGURATION',
                'severity': 'CRITICAL',
                'code': 'COMMANDS_NOT_CONFIGURED',
                'message': f'No {self.platform} health commands have been configured yet.',
            }],
            error_message='Manual ALE command mapping is required before inspection can verify this device.',
        )

    def cleanup(self, switch) -> DeviceInspection:
        return DeviceInspection(
            status='UNSUPPORTED',
            evidence={'platform': self.platform},
            findings=[{
                'category': 'CLEANUP',
                'severity': 'CRITICAL',
                'code': 'CLEANUP_NOT_CONFIGURED',
                'message': f'No {self.platform} cleanup commands have been configured yet.',
            }],
            error_message='Cleanup is disabled until the approved ALE commands are provided.',
        )
