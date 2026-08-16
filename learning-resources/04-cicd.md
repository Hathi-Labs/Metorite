# 04 · CI/CD — Push-to-Deploy

Metorite deploys by **merging to `main`**. There is no separate "deploy" button, no manual SSH, no
release ceremony. A GitHub Actions workflow (`.github/workflows/deploy.yml`) watches `main`, gates the
change behind lint and tests, then SSHes into the VPS and updates it in place. This chapter walks the
pipeline and the reasoning, so you can build the same for your own project.

---

## 1. The pipeline at a glance

```
 push to main
      │
      ▼
┌──────────┐   needs   ┌──────────┐   needs   ┌────────────────────┐   needs   ┌──────────┐
│  lint    │──────────▶│  test    │──────────▶│  deploy (SSH)      │──────────▶│  smoke   │
│ ruff+mypy│           │ pytest -x│           │ pull→compose→migrate│           │ GET /health│
│(non-block)│          │ (GATE)   │           │ →uv sync→restart    │           │(informational)│
└──────────┘           └──────────┘           └────────────────────┘           └──────────┘
```

Four jobs, chained with `needs:` so each waits for the previous. The trigger:

```yaml
on:
  push:
    branches: [main]
    paths-ignore: ["**.md", "project-docs/**", "skills/**", "workbench/e2e/**"]
  workflow_dispatch:            # manual run, with an emergency skip_tests toggle
```

Note `paths-ignore`: a docs-only or planning-only change **doesn't trigger a deploy**. Cheap, obvious,
and it saves a lot of pointless VPS churn. (This is also why these learning-resource `.md` files won't
trigger a deploy.)

---

## 2. Gate 1 — lint (informational, non-blocking)

Ruff and mypy run but with `continue-on-error: true`:

```yaml
- name: Ruff lint (info-only, not blocking)
  continue-on-error: true
  run: uv run ruff check . || echo "ruff found issues (non-blocking)"
```

**Design choice:** style/type issues are *surfaced* but don't *block* a deploy. This is a pragmatic
small-team stance — you see the warnings, but a lint nit never stops a needed fix from shipping. If your
team wants hard enforcement, drop the `continue-on-error`. The important thing is to *decide* which gates
are advisory and which are load-bearing.

---

## 3. Gate 2 — tests (the real gate)

```yaml
- name: Run unit tests
  run: uv run python -m pytest tests/unit/ -x -v
```

This one has **no** `continue-on-error`. If a unit test fails, the `deploy` job never runs — broken code
cannot reach production. The `-x` flag stops at the first failure (fail fast). This is *the* safety
property of push-to-deploy: **the test suite is the thing standing between a bad merge and a live
outage**, so it must be trustworthy and reasonably fast.

> Practical consequence for contributors: run `pytest tests/unit/` locally *before* merging to `main`,
> because the merge itself is the deploy trigger and the suite runs with `-x`.

---

## 4. Gate 3 — deploy over SSH

The deploy job uses `appleboy/ssh-action` to run an **idempotent script** on the VPS. The script (held in
one `DEPLOY_SCRIPT` env var so it's a single source of truth) does, in order:

1. **Back up runtime-mutated state**, then `git fetch && git reset --hard origin/main`. (The reset is why
   `agents.json` is backed up first — see chapter 03 §5.)
2. **Reconcile `.env`** — idempotently ensure required env vars exist (OAuth tenant IDs, public URLs,
   memory-layer toggles). This lets deploys introduce new required config without a human editing `.env`.
3. **`docker compose --profile core up -d --remove-orphans`** — bring the stateful infra to desired state.
4. **Wait for healthchecks** (up to 90s) — don't proceed until Postgres/Redis report healthy.
5. **`apply_migrations.sh`** — run any new numbered SQL migrations against Postgres.
6. **`uv sync`** — install any new/changed Python dependencies.
7. **`systemctl restart acb-gateway`**, then assert it came back active (`systemctl is-active`).
8. **Rebuild the Next.js workbench** (`npm ci && rm -rf .next && npm run build`, heap-capped), reinstall
   its systemd unit, restart it, assert active.
9. **Reload Caddy**, then run an infra probe (`scripts/check_infra.py`).

**Everything is idempotent** — re-running the whole script after a half-finished attempt is safe. That
property is what makes the next feature (retries) possible.

---

## 5. Surviving flaky networks: retry with backoff

The single most practical lesson in this pipeline: **the SSH dial itself is the flakiest step**, so the
deploy is attempted up to **three times with increasing backoff** (0 → 60s → 150s):

```yaml
- name: Deploy via SSH (attempt 1)
  id: deploy1
  continue-on-error: true          # allow a retry
  uses: appleboy/ssh-action@v1
  with: { ...; timeout: 120s; command_timeout: 15m; script: ${{ env.DEPLOY_SCRIPT }} }

- name: Back off before attempt 2
  if: steps.deploy1.outcome == 'failure'
  run: sleep 60

- name: Deploy via SSH (attempt 2)          # ...same, id: deploy2
- name: Back off before attempt 3           # sleep 150
- name: Deploy via SSH (attempt 3, final)   # NOT continue-on-error → real failure if this fails
```

The backoff matters: an immediate re-dial during a multi-minute network blip just fails again. Waiting
progressively longer *outlasts* the blip. Because the deploy script is idempotent, a retry after a
partial run simply converges to the same end state. Only the *third* attempt is allowed to fail the job.

---

## 6. Gate 4 — smoke test (informational)

The final job curls the **public** health endpoint with generous retries (~5 min):

```yaml
- run: |
    for attempt in $(seq 1 30); do
      curl -fsS --max-time 10 "$GATEWAY_URL/health" && exit 0
      sleep 10
    done
    exit 1
```

It's `continue-on-error` on purpose: the deploy job *already* verified the gateway is healthy **on the
box** before finishing. This probe hits the public URL, which can flap for a couple of minutes while the
Next.js rebuild saturates the small VPS. A transient public-URL flap must not mark an otherwise-good
deploy as failed. **Lesson:** distinguish "is the service actually up" (gated, checked locally) from "is
the edge momentarily busy" (informational). Don't let the latter page you.

---

## 7. Secrets

The VPS SSH credentials and any deploy-time secrets live in **GitHub Actions repository secrets**
(`HOSTINGER_HOST`, `HOSTINGER_USER`, `HOSTINGER_SSH_KEY`, `HOSTINGER_APP_DIR`, …). CI holds them; they
never appear in the repo. The runtime app secrets (API keys, OAuth tokens) live in the VPS's `.env` and
the encrypted Postgres key store — a separate concern from *deploy* secrets. Keep those two buckets
mentally distinct: CI needs *how to reach and update the box*; the app needs *how to reach third-party
services*.

---

## 8. The recipe, generalized

If you're building your own push-to-deploy:

1. **Trigger on merge to your release branch**, ignoring paths that can't affect runtime.
2. **Gate on a fast, trustworthy test suite** with fail-fast. This is the only non-negotiable gate.
3. **Make the remote deploy script idempotent** — desired-state, safe to re-run — then wrap it in
   **retries with backoff** to absorb network flakiness.
4. **Assert liveness on the box** as part of deploy (restart → `is-active`), and treat public-URL probes
   as informational.
5. **Apply DB migrations as an ordered, idempotent step** in the same script (numbered SQL files +
   a glob-and-sort applier is enough to start).
6. **Keep deploy secrets in CI, app secrets on the box.**

Next: **[05 · Authentication & OAuth](./05-auth-and-oauth.md)**.
