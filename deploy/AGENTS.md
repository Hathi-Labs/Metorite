# Deployment

## Purpose
Hostinger VPS deployment scripts, Caddy reverse proxy config, and CI/CD pipeline.

## Key Files
- hostinger/bootstrap.sh -- initial VPS setup (installs uv, validates tokens, writes systemd units)
- hostinger/deploy.sh -- application deployment (Docker, restart services)
- hostinger/README.md -- setup instructions
- hostinger/acb-gateway.service -- systemd unit: FastAPI gateway on :8080
- hostinger/acb-workbench.service -- systemd unit: Next.js workbench on :3001
- caddy/ -- Caddy reverse proxy configuration
- secrets/manifest.json -- the secrets-drop manifest that `scripts/secrets.sh` reads. It holds names, paths and modes, and never a value. Git tracks only this file in `secrets/`. Reference: `docs/secrets_drop.md`
- ../.github/workflows/deploy.yml -- CI/CD: push-to-deploy (lint → test → SSH → deploy → smoke)
- ../.github/workflows/pr-check.yml -- PR validation (lint + test only)

## Conventions
- Bootstrap installs uv, validates GITHUB_TOKEN, writes acb/acb-gateway/acb-workbench systemd units
- Deploy pulls latest, rebuilds Docker images, syncs Python deps, restarts gateway + workbench systemd services
- Docker Compose boots with `--profile core --profile memory` (Postgres, Redis, Neo4j)
- Neo4j is required for Graphiti bi-temporal knowledge graph (GRAPHITI_ENABLED=true)
- **LLM routing: gateway /v1/chat/completions reads keys from encrypted Postgres — no separate proxy**
- Provider keys live in the encrypted `provider_keys` table; seeded from `.env` on first boot
- Next.js workbench is rebuilt (`npm ci && npm run build`) and restarted on every deploy
- Caddy handles SSL termination and routing
- CI/CD deploys on push to main; PRs run lint + test only
- SSH key for CI/CD must be a dedicated key (not a developer personal key)
