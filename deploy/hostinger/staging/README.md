# Units for the STAGING box only

The files here belong to the staging deployment on the dev box (WS-38,
`project-docs/specs/staging_and_daily_release.md` §5.2 and §5.11).

**Production never reads this folder.** `scripts/vps_apply.sh` installs the
units of `deploy/hostinger/*.service` and `*.timer` on every box, and H-132 says
that each timer there arrives scheduled. A unit that must not run on production
is therefore not shipped there. It is shipped here, and only the staging branch
of `vps_apply.sh` reads this folder. Fence: `tests/unit/test_staging_box.py`.

| Unit | What it does |
|---|---|
| `acb-staging-db.service` | The staging database: Postgres 17, the version of the production cluster, on `127.0.0.1:5435` |
| `acb-console-tunnel.service` | A one-port SSH tunnel to the production Customer Console, for the shared AI Router |
| `acb-staging-refresh.service` and `.timer` | Each night, pulls the reduced copy of production and rebuilds the staging database from it |
| `acb-staging.slice` | Caps the memory and CPU share of the staging services, because agents share the box |
| `*.service.d/60-staging-slice.conf` | Puts each staging service in that slice |
