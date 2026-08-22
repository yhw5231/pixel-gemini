# ── Pixel Gemini – container image ───────────────────────────────────────────
FROM python:3.12-slim-bookworm AS base

# Browser installation is architecture-aware. Google Chrome's Linux package is
# amd64-only, so arm64 uses Debian's native Chromium and matching chromedriver.
# Keep the Chrome version argument for reproducible amd64 builds.
ARG CHROME_VERSION=154.0.8012.0

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Install common browser dependencies. On arm64, use Debian's native Chromium
# and matching driver because Google's Linux Chrome/CfT downloads are amd64-only.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
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
        xdg-utils; \
    if [ "$(dpkg --print-architecture)" = "arm64" ]; then \
        apt-get install -y --no-install-recommends chromium chromium-driver; \
        ln -sf /usr/bin/chromium /usr/local/bin/google-chrome; \
        ln -sf /usr/bin/chromedriver /usr/local/bin/chromedriver; \
    fi; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# On amd64, install the pinned Chrome for Testing and matching chromedriver.
# On arm64, the Debian Chromium packages installed above are used instead.
RUN set -eux; \
    if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        PLATFORM="linux64"; \
        echo "==> Installing Chrome for Testing ${CHROME_VERSION} (${PLATFORM})"; \
        curl -fsSL -o /tmp/chrome.zip \
            "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${PLATFORM}/chrome-${PLATFORM}.zip"; \
        curl -fsSL -o /tmp/chromedriver.zip \
            "https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/${PLATFORM}/chromedriver-${PLATFORM}.zip"; \
        unzip -q /tmp/chrome.zip -d /opt; \
        unzip -q /tmp/chromedriver.zip -d /opt; \
        mv "/opt/chrome-${PLATFORM}" /opt/chrome; \
        mv "/opt/chromedriver-${PLATFORM}/chromedriver" /usr/local/bin/chromedriver; \
        chmod +x /usr/local/bin/chromedriver; \
        ln -sf /opt/chrome/chrome /usr/local/bin/google-chrome; \
        rm -rf "/opt/chromedriver-${PLATFORM}" /tmp/chrome.zip /tmp/chromedriver.zip; \
    fi

# Build-time smoke test: verify that the architecture-specific browser and
# matching driver launch successfully without runtime downloads.
RUN python -c "from selenium import webdriver; from selenium.webdriver.chrome.options import Options; from selenium.webdriver.chrome.service import Service; o = Options(); o.binary_location = '/usr/local/bin/google-chrome'; o.add_argument('--headless=new'); o.add_argument('--no-sandbox'); o.add_argument('--disable-dev-shm-usage'); d = webdriver.Chrome(service=Service(executable_path='/usr/local/bin/chromedriver'), options=o); d.quit(); print('Browser and chromedriver verified')" \
    && /usr/local/bin/chromedriver --version

COPY . .

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data \
    && chmod 777 /data

EXPOSE 8910

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8910/healthz || exit 1

CMD ["/app/docker/entrypoint.sh"]