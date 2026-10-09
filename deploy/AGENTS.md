# Deployment

## Purpose
Hostinger VPS deployment scripts, Caddy reverse proxy config, and CI/CD pipeline.

## Key Files
- hostinger/bootstrap.sh -- initial VPS setup (installs uv, validates tokens, writes systemd units)
- hostinger/deploy.sh -- application deployment (Docker, restart services)
- hostinger/README.md -- setup instructions
- hostinger/acb.service -- systemd unit: the docker compose stack, `--profile core` only (WS-49 BH-8)
- hostinger/acb-gateway.service -- systemd unit: FastAPI gateway on :8080
- hostinger/acb-workbench.service -- systemd unit: Next.js workbench on :3001
- caddy/ -- Caddy reverse proxy configuration
- secrets/manifest.json -- the secrets-drop manifest that `scripts/secrets.sh` reads. It holds names, paths and modes, and never a value. Git tracks only this file in `secrets/`. Reference: `docs/secrets_drop.md`
- ../.github/workflows/deploy.yml -- CI/CD: push-to-deploy (lint → test → SSH → deploy → smoke)
- ../.github/workflows/pr-check.yml -- PR validation (lint + test only)

## Conventions
- Bootstrap installs uv, validates GITHUB_TOKEN, and installs `hostinger/acb.service` from the repo. It writes no unit from a heredoc
- Deploy pulls latest, rebuilds Docker images, syncs Python deps, restarts gateway + workbench systemd services
- After its pull, `scripts/vps_apply.sh` runs the pulled commit's own copy of itself one time. It does this when the running copy differs or came from stdin. Every step AFTER the pull runs from the target's own copy. The pull block itself (ownership repair, agents.json backup, fetch, reset) runs from the copy that started. A change to the pull block takes effect on the next apply. It records a sha as applied only when that sha's own copy ran the steps. A step after the pull gets only the env that the `exec` passes. Put a value that the pull step sets into that `exec`. Fence: `tests/unit/test_deploy_reexec.py`
- `scripts/vps_apply.sh` installs every `hostinger/*.service` and `*.timer` file when it changes (BO-23). A unit that is not a repo file is drift
- `scripts/vps_apply.sh` installs every `hostinger/<unit>.service.d/*.conf` drop-in before the first service restart, then runs `daemon-reload` (WS-49 BH-7). It deletes nothing. It never writes a `90-*` name, because `90-*` is a rollback on the box (`scripts/bh2_rollback.sh`). A unit with a changed drop-in gets one restart. Fences: `tests/unit/test_unit_hardening.py` and `tests/unit/test_agent_deps_target.py`
- `hostinger/acb-gateway.service.d/40-agent-site.conf` gives the gateway `/var/lib/acb-gateway` and `/var/cache/acb-gateway`. Agent installs go there, never to the shared venv. Do not set `PYTHONPATH` in any unit
- The T2 vendor cache is the constant `/opt/acb/t2-vendor`. The deploy installs it with `--ignore-scripts` and reads no path from `.env`. It removes a `CUSTOM_APPS_T2_VENDOR_DIR` line from `.env`, with a warning
- Docker Compose boots with `--profile core` only (Postgres, Redis). The memory profile (Neo4j) is OFF. WS-49 BH-8 took it out after Neo4j answered on the public internet
- Every published port in `infra/docker-compose.yml` binds to the literal `127.0.0.1`. Docker port rules go around ufw. Fence: `tests/unit/test_compose_ports_local.py`
- Every published port in every tracked compose file binds to the literal `127.0.0.1`. That includes `apps/services/meeting_bot/docker-compose.yml`, which also needs `MEETING_BOT_TOKEN` to start
- Neo4j has no default password in compose. Its guard reads only the env. An existing data volume keeps its stored password, so read `hostinger/README.md` (Memory system) before you turn Neo4j on again. `NEO4J_PASSWORD` goes to the box through `scripts/secrets.sh`
- Neo4j is required for Graphiti bi-temporal knowledge graph (GRAPHITI_ENABLED=true)
- `hostinger/acb-backup.service` runs as root and loads the acb-writable `/opt/acb/app/.env`. So `scripts/backup_db.sh` trusts no value from it (WS-49 BH-6a). The header of that script gives the class of each name. Off-box names come from `/etc/acb/backup-offbox.env` only. An `Environment=` line cannot pin a name, because an `EnvironmentFile=` value wins over it. Fence: `tests/unit/test_backup_env_values.py`
- **LLM routing: gateway /v1/chat/completions reads keys from encrypted Postgres — no separate proxy**
- Provider keys live in the encrypted `provider_keys` table; seeded from `.env` on first boot
- Next.js workbench is rebuilt (`npm ci && npm run build`) and restarted on every deploy
- Caddy handles SSL termination and routing
- CI/CD deploys on push to main; PRs run lint + test only
- SSH key for CI/CD must be a dedicated key (not a developer personal key)
