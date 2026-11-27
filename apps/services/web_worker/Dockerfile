# Metorite web worker (WEB-6, project-docs/specs/web_research.md section 5.1).
# Playwright Chromium renders a page, and Crawl4AI writes its Markdown.
#
# The build context is the REPO ROOT, because the image installs acb_common
# for the one address rule (acb_common.public_address). Never copy or vendor
# that module. Build it from the root of a checkout:
#
#   docker build -f apps/services/web_worker/Dockerfile -t acb-web-worker:latest .
#
# The tag of the base image matches playwright in requirements.txt, so the
# browsers in /ms-playwright fit the Python package.
FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

# Crawl4AI imports a fork of litellm. LITELLM_LOCAL_MODEL_COST_MAP makes
# litellm read its model price map from the package, with no fetch at import.
# A uid with no home gets HOME=/, so HOME is /tmp, which uid 1000 can write.
# RES_OPTIONS bounds each DNS query of glibc, so a check thread that waits on
# a DNS server that never answers ends in about 2 s (fetch.AddressChecker).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    HOME=/tmp \
    RES_OPTIONS="timeout:2 attempts:1" \
    CRAWL4_AI_BASE_DIRECTORY=/tmp \
    WEB_WORKER_JOB_ROOT=/tmp/web-worker-jobs \
    PATH=/opt/venv/bin:$PATH

# The full closure, pinned with hashes. acb_common installs with --no-deps,
# because the lock holds its dependencies already. The base image leaves the
# browser tree writable by every uid, so the chmod makes uid 1000 unable to
# change a browser binary.
COPY apps/services/web_worker/requirements.lock.txt /tmp/build/requirements.lock.txt
COPY packages/acb_common /tmp/build/acb_common
RUN python3 -m venv --without-pip /opt/venv \
    && /usr/local/bin/pip --python /opt/venv/bin/python install --require-hashes --no-deps -r /tmp/build/requirements.lock.txt \
    && /usr/local/bin/pip --python /opt/venv/bin/python install --no-deps /tmp/build/acb_common \
    && rm -rf /tmp/build \
    && chmod -R go-w /ms-playwright

WORKDIR /app
COPY --chmod=0444 apps/services/web_worker/web_worker ./web_worker
RUN chmod 0555 /app/web_worker

EXPOSE 8080

# Not root. uid 1000 owns nothing in /app or /opt/venv.
USER 1000:1000

CMD ["uvicorn", "--factory", "web_worker.main:create_app", "--host", "0.0.0.0", "--port", "8080", "--no-server-header"]
