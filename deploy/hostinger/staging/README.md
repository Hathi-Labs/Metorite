# Units for the STAGING box only

The files here belong to the staging deployment on the dev box (WS-38,
`project-docs/specs/staging_and_daily_release.md` §5.2 and §5.11).

**Production never reads this folder.** `scripts/vps_apply.sh` installs the
units of `deploy/hostinger/*.service` and `*.timer` on every box, and H-132 says
that each timer there arrives scheduled. So a unit that must not run on production
is not shipped there. It is shipped here, and only the staging branch
of `vps_apply.sh` reads this folder. Fence: `tests/unit/test_staging_box.py`.

| Unit | What it does |
|---|---|
| `acb-staging-db.service` | The staging database: Postgres 17, the version of the production cluster, on `127.0.0.1:5435` |
| `acb-console-tunnel.service` | A one-port SSH tunnel to the production Customer Console, for the shared AI Router |
| `acb-staging-refresh.service` and `.timer` | Each night, pulls the reduced copy of production and rebuilds the staging database from it |
| `acb-staging.slice` | Caps the memory and CPU share of the staging services, because agents share the box |
| `*.service.d/60-staging-slice.conf` | Puts each staging service in that slice |

## Set a flag on staging: `acb-staging-flag`

`acb-staging-flag` is a root script that sets or clears a feature flag in
`/opt/acb/app/.env` on the staging box (HANDOFF H-358). Fence:
`tests/unit/test_acb_staging_flag.py`.

- It refuses unless the file says exactly `DEPLOY_ENV=staging`.
- It writes only one of the exact names below. A deny list of secret, host
  and outbound words is a second wall.
- It refuses each `NEXT_PUBLIC_*` key. The workbench reads those from
  `workbench/control_plane/.env.local`, and Next.js puts each value into the
  bundle at `next build`, so a restart cannot change it.
- Root does no file work. Each read and each write runs as `acb`.
- It makes a backup before each write and keeps the newest 20. It keeps the
  inode, the owner and mode 600 of the file.
- It records each key that it sets in `/opt/acb/app/.staging-flags`. Each
  night, `scripts/staging_refresh.sh` copies production's switches to staging,
  and it skips each key in that file. `unset` takes the key out again.
- It restarts `acb-gateway` and waits for `/health`.

The names it may write:

| Name | Why it is safe on staging |
|---|---|
| `KNOWLEDGE_ENABLED` | The Knowledge kill switch |
| `KNOWLEDGE_ORGS` | The organizations that Knowledge serves |
| `KNOWLEDGE_EMBED` | It spends `tier-embed` credits. The owner asked for embeddings on staging |
| `KNOWLEDGE_EMBED_SWEEP` | The embed sweep of KB-3c, with the same reason |
| `COMPANY_SEARCH_ENABLED` | Company retrieval for the assistant |
| `CONTEXT_RECENCY` | Recency in company retrieval (WS-61 CX-1). It spends nothing |
| `MY_DAY_PROVIDERS` | The gated My Day feed sources, which read only the database |
| `ARTIFACT_REGISTRY_ENABLED` | The artifact registry hook and its read routes |
| `CRM_ENABLED` | The CRM kill switch |
| `CRM_ORGS` | The organizations that the CRM serves |

Three names stay off the list on purpose. `DECIDE_ENABLED` is owner-only and
sends content to a third party. `DECIDE_FEATURE_MODES` is owner gate H-166 on
a box. `NARROWING_AGENTS` spends Router credits for each agent.

The owner installs it one time, as root on the dev box:

    install -o root -g root -m 755 /opt/acb/app/deploy/hostinger/staging/acb-staging-flag /usr/local/sbin/acb-staging-flag
    echo 'dev ALL=(root) NOPASSWD: /usr/local/sbin/acb-staging-flag' > /etc/sudoers.d/acb-staging-flag
    chmod 0440 /etc/sudoers.d/acb-staging-flag
    visudo -cf /etc/sudoers.d/acb-staging-flag

Then `dev` uses it like this:

    sudo -n /usr/local/sbin/acb-staging-flag --list
    sudo -n /usr/local/sbin/acb-staging-flag set KNOWLEDGE_ENABLED true KNOWLEDGE_ORGS '*'
    sudo -n /usr/local/sbin/acb-staging-flag unset KNOWLEDGE_ORGS
    sudo -n /usr/local/sbin/acb-staging-flag --no-restart set CRM_ORGS '*'

⚠️ On srv1747539, `dev` and `acb` are in group `docker`, and
`/var/run/docker.sock` is `root:docker` 0660. That group is root-equivalent.
So until the owner removes `dev` and `acb` from `docker`, this sudoers line is
a convenience, not a security boundary. Removing them from `docker` also needs
every agent workflow that uses docker (the scratch Postgres ladder) to keep
working. So decide it as its own step.
