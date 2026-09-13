FROM python:3.12-slim-bookworm

# Security patches on top of the base image, no extra packages — reportlab/Pillow/
# pyuwsgi all ship manylinux wheels for cpython 3.12, so no compiler toolchain is needed
# in the image (smaller image, less to patch, no CVE surface from gcc/build-essential).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    BASE_URL_PATH= \
    PORT=8080

RUN groupadd --system appuser && useradd --system --gid appuser --home-dir /app --no-create-home appuser

WORKDIR /app

COPY attendance_app/requirements.txt attendance_app/requirements.txt
RUN pip install --no-cache-dir --only-binary=:all: -r attendance_app/requirements.txt

COPY pass_schedule.py .
COPY attendance_app/ attendance_app/

RUN mkdir -p /data && chown -R appuser:appuser /data /app

USER appuser
WORKDIR /app/attendance_app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + os.environ.get('BASE_URL_PATH','') + '/healthz', timeout=2)" || exit 1

CMD ["pyuwsgi", "--ini", "uwsgi.ini"]
