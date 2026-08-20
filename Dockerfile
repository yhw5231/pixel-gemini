# ── Pixel Gemini – container image ───────────────────────────────────────────
FROM python:3.12-slim-bookworm AS base

# Chrome for Testing (CfT) version. CfT is Google's official test build of
# Chrome: identical engine, freely redistributable, and the ONLY official
# Chrome line that ships Linux arm64 binaries (the 137–152 stable/dev lines
# are linux64-only). Pinning one version across architectures keeps the image
# reproducible and keeps the simulated UA (see config.py) truthful.
ARG CHROME_VERSION=154.0.8012.0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Chrome runtime libraries on Debian 12 – identical list for both
# architectures (on arm64 apt resolves the arm64 variants automatically).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        unzip \
        fonts-liberation \
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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Install the pinned Chrome for Testing + matching chromedriver for the
# current architecture (linux64 or linux-arm64) into /opt/chrome and
# /usr/local/bin/chromedriver.
RUN ARCH="$(dpkg --print-architecture)" \
    && if [ "$ARCH" = "arm64" ]; then PLATFORM="linux-arm64"; else PLATFORM="linux64"; fi \
    && echo "==> Installing Chrome for Testing ${CHROME_VERSION} (${PLATFORM})" \
    && curl -fsSL -o /tmp/chrome.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${PLATFORM}/chrome-${PLATFORM}.zip" \
    && curl -fsSL -o /tmp/chromedriver.zip \
        "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${PLATFORM}/chromedriver-${PLATFORM}.zip" \
    && unzip -q /tmp/chrome.zip -d /opt \
    && unzip -q /tmp/chromedriver.zip -d /opt \
    && mv "/opt/chrome-${PLATFORM}" /opt/chrome \
    && mv "/opt/chromedriver-${PLATFORM}/chromedriver" /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && ln -sf /opt/chrome/chrome /usr/local/bin/google-chrome \
    && rm -rf "/opt/chromedriver-${PLATFORM}" /tmp/chrome.zip /tmp/chromedriver.zip

# Build-time smoke test: verify headless Chrome actually launches (catches
# missing libraries early) and bake a working offline setup into the image –
# no network and no Selenium Manager downloads needed at runtime.
RUN python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; from selenium.webdriver.chrome.service import Service; o = Options(); o.binary_location = '/opt/chrome/chrome'; o.add_argument('--headless=new'); o.add_argument('--no-sandbox'); o.add_argument('--disable-dev-shm-usage'); d = webdriver.Chrome(service=Service(executable_path='/usr/local/bin/chromedriver'), options=o); d.quit(); print('Chrome ' + '${CHROME_VERSION}' + ' + chromedriver verified')" \
    && /usr/local/bin/chromedriver --version

COPY . .

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data \
    && chmod 777 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["/app/docker/entrypoint.sh"]