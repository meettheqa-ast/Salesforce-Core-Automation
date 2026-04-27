# syntax=docker/dockerfile:1.7
# Backend image for AI QA Portal — FastAPI + Robot Framework + RF-MCP + headless Chromium.
# Designed to run on Fly.io Machines (Linux/amd64) with a persistent volume mounted at /data.

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# --- System deps -----------------------------------------------------------
# chromium + chromium-driver give us a headless browser Robot/Selenium can drive.
# The libnss / libxss / fonts packages cover the typical missing-shared-lib errors.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      git \
      tini \
      chromium \
      chromium-driver \
      fonts-liberation \
      libnss3 \
      libxss1 \
      libgbm1 \
      libatk1.0-0 \
      libatk-bridge2.0-0 \
      libcups2 \
      libdrm2 \
      libxcomposite1 \
      libxdamage1 \
      libxrandr2 \
      libxkbcommon0 \
      libpangocairo-1.0-0 \
      libasound2 \
      libtk8.6 \
      libtcl8.6 \
   && rm -rf /var/lib/apt/lists/*

# BACKEND_IN_CONTAINER lets runs.py force --headless=new + --no-sandbox even
# when the caller asks for headed mode (no display inside the container).
# CONTAINER_BROWSER_BINARY is read by Resources/Common/GlobalKeywords.robot to
# point Selenium at the system chromium binary (the default search misses it).
# SE_OFFLINE keeps Selenium Manager from trying to download chromedriver from
# googlechromelabs.github.io -- we already have /usr/bin/chromedriver.
ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    BACKEND_IN_CONTAINER=1 \
    CONTAINER_BROWSER_BINARY=/usr/bin/chromium \
    SE_OFFLINE=true \
    WDM_LOCAL=1

# --- Python deps -----------------------------------------------------------
WORKDIR /app

COPY requirements.txt /app/_pip/requirements.txt
COPY ai_qa_portal/requirements.txt /app/_pip/ai_qa_portal/requirements.txt
RUN python -m pip install --upgrade pip \
 && pip install -r /app/_pip/requirements.txt -r /app/_pip/ai_qa_portal/requirements.txt

# --- App source ------------------------------------------------------------
COPY . /app

# Pre-create writable mount points so the container also works without a volume.
RUN mkdir -p /data/Results /data/Saved_Projects /data/ai_qa_portal_data /data/ai_qa_portal_outputs

# --- Runtime env -----------------------------------------------------------
ENV RFMCP_HOST=127.0.0.1 \
    RFMCP_PORT=8765 \
    DATA_DIR=/data/ai_qa_portal_data \
    OUTPUT_DIR=/data/ai_qa_portal_outputs \
    RESULTS_DIR=/data/Results \
    SAVED_PROJECTS_DIR=/data/Saved_Projects

EXPOSE 8000

# tini reaps zombie subprocesses (Robot + chromedriver spawn many).
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "ai_qa_portal.backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
