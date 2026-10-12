# syntax=docker/dockerfile:1
# targety: test (pytest), runtime (domyślny)

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend
COPY data ./data

# baza budowana w obrazie, przy starcie kontenera nic się już nie importuje
RUN useradd --system --uid 10001 --no-create-home --home-dir /app polids \
 && python -m backend.app.importers.seed \
 && mkdir -p /app/data/state \
 && chown -R polids:polids /app/data


FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
USER polids
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "backend/tests"]


FROM base AS runtime
ARG REVISION=dev
LABEL org.opencontainers.image.revision="${REVISION}"
USER polids
EXPOSE 1337
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:1337/healthz', timeout=4)"]
# jeden worker - cache zapytań do VATSIM/vIFF/NOTAM jest w pamięci procesu
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "1337", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
