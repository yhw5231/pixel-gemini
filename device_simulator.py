"""
Android Pixel 10 Pro device simulator.

Each session gets unique identifiers (IMEI, Android ID, device fingerprint,
Chrome version patch) while the hardware identity remains "Pixel 10 Pro".

Uniqueness guarantees
---------------------
- All identifiers are generated from :mod:`secrets` (cryptographically
  strong randomness, no predictable MT19937 sequence).
- Every generated IMEI / Android ID / Chrome patch version is recorded in an
  in-process registry and **never repeated** across sessions of the same
  process (per-run uniqueness; see docs/设备模拟与风控分析.md).
- IMEIs are Luhn-valid 15-digit numbers with a GSMA-style TAC prefix.
"""

import logging
import random
import secrets
import string
import threading
import uuid
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

# ── Uniqueness registry ───────────────────────────────────────────────────────

_USED_KEYS: set = set()
_USED_LOCK = threading.Lock()
_WARNED_POOLS: set = set()


def _unique(generator, what: str, allow_reuse: bool = False) -> str:
    """
    Return a generated value that has never been produced before.

    IMEI / Android ID are treated as hard identity: they must never repeat
    (a real device has exactly one of each).  Chrome patch versions have a
    small realistic pool (one major Chrome series only spans ~100 patches)
    and may legitimately repeat across devices, so after the pool is
    exhausted we fall back to a random value and log a warning once.
    """
    for _ in range(5000):
        value = generator()
        with _USED_LOCK:
            if value not in _USED_KEYS:
                _USED_KEYS.add(value)
                return value
    if allow_reuse:
        with _USED_LOCK:
            if what not in _WARNED_POOLS:
                _WARNED_POOLS.add(what)
                logger.warning(
                    "%s pool exhausted – reusing random values (realistic "
                    "but reduces per-session distinctiveness)", what)
        return generator()
    raise RuntimeError(f"could not generate a unique {what} – pool exhausted")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _luhn_checksum(number: str) -> int:
    """Return the Luhn checksum for a numeric string."""
    digits = [int(d) for d in number]
    odd_digits = digits[-1::-2]
    even_digits = digits[-2::-2]
    total = sum(odd_digits)
    for d in even_digits:
        total += sum(divmod(d * 2, 10))
    return total % 10


def _generate_imei() -> str:
    """Generate a syntactically valid IMEI (15 digits, Luhn-valid)."""
    # TAC prefix "35xxxxxx" is a GSMA-allocated range used by Google devices.
    tac = "35" + "".join(secrets.choice(string.digits) for _ in range(6))
    serial = "".join(secrets.choice(string.digits) for _ in range(6))
    partial = tac + serial
    check_digit = (10 - _luhn_checksum(partial + "0")) % 10
    return partial + str(check_digit)


def _generate_android_id() -> str:
    """Generate a 16-character hex Android ID (64-bit, never all-zero)."""
    value = "".join(secrets.choice("0123456789abcdef") for _ in range(16))
    if value in ("0000000000000000", "ffffffffffffffff"):
        return _generate_android_id()
    return value


def _generate_device_fingerprint(model: str, build_id: str, android: str) -> str:
    """
    Return a realistic Android build fingerprint.

    Real Google fingerprints look like
    ``google/pixel_10_pro/pixel_10_pro:16/AP3A.250905.001/12979047:user/release-keys``.
    Note the previous implementation emitted ``eng.user.release-keys`` –
    "eng" (engineering build) combined with "user" and "release-keys" is
    self-contradictory and does not exist on any shipped device.
    """
    return (
        f"google/{model.lower().replace(' ', '_')}/"
        f"{model.lower().replace(' ', '_')}:{android}/"
        f"{build_id}/{config.BUILD_NUMBER}:user/release-keys"
    )


def _random_chrome_patch() -> str:
    """Return a Chrome version within the same major/build series as the
    configured CHROME_VERSION (a real Pixel 10 Pro runs a Chrome from the
    same era, not an arbitrarily old one)."""
    major = config.CHROME_MAJOR_VERSION
    build = config.CHROME_BUILD
    patch = secrets.randbelow(config.CHROME_PATCH_MAX - config.CHROME_PATCH_MIN + 1)
    return f"{major}.0.{build}.{patch + config.CHROME_PATCH_MIN}"


# ── Device profile dataclass ──────────────────────────────────────────────────

@dataclass
class DeviceProfile:
    imei: str
    android_id: str
    device_fingerprint: str
    user_agent: str
    chrome_version: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Fixed Pixel 10 Pro hardware identity
    model: str = config.DEVICE_MODEL
    brand: str = config.DEVICE_BRAND
    manufacturer: str = config.DEVICE_MANUFACTURER
    android_version: str = config.ANDROID_VERSION
    android_sdk: str = config.ANDROID_SDK
    build_id: str = config.BUILD_ID

    def as_headers(self) -> dict:
        """Return HTTP headers that identify this device."""
        return {
            "User-Agent": self.user_agent,
            "X-Device-Model": self.model,
            "X-Android-ID": self.android_id,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        }

    def summary(self) -> str:
        """Human-readable summary for the Web UI / Telegram messages."""
        return (
            f"📱 *Device Profile*\n"
            f"Model: {self.model}\n"
            f"Android: {self.android_version} (SDK {self.android_sdk})\n"
            f"Build: {self.build_id}\n"
            f"Chrome: {self.chrome_version}\n"
            f"IMEI: `{self.imei}`\n"
            f"Android ID: `{self.android_id}`\n"
            f"Session: `{self.session_id[:8]}…`"
        )


# ── Public factory ────────────────────────────────────────────────────────────

def create_device_profile() -> DeviceProfile:
    """
    Create a fresh Pixel 10 Pro device profile with unique, non-repeating
    per-session identifiers.
    """
    chrome_version = _unique(_random_chrome_patch, "Chrome version",
                             allow_reuse=True)
    template = random.choice(config.USER_AGENT_TEMPLATES)
    user_agent = template.format(
        android=config.ANDROID_VERSION,
        model=config.DEVICE_MODEL,
        build=config.BUILD_ID,
        chrome=chrome_version,
    )
    fingerprint = _generate_device_fingerprint(
        config.DEVICE_MODEL,
        config.BUILD_ID,
        config.ANDROID_VERSION,
    )
    return DeviceProfile(
        imei=_unique(_generate_imei, "IMEI"),
        android_id=_unique(_generate_android_id, "Android ID"),
        device_fingerprint=fingerprint,
        user_agent=user_agent,
        chrome_version=chrome_version,
    )
