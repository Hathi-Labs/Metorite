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
- `scripts/vps_apply.sh` installs every `hostinger/*.service` and `*.timer` file when it changes (BO-23). A unit that is not a repo file is drift
- Docker Compose boots with `--profile core` only (Postgres, Redis). The memory profile (Neo4j) is OFF. WS-49 BH-8 took it out after Neo4j answered on the public internet
- Every published port in `infra/docker-compose.yml` binds to the literal `127.0.0.1`. Docker port rules go around ufw. Fence: `tests/unit/test_compose_ports_local.py`
- Neo4j has no default password. It refuses to start without a real `NEO4J_PASSWORD`, which goes to the box through `scripts/secrets.sh`
- Neo4j is required for Graphiti bi-temporal knowledge graph (GRAPHITI_ENABLED=true)
- **LLM routing: gateway /v1/chat/completions reads keys from encrypted Postgres — no separate proxy**
- Provider keys live in the encrypted `provider_keys` table; seeded from `.env` on first boot
- Next.js workbench is rebuilt (`npm ci && npm run build`) and restarted on every deploy
- Caddy handles SSL termination and routing
- CI/CD deploys on push to main; PRs run lint + test only
- SSH key for CI/CD must be a dedicated key (not a developer personal key)
