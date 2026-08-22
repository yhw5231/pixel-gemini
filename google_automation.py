"""
Google One automation using Selenium.

Logs into a Gmail account, navigates to Google One, detects the
12-month free Gemini Pro offer, and returns the activation / payment link.
"""

import logging
import os
import random as _random
import re
import shutil
import subprocess
import time
import zlib
from urllib.parse import urlparse
from typing import Optional, Tuple

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

import config
from device_simulator import DeviceProfile

logger = logging.getLogger(__name__)


# ── Stealth helpers ───────────────────────────────────────────────────────────

# Runs on every new document *before* page scripts, hiding the most common
# automation fingerprints.  Note: this is best-effort hardening only – it
# cannot defeat Google's server-side risk engine (see docs/设备模拟与风控分析.md).
STEALTH_SCRIPT = """
() => {
  const define = (obj, prop, val) => {
    try { Object.defineProperty(obj, prop, { get: () => val }); } catch (e) {}
  };
  define(navigator, 'webdriver', undefined);
  define(navigator, 'maxTouchPoints', %(touch_points)d);
  define(navigator, 'hardwareConcurrency', %(cores)d);
  define(navigator, 'deviceMemory', %(memory)d);
  define(navigator, 'platform', 'Linux armv8l');
  define(navigator, 'vendor', 'Google Inc.');
  define(navigator, 'languages', ['%(lang)s']);
  // A mobile browser exposes a plugin list, but it is always empty.
  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => {
        const p = [];
        p.item = () => null; p.namedItem = () => null; p.refresh = () => {};
        return p;
      },
    });
  } catch (e) {}
  window.chrome = window.chrome || { runtime: {}, app: {}, csi: () => {}, loadTimes: () => {} };
  // Headless Chrome used to leak these flags.
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
  delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

  // WebGL: hide the software rasterizer (SwiftShader) and report a phone GPU
  // instead, so fingerprinters don't see a datacenter/virtual-GPU signal.
  const patchGL = (proto) => {
    if (!proto || !proto.getParameter) return;
    const orig = proto.getParameter;
    proto.getParameter = function (p) {
      if (p === 37445) return '%(gpu_vendor)s';   // UNMASKED_VENDOR_WEBGL
      if (p === 37446) return '%(gpu_renderer)s'; // UNMASKED_RENDERER_WEBGL
      return orig.call(this, p);
    };
  };
  patchGL(WebGLRenderingContext.prototype);
  patchGL(WebGL2RenderingContext && WebGL2RenderingContext.prototype);

  // Canvas: add per-session sub-perceptual alpha noise so that every
  // simulated "device" yields its own canvas fingerprint, instead of all
  // sessions sharing one identical binary hash (cross-account correlation).
  (() => {
    let s = %(seed)d >>> 0;
    const rand = () => { s = (s * 1103515245 + 12345) >>> 0; return s / 4294967296; };
    const noise = () => 0.96 + rand() * 0.08;
    const methods = ['fillRect', 'strokeRect', 'fillText', 'strokeText',
                     'arc', 'rect', 'fill', 'stroke', 'drawImage'];
    for (const m of methods) {
      const proto = CanvasRenderingContext2D.prototype;
      if (!proto[m]) continue;
      const orig = proto[m];
      proto[m] = function (...args) {
        try { this.globalAlpha = Math.min(1, Math.max(0.02, this.globalAlpha * noise())); } catch (e) {}
        return orig.apply(this, args);
      };
    }
  })();
}
"""


def _apply_stealth(driver: webdriver.Chrome, profile: DeviceProfile) -> None:
    """Inject anti-automation script and consistent device signals."""
    script = STEALTH_SCRIPT % {
        "touch_points": config.DEVICE_TOUCH_POINTS,
        "cores": config.DEVICE_CORES,
        "memory": config.DEVICE_MEMORY_GB,
        "lang": config.DEVICE_LANGUAGE,
        "gpu_vendor": config.DEVICE_GPU_VENDOR,
        "gpu_renderer": config.DEVICE_GPU_RENDERER,
        "seed": zlib.crc32(profile.session_id.encode("utf-8")) & 0xFFFFFFFF,
    }
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": script}
        )
    except WebDriverException as exc:
        logger.warning("Stealth script injection failed (continuing): %s", exc)
    try:
        driver.execute_cdp_cmd(
            "Emulation.setTimezoneOverride",
            {"timezoneId": config.DEVICE_TIMEZONE},
        )
    except WebDriverException as exc:
        logger.warning("Timezone override failed (continuing): %s", exc)

    # sec-ch-ua / Client Hints consistency: without this, Chrome advertises
    # the *binary's* real version (e.g. 120 from Debian Chromium) while the
    # UA claims 137.0.7278.x – a cross-checkable contradiction.
    major = profile.chrome_version.split(".")[0]
    brand = {"brand": "Google Chrome", "version": major}
    chromium = {"brand": "Chromium", "version": major}
    full_brand = {"brand": "Google Chrome", "version": profile.chrome_version}
    full_chromium = {"brand": "Chromium", "version": profile.chrome_version}
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": profile.user_agent,
            "acceptLanguage": config.DEVICE_LANGUAGE,
            "platform": config.DEVICE_PLATFORM,
            "userAgentMetadata": {
                "brands": [chromium, brand],
                "fullVersionList": [full_chromium, full_brand],
                "platform": config.DEVICE_PLATFORM,
                "platformVersion": f"{config.ANDROID_VERSION}.0.0",
                "architecture": "arm",
                "model": config.DEVICE_MODEL,
                "mobile": True,
            },
        })
    except WebDriverException as exc:
        logger.warning("User-Agent metadata override failed (continuing): %s", exc)


# ── Human-like interaction helpers ────────────────────────────────────────────

def _human_sleep(lo: float = 1.2, hi: float = 2.8) -> None:
    """Sleep a random amount of time in [lo, hi] to mimic human pacing."""
    time.sleep(_random.uniform(lo, hi))


def _type_human(element, text: str) -> None:
    """Type *text* character by character with human-like key delays."""
    for ch in text:
        element.send_keys(ch)
        time.sleep(_random.uniform(0.02, 0.09))


# ── Driver factory ────────────────────────────────────────────────────────────

def _driver_major(path: str) -> Optional[int]:
    """Extract the major Chrome version reported by a chromedriver binary."""
    try:
        out = subprocess.run([path, "--version"], capture_output=True,
                             text=True, timeout=10)
        m = re.search(r"(\d+)\.\d+\.\d+", out.stdout or out.stderr)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


def _detect_chrome_major() -> Optional[int]:
    """Detect the major version of the installed Chrome/Chromium."""
    # 1. Look for Chrome/Chromium on PATH (Linux/macOS/Windows).
    for name in ("google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "chrome", "msedge"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            out = subprocess.run([path, "--version"], capture_output=True,
                                 text=True, timeout=10)
            m = re.search(r"(\d+)\.\d+\.\d+", out.stdout + out.stderr)
            if m:
                return int(m.group(1))
        except Exception:
            continue
    # 2. Windows registry fallback (Chrome).
    if os.name == "nt":
        for hive in (
            r"HKLM\SOFTWARE\Google\Chrome\BLBeacon",
            r"HKLM\SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon",
        ):
            try:
                out = subprocess.run(["reg", "query", hive, "/ve"],
                                     capture_output=True, text=True, timeout=10)
                m = re.search(r"(\d+)\.\d+\.\d+", out.stdout)
                if m:
                    return int(m.group(1))
            except Exception:
                continue
    return None


def _resolve_service() -> Tuple[Service, str]:
    """
    Resolve the chromedriver Service.

    Returns ``(service, source_label)`` where *source_label* is ``"sm"``
    (Selenium Manager, auto-installs when no matching driver is cached) or
    ``"explicit"`` (user-provided path).

    *Explicit path* logic
    ---------------------
    - If the file does not exist → fall back to SM.
    - If the driver's major version does NOT match the installed Chrome
      major version → log a warning and fall back to SM (so it auto-downloads
      a matching driver).
    - If the version cannot be compared (e.g. Chrome detection fails) → try
      the explicit driver anyway; if it fails at session creation the caller
      will retry with SM.
    """
    path = os.environ.get("CHROMEDRIVER_PATH", "").strip()
    if not path:
        return Service(), "sm"

    if not os.path.isfile(path):
        logger.warning("CHROMEDRIVER_PATH=%s not found – using Selenium Manager", path)
        return Service(), "sm"

    dmajor = _driver_major(path)
    cmajor = _detect_chrome_major()

    if dmajor and cmajor and dmajor != cmajor:
        logger.warning(
            "chromedriver %s (Chrome %d) does NOT match installed Chrome %d – "
            "falling back to Selenium Manager (auto-installs matching driver)",
            path, dmajor, cmajor)
        return Service(), "sm"

    if dmajor and cmajor:
        logger.info("Using explicit chromedriver (Chrome %d matched)", cmajor)
    else:
        logger.info("Using explicit chromedriver %s (version check skipped)", path)

    return Service(executable_path=path), "explicit"


def _build_driver(profile: DeviceProfile) -> webdriver.Chrome:
    """Return a headless Chrome WebDriver configured for the device profile."""
    options = Options()

    if config.HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")
    options.add_argument(f"--window-size={config.DEVICE_VIEWPORT_WIDTH},{config.DEVICE_VIEWPORT_HEIGHT}")
    options.add_argument(f"--user-agent={profile.user_agent}")
    options.add_argument(f"--lang={config.DEVICE_LANGUAGE}")

    # Mobile emulation – Pixel 10 Pro viewport, with touch support enabled
    # (without it navigator.maxTouchPoints is 0, contradicting a phone UA).
    mobile_emulation = {
        "deviceMetrics": {
            "width": config.DEVICE_VIEWPORT_WIDTH,
            "height": config.DEVICE_VIEWPORT_HEIGHT,
            "pixelRatio": config.DEVICE_PIXEL_RATIO,
        },
        "userAgent": profile.user_agent,
        "touch": True,
    }
    options.add_experimental_option("mobileEmulation", mobile_emulation)

    # Suppress automation flags
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument("--disable-blink-features=AutomationControlled")

    # Optional egress proxy – highly recommended.  Google's risk engine
    # treats datacenter IPs as high-risk; use a residential/mobile proxy.
    proxy_url = os.environ.get("PROXY_URL", "").strip()
    if proxy_url:
        options.add_argument(f"--proxy-server={proxy_url}")
        logger.info("Using egress proxy %s", proxy_url.split("@")[-1])

    # Resolve chromedriver service: explicit path (with version check) or
    # Selenium Manager (auto-downloads matching driver when needed).
    service, source = _resolve_service()
    try:
        driver = webdriver.Chrome(service=service, options=options)
    except WebDriverException as exc:
        if source == "explicit":
            logger.warning(
                "Explicit chromedriver failed (%s) – retrying with "
                "Selenium Manager (auto-installs matching driver)", exc)
            service = Service()
            driver = webdriver.Chrome(service=service, options=options)
        else:
            raise
    driver.implicitly_wait(config.IMPLICIT_WAIT)
    driver.set_page_load_timeout(config.PAGE_LOAD_TIMEOUT)

    # Read the version from the browser that actually started, then synchronize
    # the profile UA and Client Hints before navigating to any website.
    browser_version = str(driver.capabilities.get("browserVersion", "")).strip()
    if not browser_version:
        driver.quit()
        raise WebDriverException(
            "Started Chromium did not report browserVersion; cannot synchronize UA"
        )

    ua_template = config.USER_AGENT_TEMPLATES[0]
    profile.chrome_version = browser_version
    profile.user_agent = ua_template.format(
        android=config.ANDROID_VERSION,
        model=config.DEVICE_MODEL,
        build=config.BUILD_ID,
        chrome=browser_version,
    )
    logger.info("Synchronized User-Agent with running Chromium %s", browser_version)

    _apply_stealth(driver, profile)
    return driver


# ── Login helper ──────────────────────────────────────────────────────────────

# Text markers that indicate Google flagged the sign-in attempt.
LOGIN_CHALLENGE_MARKERS = [
    "this browser or app may not be secure",
    "couldn't sign you in",
    "confirm you're not a robot",
    "unusual traffic",
    "verify it's you",
    "verify your identity",
    "two-step verification",
    "enter the code",
    "try another way",
    "device confirmation",
]


def _login_failure_reason(driver: webdriver.Chrome) -> str:
    """Inspect the current page and return a human-readable failure reason."""
    current_url = driver.current_url
    parsed = urlparse(current_url)
    path = parsed.path or ""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except NoSuchElementException:
        body_text = ""
    for marker in LOGIN_CHALLENGE_MARKERS:
        if marker in body_text:
            return f"Google blocked the login (challenge page: “{marker}”)."
    if "challenge" in path or "/signin/v2/challenge" in current_url:
        return "Google showed a security challenge (2FA / device verification)."
    return "Login failed – please check the credentials."


def _wait_for(driver: webdriver.Chrome, by: str, value: str,
               timeout: int = config.WEBDRIVER_TIMEOUT) -> object:
    """Return element after waiting for it to be clickable."""
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, value))
    )


OTP_INPUT_SELECTORS = (
    "input#totpPin",
    'input[name="totpPin"]',
    'input[name="code"]',
    'input[aria-label*="code" i]',
    'input[type="tel"]',
)


def _find_otp_input(driver: webdriver.Chrome):
    """Return a visible one-time-code input on a challenge page, if any."""
    for selector in OTP_INPUT_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                try:
                    if el.is_displayed():
                        return el
                except Exception:
                    continue
        except Exception:
            continue
    return None


def _submit_otp(driver: webdriver.Chrome, code: str) -> None:
    """Type *code* into the visible OTP input and press Next."""
    el = _find_otp_input(driver)
    if el is None:
        raise NoSuchElementException("OTP input disappeared")
    el.clear()
    _type_human(el, code)
    _human_sleep(0.4, 1.0)
    for selector in ("#totpNext", "#next", 'button[type="submit"]'):
        try:
            btn = driver.find_element(By.CSS_SELECTOR, selector)
            btn.click()
            return
        except NoSuchElementException:
            continue
    el.submit()


def _challenge_active(driver: webdriver.Chrome) -> bool:
    """True when the current page is still a Google challenge page."""
    path = urlparse(driver.current_url).path or ""
    return "challenge" in path or _page_contains_marker(driver)


def _gmail_login(driver: webdriver.Chrome, email: str, password: str,
                 otp_provider=None) -> Optional[str]:
    """
    Perform Gmail / Google account login.

    When Google presents a two-step-verification challenge, *otp_provider* (a
    zero-argument callable returning a 6/8-digit code, or ``None`` on
    timeout) is consulted for the code; without one the run fails fast with
    a descriptive reason.

    Returns ``None`` on apparent success, or a human-readable failure reason
    (challenge page / wrong credentials / timeout).
    """
    try:
        driver.get(config.GMAIL_LOGIN_URL)
        _human_sleep(2.0, 3.5)

        # ── Email step ────────────────────────────────────────────────────────
        email_field = _wait_for(driver, By.CSS_SELECTOR,
                                'input[type="email"]')
        email_field.clear()
        _type_human(email_field, email)

        next_btn = _wait_for(driver, By.ID, "identifierNext")
        _human_sleep(0.4, 1.2)
        next_btn.click()
        _human_sleep(1.5, 3.0)

        # ── Password step ─────────────────────────────────────────────────────
        password_field = _wait_for(driver, By.CSS_SELECTOR,
                                   'input[type="password"]')
        password_field.clear()
        _type_human(password_field, password)

        pw_next = _wait_for(driver, By.ID, "passwordNext")
        _human_sleep(0.4, 1.2)
        pw_next.click()
        _human_sleep(2.5, 4.0)

        # ── Verify login ──────────────────────────────────────────────────────
        current_url = driver.current_url
        parsed = urlparse(current_url)
        hostname = parsed.hostname or ""
        path = parsed.path or ""

        # ── Two-step verification / challenge handling ─────────────────────
        # A challenge with a one-time-code input can be satisfied (TOTP
        # secret or a code typed in by the user via the web UI).  Anything
        # else (device prompts, "couldn't sign you in", …) is still fatal.
        if _challenge_active(driver) or _find_otp_input(driver) is not None:
            if _find_otp_input(driver) is not None and otp_provider is not None:
                passed = False
                for attempt in range(1, 4):
                    logger.info("2FA challenge detected for %s (attempt %d/3) – "
                                "requesting verification code", email, attempt)
                    code = otp_provider()
                    if not code:
                        return ("需要两步验证，但在限定时间内未收到验证码（"
                                "可在账户中配置 TOTP 密钥，或在运行页面手动输入）。")
                    try:
                        _submit_otp(driver, str(code).strip())
                    except NoSuchElementException:
                        break  # input vanished – likely accepted
                    _human_sleep(3.0, 4.5)
                    if not _challenge_active(driver) and _find_otp_input(driver) is None:
                        passed = True
                        break
                    logger.warning("2FA code not accepted for %s (attempt %d/3)",
                                   email, attempt)
                if not passed and _challenge_active(driver):
                    return "两步验证码未被 Google 接受，请检查 TOTP 密钥或验证码。"
                logger.info("Two-step verification passed for %s", email)
                # Recompute URL state after the challenge redirect.
                current_url = driver.current_url
                parsed = urlparse(current_url)
                hostname = parsed.hostname or ""
                path = parsed.path or ""
            else:
                reason = _login_failure_reason(driver)
                logger.warning("Login flagged for %s: %s", email, reason)
                return reason

        # Success: landed on the account hub or any /u/<id>/… account page.
        if hostname == "myaccount.google.com" or (
            hostname.endswith(".google.com") and "/u/" in path
        ):
            logger.info("Login succeeded for %s", email)
            return None

        # Check for inline error messages (wrong password etc.).
        try:
            error_el = driver.find_element(
                By.CSS_SELECTOR, '[jsname="B34EJ"], [aria-live="assertive"]'
            )
            if error_el.text:
                logger.warning("Login error detected: %s", error_el.text)
                return f"Login rejected by Google: {error_el.text.strip()}"
        except NoSuchElementException:
            pass

        # If we're no longer on the login page, assume success.
        if not (hostname == "accounts.google.com" and path.startswith("/signin")):
            logger.info("Login appeared successful for %s (URL: %s)",
                        email, current_url)
            return None

        logger.warning("Unexpected URL after login: %s", current_url)
        return "Login did not complete – Google returned an unexpected page."

    except TimeoutException:
        logger.error("Timeout during login")
        return "Login timed out (Google page did not respond in time)."
    except WebDriverException as exc:
        logger.error("WebDriver error during login: %s", exc)
        return f"Browser automation error during login: {exc}"


def _page_contains_marker(driver: webdriver.Chrome) -> bool:
    """Return True when the current page text contains a challenge marker."""
    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text.lower()
    except NoSuchElementException:
        return False
    return any(marker in body_text for marker in LOGIN_CHALLENGE_MARKERS)


# ── Offer detection ───────────────────────────────────────────────────────────

def _extract_payment_link(driver: webdriver.Chrome) -> Optional[str]:
    """
    Scan the current page for a Gemini Pro offer / activation link.

    Strategy:
    1. Look for anchor tags whose text or aria-label contains offer keywords.
    2. Fall back to scanning all links for 'gemini' or 'upgrade' patterns.
    3. Return the first matching href found.
    """
    keywords = config.GEMINI_OFFER_KEYWORDS

    # -- Strategy 1: anchor text / aria-label match ---------------------------
    all_links = driver.find_elements(By.TAG_NAME, "a")
    for link in all_links:
        try:
            text = (link.text + " " + link.get_attribute("aria-label")).lower()
            href = link.get_attribute("href") or ""
            if any(kw in text for kw in keywords) and href:
                logger.info("Found offer link via text match: %s", href)
                return href
        except Exception:
            continue

    # -- Strategy 2: URL pattern scan -----------------------------------------
    url_patterns = re.compile(
        r"(gemini|upgrade|activate|offer|redeem|trial|checkout)",
        re.IGNORECASE,
    )
    for link in all_links:
        try:
            href = link.get_attribute("href") or ""
            if url_patterns.search(href):
                logger.info("Found offer link via URL pattern: %s", href)
                return href
        except Exception:
            continue

    # -- Strategy 3: button / CTA elements ------------------------------------
    buttons = driver.find_elements(By.CSS_SELECTOR, "button, [role='button']")
    for btn in buttons:
        try:
            text = btn.text.lower()
            if any(kw in text for kw in keywords):
                # Try to find parent anchor
                try:
                    parent_link = btn.find_element(By.XPATH, "ancestor::a")
                    href = parent_link.get_attribute("href") or ""
                    if href:
                        logger.info("Found offer link via button parent: %s", href)
                        return href
                except NoSuchElementException:
                    pass
                # Return current URL as fallback (user will land on offer page)
                logger.info("Found offer CTA button on page: %s", driver.current_url)
                return driver.current_url
        except Exception:
            continue

    return None


def _navigate_google_one(driver: webdriver.Chrome) -> Optional[str]:
    """
    Navigate to Google One and attempt to find the Gemini Pro offer link.

    Returns the payment/activation URL or None if not found.
    """
    for url in (config.GOOGLE_ONE_URL, config.GOOGLE_ONE_OFFERS_URL):
        try:
            logger.info("Navigating to %s", url)
            driver.get(url)
            time.sleep(3)

            # Dismiss cookie/consent banners if present
            for selector in (
                '[aria-label="Accept all"]',
                'button[jsname="higCR"]',
                '[data-action="accept"]',
            ):
                try:
                    btn = driver.find_element(By.CSS_SELECTOR, selector)
                    btn.click()
                    time.sleep(1)
                    break
                except NoSuchElementException:
                    pass

            link = _extract_payment_link(driver)
            if link:
                return link

        except (TimeoutException, WebDriverException) as exc:
            logger.warning("Error accessing %s: %s", url, exc)

    return None


# ── Public API ────────────────────────────────────────────────────────────────

class GoogleAutomationError(Exception):
    """Raised when automation encounters an unrecoverable error."""


def check_gemini_offer(email: str, password: str,
                       device: DeviceProfile,
                       otp_provider=None,
                       driver_callback=None) -> Optional[str]:
    """
    Main entry point.

    Logs into *email* / *password* using the supplied *device* profile,
    navigates to Google One, and returns the Gemini Pro offer link (or None).

    *otp_provider* is a zero-argument callable consulted when Google asks
    for a two-step verification code (return the code, or ``None`` to
    abort).  *driver_callback*, when given, is invoked with the live
    WebDriver so the caller can supervise it (e.g. force-quit on timeout).

    Raises :class:`GoogleAutomationError` if the driver cannot be started or
    the login step fails with an error.
    """
    driver: Optional[webdriver.Chrome] = None
    try:
        logger.info("Starting WebDriver for session %s", device.session_id)
        driver = _build_driver(device)
        if driver_callback is not None:
            try:
                driver_callback(driver)
            except Exception:
                logger.exception("driver_callback failed (continuing)")

        login_error = _gmail_login(driver, email, password,
                                   otp_provider=otp_provider)
        if login_error is not None:
            raise GoogleAutomationError(login_error)

        offer_link = _navigate_google_one(driver)
        return offer_link

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
