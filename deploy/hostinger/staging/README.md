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
`/opt/acb/app/.env` on the staging box (HANDOFF H-358). It refuses unless the
file says `DEPLOY_ENV=staging`. It writes only an allowlisted flag name, and it
never writes a secret, a host or an outbound switch. It makes a backup before
each write and keeps the file's inode, owner and mode. Then it restarts the
unit that reads the key and waits for `/health`. Fence:
`tests/unit/test_acb_staging_flag.py`.

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

⚠️ A `NEXT_PUBLIC_*` key does not reach the workbench through this file. The
workbench reads `workbench/control_plane/.env.local`, and Next.js puts each
`NEXT_PUBLIC_*` value into the bundle at `next build`. A restart does not change
it. The script warns about this on each `NEXT_PUBLIC_*` write.
