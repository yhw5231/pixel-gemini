"""
Configuration and constants for the Pixel 10 Pro Google One Gemini Bot.
"""

import os

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── Web UI ────────────────────────────────────────────────────────────────────
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", "8000"))
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

# ── Device specs – Google Pixel 10 Pro (Android 16) ──────────────────────────
DEVICE_MODEL = "Pixel 10 Pro"
DEVICE_BRAND = "google"
DEVICE_MANUFACTURER = "Google"
ANDROID_VERSION = "16"
ANDROID_SDK = "36"
# Build ID and Chrome version must belong to the same era: a Pixel 10 Pro
# launched Aug 2025 ships with the Sep 2025 Android 16 patch level. The
# Chrome version below tracks the Chrome for Testing build installed in the
# container image (Dockerfile ARG CHROME_VERSION) – using an old/foreign
# Chrome makes the user-agent internally inconsistent and easy to flag as
# fabricated.
BUILD_ID = "AP3A.250905.001"
BUILD_NUMBER = "12979047"
CHROME_VERSION = "154.0.8012.0"
CHROME_MAJOR_VERSION = 154
CHROME_BUILD = 8012
# Randomised patch stays inside the real 154.0.8012.x release window.
CHROME_PATCH_MIN = 1
CHROME_PATCH_MAX = 50

# Locale / timezone used by the simulated device.  The timezone should be
# consistent with the exit IP (see docs/设备模拟与风控分析.md).
DEVICE_LANGUAGE = os.environ.get("DEVICE_LANGUAGE", "en-US")
DEVICE_TIMEZONE = os.environ.get("DEVICE_TIMEZONE", "America/Los_Angeles")
DEVICE_CORES = int(os.environ.get("DEVICE_CORES", "8"))
DEVICE_MEMORY_GB = int(os.environ.get("DEVICE_MEMORY_GB", "8"))
DEVICE_TOUCH_POINTS = int(os.environ.get("DEVICE_TOUCH_POINTS", "5"))
DEVICE_PLATFORM = os.environ.get("DEVICE_PLATFORM", "Android")

# Real Pixel 10 Pro viewport: 1280×2856 px @ density 3.0 → ~427×952 CSS px.
# (The previous 390×844 @ 3.0 was iPhone-like and inconsistent with the UA.)
DEVICE_VIEWPORT_WIDTH = int(os.environ.get("DEVICE_VIEWPORT_WIDTH", "427"))
DEVICE_VIEWPORT_HEIGHT = int(os.environ.get("DEVICE_VIEWPORT_HEIGHT", "952"))
DEVICE_PIXEL_RATIO = float(os.environ.get("DEVICE_PIXEL_RATIO", "3.0"))

# GPU strings reported to WebGL fingerprinting (hide SwiftShader).
# Real Pixel 10 Pro (Tensor G5) uses Imagination Technologies PowerVR DXT-48-1536.
DEVICE_GPU_VENDOR = os.environ.get("DEVICE_GPU_VENDOR", "Imagination Technologies")
DEVICE_GPU_RENDERER = os.environ.get("DEVICE_GPU_RENDERER", "PowerVR DXT-48-1536")

# Realistic Pixel 10 Pro user-agent strings (standard Chrome mobile UA only).
# NOTE: the WebView UA variant (containing "; wv)" and "Version/4.0") was
# removed – embedded-WebView UAs are treated as high-risk by Google's
# sign-in security and make automation stand out more.
USER_AGENT_TEMPLATES = [
    (
        "Mozilla/5.0 (Linux; Android {android}; {model} Build/{build}) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{chrome} Mobile Safari/537.36"
    ),
]

# ── Google URLs ───────────────────────────────────────────────────────────────
GMAIL_LOGIN_URL = "https://accounts.google.com/signin/v2/identifier"
GOOGLE_ONE_URL = "https://one.google.com/"
GOOGLE_ONE_OFFERS_URL = "https://one.google.com/about/plans"

# ── Gemini offer detection keywords ──────────────────────────────────────────
GEMINI_OFFER_KEYWORDS = [
    "gemini pro",
    "gemini advanced",
    "12 month",
    "12-month",
    "free trial",
    "activate",
    "get started",
    "claim offer",
    "redeem",
]

# ── Selenium / WebDriver ──────────────────────────────────────────────────────
WEBDRIVER_TIMEOUT = 30          # seconds – explicit wait
IMPLICIT_WAIT = 10              # seconds
PAGE_LOAD_TIMEOUT = 60          # seconds
HEADLESS = True                 # always headless on Replit

# ── Session storage ───────────────────────────────────────────────────────────
# In-memory dict keyed by Telegram chat_id.
# Values: {"email": ..., "password": ..., "device": <DeviceProfile>, "offer_link": ...}
SESSION_STORE: dict = {}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
