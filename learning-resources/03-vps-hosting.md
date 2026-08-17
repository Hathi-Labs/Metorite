# 03 · Hosting on a VPS

The entire Metorite production stack — backend, frontend, database, cache, and an optional graph
DB — runs on **one cheap Linux box** (a Hostinger KVM VPS, Ubuntu 24.04). No Kubernetes, no managed
cloud services. This chapter explains that topology and *why it's a good default* for a small team.

---

## 1. The mental model: three layers on one machine

```
┌──────────────────────────────────────────────────────────────────────┐
│  VPS (Ubuntu 24.04, Docker installed)                                  │
│                                                                        │
│  Layer 3 — EDGE:  Caddy          :80 / :443   (TLS + reverse proxy)    │
│                       │                                                 │
│         ┌─────────────┴──────────────┐                                 │
│         ▼                            ▼                                  │
│  Layer 2 — APPS (systemd):                                             │
│    acb-gateway.service    :8080   (uvicorn / FastAPI)                  │
│    acb-workbench.service  :3001   (npm start / Next.js)               │
│         │                                                              │
│         ▼                                                              │
│  Layer 1 — STATEFUL INFRA (Docker Compose):                           │
│    postgres :5432   ·   redis :6379   ·   (neo4j :7687 optional)      │
└──────────────────────────────────────────────────────────────────────┘
```

Three layers, three different tools, each chosen for what it's good at:

| Layer | Tool | Why this tool |
|---|---|---|
| **Edge** | **Caddy** | Automatic HTTPS (fetches + renews Let's Encrypt certs with zero config), simple reverse-proxy syntax, HTTP→HTTPS redirect for free. |
| **Apps** | **systemd** | The apps are long-lived processes that must restart on crash and start on boot. That's exactly what an init system does. `Restart=always` is the whole HA story. |
| **Infra** | **Docker Compose** | Postgres/Redis/Neo4j are *stateful* and version-sensitive. Containers pin exact versions and keep data in named volumes, so the DB is reproducible and isolated from the host. |

**The key insight:** stateless app code and stateful infra want *different* deployment models. Put your
database in a container with a volume (reproducible, pinned). Run your app code as a native systemd
service (fast restarts, direct filesystem/venv access, no image rebuild to deploy). Mixing them —
e.g. running Postgres as a bare process, or your app inside a container you rebuild every deploy — is
where a lot of small-team pain comes from.

---

## 2. Why *not* containerize the apps too?

A reasonable question. Metorite deliberately runs the gateway and workbench as native systemd
services rather than Docker containers. The trade-off:

- **Deploying** becomes `git pull && uv sync && systemctl restart` — seconds, no image build/push/pull.
- The app has direct access to the **agent clone cache** (`~/.acb/agents/`) and the Python venv.
- On a 4 GB box, you avoid the memory overhead of extra container layers during the Next.js rebuild.

The cost: you don't get container isolation for the apps, and "works on my machine" drift is possible.
For a two-person team on a single box, the operational simplicity wins. At larger scale you'd flip this
(containers + an orchestrator) — see the "v3" column in the architecture doc's hosting table.

---

## 3. Domains and the edge

Two DNS A-records point at the VPS IP, and Caddy routes by hostname:

```
app.metorite.com       → localhost:3001   (the Next.js workbench)
api.metorite.com   → localhost:8080   (the FastAPI gateway)
```

A minimal Caddyfile for this is remarkably small — Caddy handles the certificates itself:

```
app.metorite.com {
    reverse_proxy localhost:3001
}
api.metorite.com {
    reverse_proxy localhost:8080
}
```

The apps bind to `localhost` only; **nothing but Caddy is exposed to the internet**. A UFW firewall
allows just SSH (22), HTTP (80), and HTTPS (443). Postgres, Redis, and the app ports are reachable only
from the box itself (and, for the DB/cache, only over the Docker network).

---

## 4. Stateful infra with Docker Compose

`infra/docker-compose.yml` defines the data layer, organized with **profiles** so you only run what you
need:

- **`core` profile** (always on): `postgres` (the `pgvector/pgvector:pg16` image — Postgres with the
  vector extension baked in) and `redis` (`redis:7-alpine`). Both have healthchecks (`pg_isready`,
  `redis-cli ping`) and named volumes (`acb-postgres-data`, `acb-redis-data`) so data survives container
  recreation.
- **`memory` profile** (optional): `neo4j` for the bi-temporal knowledge graph. Disabled on small boxes
  — it wants ~500 MB RAM (the deploy script actively forces `GRAPHITI_ENABLED=false` to protect a 4 GB
  VPS).
- **`sandbox` profile**: builds the self-mutation runner image (chapter 12).

**Postgres initialization is just SQL files.** There's no migration *tool* — Postgres's Docker image
runs every `.sql` file mounted into `/docker-entrypoint-initdb.d/` in alphabetical order on first boot.
Metorite has 40+ numbered migrations (`01_schema.sql` … `40_email_assistant_fallback_model.sql`),
applied on the running box by `scripts/apply_migrations.sh` (which globs `*.sql | sort`). Numbering is
the entire ordering strategy — see chapter 04 for how new migrations get applied on deploy.

---

## 5. State that must not be lost

Two subtle but important operational details, both learned the hard way and worth stealing:

1. **The agent clone cache lives in `~/.acb/agents/`, not `/tmp`.** Agents are cloned once and pulled on
   each run (chapter 06). If that cache were in `/tmp`, a reboot (which systemd-tmpfiles clears) would
   wipe every clone and every in-progress artifact. Durable working state belongs in a durable path.

2. **A tracked file that the app mutates at runtime must be preserved across `git reset`.** The deploy
   does `git reset --hard origin/main`, which would clobber `apps/gateway/agents.json` (the runtime agent
   registry the UI writes to). The deploy script backs it up before the reset and restores it after. The
   general lesson: **if your deploy hard-resets the working tree, inventory anything the running app
   writes into tracked paths and protect it explicitly.**

---

## 6. The one-database principle

All durable state is in the single Postgres instance: the entity graph, chat history, the encrypted
credential store, the approval/mutation queues, email data, and vector embeddings (via pgvector). One
database means **one thing to back up** and one connection story. For a small team this is a deliberate
and correct simplification (ADR-002) — you do not need a separate vector DB, a separate graph DB, and a
separate relational DB to ship. Reach for those only when a specific scale problem forces it.

Redis is the other stateful piece, but it's *ephemeral by design*: the event bus (Redis Streams), the
streaming-reconnection buffer (`cc:stream:{thread_id}`, 1-hour TTL), and chat-history caching. Losing
Redis loses in-flight events, not durable facts.

---

## 7. Sizing

Metorite targets a **Hostinger KVM 4** (4 vCPU / 16 GB / NVMe, ~$29/mo) and runs on as little as a
KVM 2 (2 vCPU / 8 GB) with the memory profile off. The binding constraint is RAM during the Next.js
production build (which the deploy caps with `NODE_OPTIONS=--max-old-space-size=1024`), not steady-state
serving. This is a useful reference point: *a non-trivial multi-agent platform fits comfortably on a
single mid-tier VPS.* You are almost certainly over-provisioning if you start with a cluster.

Next: **[04 · CI/CD — Push-to-Deploy](./04-cicd.md)**.
