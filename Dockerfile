# ── Pixel Gemini – container image ───────────────────────────────────────────
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data

# Google Chrome Stable (not Debian's old Chromium): the binary version must be
# in the same era as the simulated UA (Chrome 137.x), otherwise the
# `sec-ch-ua` / Client-Hints headers advertise a version that contradicts the
# User-Agent – an easy cross-checkable red flag.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
    && curl -fsSL -o /tmp/google-chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/google-chrome.deb \
    && rm -f /tmp/google-chrome.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Pre-cache the chromedriver matching the installed Chrome via Selenium
# Manager and verify headless Chrome actually launches – this doubles as a
# build-time smoke test (catches missing libraries early) and makes the
# container work offline at runtime.
RUN python -c "
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
o = Options()
o.add_argument('--headless=new')
o.add_argument('--no-sandbox')
o.add_argument('--disable-dev-shm-usage')
d = webdriver.Chrome(options=o)
d.quit()
print('Chrome + chromedriver verified')
"

COPY . .

RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /data \
    && chmod 777 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

CMD ["/app/docker/entrypoint.sh"]
