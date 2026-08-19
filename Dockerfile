# ── Pixel Gemini – container image ───────────────────────────────────────────
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Browser strategy (multi-arch):
#  - amd64: Google Chrome Stable .deb from dl.google.com – the binary version
#    must be in the same era as the simulated UA (Chrome 137.x), otherwise the
#    `sec-ch-ua` / Client-Hints headers advertise a version that contradicts
#    the User-Agent – an easy cross-checkable red flag.
#  - arm64: Google Chrome ships NO official arm64 Linux build, so we use
#    Debian's Chromium + chromium-driver instead (apt resolves all runtime
#    dependencies automatically).
# Both branches end with a working headless Chrome-compatible browser; the
# matching driver is verified at build time (smoke test) below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && ARCH="$(dpkg --print-architecture)" \
    && if [ "$ARCH" = "amd64" ]; then \
        apt-get install -y --no-install-recommends \
            fonts-liberation \
            gnupg \
            libasound2 \
            libatk-bridge2.0-0 \
            libatk1.0-0 \
            libatspi2.0-0 \
            libcairo2 \
            libcups2 \
            libdbus-1-3 \
            libdrm2 \
            libgbm1 \
            libglib2.0-0 \
            libgtk-3-0 \
            libnspr4 \
            libnss3 \
            libpango-1.0-0 \
            libu2f-udev \
            libvulkan1 \
            libx11-6 \
            libxcb1 \
            libxcomposite1 \
            libxdamage1 \
            libxext6 \
            libxfixes3 \
            libxkbcommon0 \
            libxrandr2 \
            xdg-utils \
        && curl -fsSL -o /tmp/google-chrome.deb \
            https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
        && apt-get install -y /tmp/google-chrome.deb \
        && rm -f /tmp/google-chrome.deb; \
    else \
        apt-get install -y --no-install-recommends \
            chromium \
            chromium-driver; \
    fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache the chromedriver matching the installed browser and verify
# headless mode actually launches – this doubles as a build-time smoke test
# (catches missing libraries early) and makes the container work offline at
# runtime. The matching driver is then baked into the image at
# /usr/local/bin/chromedriver, so the container needs NO manual chromedriver
# installation and no network at runtime.
#   amd64: Selenium Manager downloads the driver into ~/.cache/selenium.
#   arm64: the chromium-driver package installs /usr/bin/chromedriver.
RUN ARCH="$(dpkg --print-architecture)" \
    && if [ "$ARCH" = "amd64" ]; then \
        python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; o = Options(); o.add_argument('--headless=new'); o.add_argument('--no-sandbox'); o.add_argument('--disable-dev-shm-usage'); d = webdriver.Chrome(options=o); d.quit(); print('Chrome + chromedriver verified')"; \
    else \
        python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; from selenium.webdriver.chrome.service import Service; o = Options(); o.binary_location = '/usr/bin/chromium'; o.add_argument('--headless=new'); o.add_argument('--no-sandbox'); o.add_argument('--disable-dev-shm-usage'); d = webdriver.Chrome(service=Service(executable_path='/usr/bin/chromedriver'), options=o); d.quit(); print('Chromium + chromedriver verified')"; \
    fi \
    && DRIVER="$(find /root/.cache/selenium /usr/bin -type f -name chromedriver 2>/dev/null | head -n 1)" \
    && cp "$DRIVER" /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && /usr/local/bin/chromedriver --version

COPY . .

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data \
    && chmod 777 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["/app/docker/entrypoint.sh"]