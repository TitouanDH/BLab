from .aos6 import AOS6Profile
from .aos9 import AOS9Profile
from .aosx import AOSXProfile
from .base import ALEDeviceProfile


PROFILES = {
    'AOS6': AOS6Profile,
    'AOS9': AOS9Profile,
    'AOSX': AOSXProfile,
}


def get_profile(platform: str) -> ALEDeviceProfile:
    profile_class = PROFILES.get(platform)
    return profile_class() if profile_class else ALEDeviceProfile()
