# Deploying AI Company Brain on a Hostinger KVM VPS

## ⚠️ The production box, as of 2026-09-01

**This block is the ONE place that names the live host. Correct it here, and
nowhere else.** Verified 2026-09-01 by an `ssh` that returned the hostname and
the serving SHA.

| | |
|---|---|
| Hostname | `srv1914284.hstgr.cloud` |
| IPv4 | `187.127.172.200` |
| Plan | KVM 2, Ubuntu 24.04 LTS |
| Deploy user | `acb` (passwordless `sudo`) |
| App root | `/opt/acb/app` |
| Serves | `metorite.com`, `api.metorite.com`, `app.metorite.com` |

**Reach it with `ssh metorite`.** Put this in your `~/.ssh/config`. The file is
local to one machine, so a fresh checkout must add it again.

    Host metorite metorite-prod
      HostName 187.127.172.200
      User acb
      IdentityFile ~/.ssh/metorite_vps
      IdentitiesOnly yes

⚠️ **`187.127.179.143` and `srv1747539` are a DIFFERENT, OLDER box.** Eleven
files carried that address on 2026-09-01, and four of them were runbooks that
said "ssh here to reach production". They sent a session to the wrong host.
Two files keep the old address on purpose, and both are historical records:
`UPSTREAM-CONNECTIVITY-EVIDENCE.md` (a support ticket) and
`backup_and_restore.md` (a dated measurement). Do not rewrite either.

**Confirm before you trust this table:** `nslookup api.metorite.com` must
return the IPv4 above. DNS is the authority, not this file.

---

Target spec (matches `system_architecture.md` §5 v1 baseline):

| Plan | vCPU | RAM  | Disk        | Price (2-yr renewal) |
|------|------|------|-------------|----------------------|
| KVM 2 | 2    | 8 GB | 100 GB NVMe | ~$14.99/mo           |
| **KVM 4 (recommended)** | **4** | **16 GB** | **200 GB NVMe** | **~$28.99/mo** |

KVM 4 has enough headroom for Postgres + Redis + workbench. KVM 2 works for Phase 0/1 only.

## One-time: provision the server

1. Buy a VPS plan from <https://www.hostinger.com/vps-hosting>.
2. In hPanel → VPS → **Operating system**, pick **Ubuntu 24.04 with Docker** (one-click template).
3. Add your SSH public key under VPS → SSH keys (or set a strong root password).
4. Note the public IP. Optionally point a subdomain (e.g. `app.metorite.com`) A-record to it.
5. Wait for provisioning (~3–5 min), then `ssh root@<ip>`.

## One-time: bootstrap

Once SSH works:

```bash
# On your laptop, push the code first (or git clone on the server below).
ssh root@<ip>

# On the server:
adduser --disabled-password --gecos "" acb
usermod -aG sudo,docker acb
mkdir -p /home/acb/.ssh && cp ~/.ssh/authorized_keys /home/acb/.ssh/ && chown -R acb:acb /home/acb/.ssh
exit

ssh acb@<ip>

# Clone + bootstrap
sudo mkdir -p /opt/acb && sudo chown acb:acb /opt/acb
git clone <your-repo-url> /opt/acb/app
cd /opt/acb/app
bash deploy/hostinger/bootstrap.sh
```

`bootstrap.sh` will:
- Verify Docker is present (Hostinger one-click template ships it).
- Install Caddy (reverse proxy with auto-TLS via Let's Encrypt).
- Copy `.env.example` → `/opt/acb/app/.env` if missing (you'll edit it before the first `up`).
- Install UFW firewall rules (22, 80, 443 only).
- Set up systemd unit so the compose stack starts on reboot.

## Configure secrets

```bash
nano /opt/acb/app/.env
```

Set at minimum:
- `GEMINI_API_KEY` (Tier 1/2/3 — primary LLM provider)
- `GITHUB_TOKEN` (Tier fallback + Claude via Copilot; needs `copilot` scope)
- `POSTGRES_PASSWORD` (any long random string)
- `LITELLM_MASTER_KEY` (any long random string)
- `GATEWAY_SESSION_SECRET` (random)
- ClickUp / Zoho / WhatsApp tokens as each phase needs them.

Then edit `deploy/hostinger/caddy/Caddyfile` and replace the placeholder hostnames with yours.

## Memory system (Mem0 + Graphiti)

Metorite has two memory layers that persist across conversations:

| Layer | Backend | Env var | What it stores |
|---|---|---|---|
| **Mem0** (episodic) | Postgres + pgvector | `MEM0_ENABLED=true` | Per-user facts extracted from conversations |
| **Graphiti** (knowledge graph) | Neo4j | `GRAPHITI_ENABLED=true` | Time-stamped entity relationships and timelines |

Both are enabled by default in the deploy scripts (`--profile core --profile memory`).
Set the env vars in `/opt/acb/app/.env`:

```bash
MEM0_ENABLED=true
GRAPHITI_ENABLED=true
NEO4J_URL=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<generate-a-strong-password>
```

The memory panel appears in the chat sidebar for every agent. The `/memory` page
(Brain icon in nav) provides a full memory manager with semantic search and deletion.

## First deploy

```bash
bash deploy/hostinger/deploy.sh
```

This runs `docker compose --profile core up -d`, waits for healthchecks, and runs `scripts/check_infra.py`.

## Redeploy after a code change

```bash
cd /opt/acb/app
git pull
bash deploy/hostinger/deploy.sh
```

## Updating images

```bash
cd /opt/acb/app
docker compose -f infra/docker-compose.yml pull
bash deploy/hostinger/deploy.sh
```

## Backups

Hostinger takes weekly backups of the whole VPS automatically (included in plan). For Postgres-level point-in-time recovery later, add `pgbackrest` or a `pg_dump` cron job.

## Observability + ops dashboards

Once deployed (and DNS pointed):
- **Control Plane (UI):** `https://metorite.your-domain.tld`
- **Gateway API:** `https://api.metorite.your-domain.tld`

## LLM Routing (Consolidated)

All LLM calls go through the **gateway's own `/v1/chat/completions` endpoint**
on `http://127.0.0.1:8080/v1`.  Provider API keys live in the **encrypted
Postgres `provider_keys` table** (seeded from `.env` on first boot).

There is **no separate LiteLLM proxy process**.  The gateway's `acb_llm` package
loads keys from the encrypted DB at startup and the Python `litellm` SDK routes
directly to providers.  This means:

- One source of truth for all provider keys (the encrypted DB)
- No `litellm.env` with plain-text keys
- One fewer systemd service to maintain
- Integration Registry key changes take effect on gateway restart

### Key storage

| Store | Used by | Encrypted? |
|-------|---------|------------|
| `.env` | Bootstrap/seeding only | ❌ Plain text |
| Postgres `provider_keys` | Gateway `/v1` endpoint | ✅ Fernet (ACB_MASTER_KEY) |
| `acb_llm` in-memory cache | All LLM calls | ❌ Runtime only |

### Architecture

```
┌─────────────────────────────────────┐
│   Postgres provider_keys (encrypted) │  ← SINGLE SOURCE OF TRUTH
└──────────────┬──────────────────────┘
               │ decrypt at startup
               ▼
┌─────────────────────────────────────┐
│   Gateway (:8080)                   │
│   /v1/chat/completions              │  ← Used by EVERYTHING
│   ├── orchestrator MAF agent        │
│   ├── specialist agents (BYOK)      │
│   ├── mutation sandbox (Docker)     │
│   └── settings page model test      │
└─────────────────────────────────────┘
```

### Tier aliases (must stay in sync)

Two files define tier model mappings.  They **must agree** or chat breaks:

| Config location | Purpose | Example |
|----------------|---------|---------|
| `packages/acb_llm/acb_llm/client.py` → `_TIER_MODEL` | Maps tier ID → litellm model string | `"tier2": "deepseek/deepseek-chat"` |
| `apps/gateway/gateway/routes/v1_compat.py` → `_TIER_NAME_TO_ID` | Maps tier alias → tier ID | `"tier-balanced": "tier2"` |

The orchestrator passes tier alias names (`tier-fast`, `tier-balanced`,
`tier-powerful`) to the gateway.  If `_TIER_NAME_TO_ID` is missing an
alias, the gateway passes the raw alias to litellm, which rejects it with
`BadRequestError: LLM Provider NOT provided`.

**Adding a new tier:**
1. Add the model string to `_TIER_MODEL` in `client.py`
2. Add the alias mapping to `_TIER_NAME_TO_ID` in `v1_compat.py`
3. Optionally add it to `infra/litellm/config.yaml` for the settings UI

### Pydantic model constraints

After removing the proxy, two Pydantic models **must use empty strings, not
`None`** for fields that previously held proxy URLs:

| Model | Field | Value |
|-------|-------|-------|
| `LLMConfig` | `litellm_ui_url` | `""` (empty string, NOT `None`) |
| `LiteLLMHealth` | `ui_url` | `""` (empty string, NOT `None`) |

Setting these to `None` causes a 500 error on the models page
(`ValidationError: Input should be a valid string`).

### Deployment checklist

```bash
# 2. Verify the gateway's /v1 endpoint (SDK, no separate proxy)
grep LITELLM_BASE_URL /opt/acb/app/.env
# Must be: LITELLM_BASE_URL=http://127.0.0.1:8080

# 3. Test the gateway's /v1 endpoint
curl -s -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"tier-balanced","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'

# 4. Test the models page endpoint
curl -s http://127.0.0.1:8080/settings/llm | python3 -m json.tool | head -5

# 5. Test orchestrator chat end-to-end
curl -s -N -X POST http://127.0.0.1:8080/copilot/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-local" \
  -d '{"messages":[{"role":"user","content":"Hi"}],"stream":true}' | head -5
```

### Verification

```bash
# Test the gateway's /v1 endpoint directly
curl -s -X POST http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"tier-balanced","messages":[{"role":"user","content":"Hello"}],"max_tokens":20}'
```

## CI/CD via GitHub Actions

Push-to-deploy is configured via `.github/workflows/deploy.yml`. On every push to
`main`, the pipeline:

1. **Lint** — Ruff lint + mypy type check
2. **Test** — Full unit test suite (`pytest tests/unit/`)
3. **Deploy** — SSH into Hostinger VPS, `git pull`, restart Docker Compose, sync Python deps, reload Caddy, run `check_infra.py`
4. **Smoke** — Hit `https://metorite.your-domain.tld/health` from CI

PRs get a lighter `pr-check.yml` workflow (lint + test only, no deploy).

### One-time: set up CI/CD secrets

In your GitHub repo → Settings → Secrets and variables → Actions, add:

| Secret | Value |
|--------|-------|
| `HOSTINGER_HOST` | VPS IP address (e.g. `123.45.67.89`) |
| `HOSTINGER_USER` | SSH user (`acb`) |
| `HOSTINGER_SSH_KEY` | SSH private key (ed25519 preferred) |
| `HOSTINGER_SSH_PORT` | SSH port (usually `22`) |

Generate an SSH key for CI/CD (do NOT reuse your personal key):

```bash
ssh-keygen -t ed25519 -C "ci-deploy@metorite" -f ~/.ssh/ci_deploy_key
cat ~/.ssh/ci_deploy_key.pub >> ~/.ssh/authorized_keys   # on the VPS
cat ~/.ssh/ci_deploy_key  # → paste into HOSTINGER_SSH_KEY secret
```

### Manual deploy (fallback)

If CI/CD is unavailable, SSH into the VPS and run:

```bash
cd /opt/acb/app && git pull && bash deploy/hostinger/deploy.sh
```

## Cost ceiling per `project_plan.md` §8

- VPS (KVM 4): ~$29/mo
- Vexa compute (when Phase 2 lands): ~€0.05–0.15/meeting
- LLM API (Gemini + GitHub Copilot): cost metered via LiteLLM; tuned per ADR-008 caching strategy

Stay well under the per-month budget by keeping Tier 1 on a cheap model (Haiku, then Qwen3 once a GPU host is added) and enforcing prompt caching in `litellm/config.yaml`.