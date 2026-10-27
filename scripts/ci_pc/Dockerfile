# The image each local CI job runs in (scripts/ci_pc/run_suite.sh).
#
# WHY A CONTAINER, when the distro is already preset: every WSL2 distro on a
# PC shares ONE network. Docker Desktop's dev stack publishes Postgres on
# *:5432 and Redis on *:6379 there, and a test that falls back to
# localhost:5432 reached the owner's real dev database (measured 2026-10-19).
# A container has its own network, so localhost is private, as on GitHub.
#
# It holds only the system layer. The repo, .venv, uv's Python, node_modules
# and the browser come from the distro's /home/ci, mounted at the same path,
# so nothing is reinstalled per run.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq \
 && apt-get install -y -qq ca-certificates curl gnupg git bash lsb-release unzip >/dev/null \
 && install -d /usr/share/keyrings /etc/apt/keyrings \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
 && curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null \
 && apt-get install -y -qq postgresql-client-16 nodejs docker-ce-cli >/dev/null \
 && npx -y playwright@1 install-deps chromium >/dev/null \
 && rm -rf /var/lib/apt/lists/*
# The job must run as the uid and gid that own the mounted /home/ci. They are
# NOT a constant: Ubuntu's image already holds `ubuntu` at 1000, so the
# distro's `ci` came out as 1001:1002 (measured 2026-10-19), and a guessed
# 1000 could not read a single file. run_suite.sh passes `id -u` and `id -g`.
ARG CI_UID
ARG CI_GID
RUN test -n "$CI_UID" && test -n "$CI_GID"
RUN if id -u ubuntu >/dev/null 2>&1; then userdel -r ubuntu; fi
RUN groupadd -g "$CI_GID" ci && useradd -m -u "$CI_UID" -g "$CI_GID" -s /bin/bash ci
USER ci
ENV HOME=/home/ci PATH=/home/ci/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    UV_NO_SYNC=1 CI=true
WORKDIR /home/ci/metorite
