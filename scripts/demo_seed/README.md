# Demo seed: Kite & Co. Interiors

This folder fills a LOCAL Metorite stack with a made-up company, so that you
can take marketing screenshots. Nothing here touches production.

"Kite & Co. Interiors" is fictional. The seed invents all people, addresses and
phone numbers. Email addresses use the reserved `.example` domain. Phone
numbers use the `+91 98000 000NN` range.

## What the seed writes

| App | Route | How the seed writes it |
|---|---|---|
| People | `/people` | SQL: 8 members, titles, managers and skills |
| Projects | `/projects` | Gateway API: 1 space, 4 projects, 25 tasks |
| My Tasks and Calendar | `/tasks`, `/calendar` | Gateway API: personal tasks and time blocks |
| My Email | `/email` | SQL: 1 mailbox, threads, triage and insights |
| My WhatsApp | `/whatsapp` | SQL: 1 Cloud API number, chats and triage |
| Chat | `/chat` | SQL: past conversations |
| Approvals | `/approvals` | SQL: pending approvals |

The seed uses the gateway API where the app has a write API. It uses SQL for
data that only an integration writes, such as a mail sync or a WhatsApp
webhook.

## The signed-in member

The local app signs in as `dev@fracktal.in` when no SSO is set. That address
is in the code (`DEV_IDENTITY` in `workbench/control_plane/src/lib/gateway.ts`).
The seed does not change the code. It gives that member the name "Asha Rao",
so the app shows the demo name.

## How to run it

Do these steps in order, from the repo root. Each port is free on purpose, so
the stack does not touch the shared scratch databases.

1. Start a new database container on port 5436.

   ```bash
   docker run -d --name metorite-landing-demo \
     -e POSTGRES_USER=acb -e POSTGRES_PASSWORD=acb -e POSTGRES_DB=acb_tenant \
     -p 127.0.0.1:5436:5432 pgvector/pgvector:pg16
   ```

2. Apply the base schema once, then the migration ladder.

   ```bash
   docker exec metorite-landing-demo psql -U acb -d acb_tenant -q \
     -c 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"; CREATE EXTENSION IF NOT EXISTS pgcrypto;'
   docker exec -i metorite-landing-demo psql -U acb -d acb_tenant \
     -v ON_ERROR_STOP=1 -q < infra/postgres/01_schema.sql
   APP_DIR="$PWD" PG_CONTAINER=metorite-landing-demo PG_USER=acb \
     PG_DB=acb_tenant SKIP_PRE_MIGRATION_BACKUP=1 bash scripts/apply_migrations.sh
   ```

3. Start the gateway from the repo root. It makes the owner member at startup.

   ```bash
   DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5436/acb_tenant \
   ACB_ENV=dev GATEWAY_INTERNAL_TOKEN=dev-local-token \
   EXECUTIVE_EMAILS=dev@fracktal.in \
   uv run uvicorn gateway.main:app --host 127.0.0.1 --port 8011
   ```

4. Start the web app in a second shell.

   ```bash
   cd workbench/control_plane
   GATEWAY_BASE_URL=http://127.0.0.1:8011 GATEWAY_INTERNAL_TOKEN=dev-local-token \
   EXECUTIVE_EMAILS=dev@fracktal.in npm run dev -- -p 3002
   ```

5. Run the seed once, against the empty database.

   ```bash
   uv run python scripts/demo_seed/seed_demo.py
   ```

6. Open <http://localhost:3002>.

## How to run it again

To run one part only, use `--only`. You can give it more than once.

These parts replace their own rows, so you can run them again at any time:
`email`, `whatsapp`, `chat` and `approvals`. Run them again before a
screenshot session, so that the message times look recent.

```bash
uv run python scripts/demo_seed/seed_demo.py --only email --only whatsapp --only chat --only approvals
```

The `people`, `projects` and `mytasks` parts use the gateway API, and they
add a second copy if you run them twice. To start again from nothing, remove
the container, then do all six steps again. Restart the gateway after the
database is new, because the gateway makes the owner member at startup.

```bash
docker rm -f metorite-landing-demo
```

## Message times

Email, WhatsApp, chat and approval times count back from "now" when the seed
runs. At night (21:00 to 09:00 IST) the seed uses 18:30 IST of the last
evening as "now". This stops the inbox from showing messages sent at midnight.

## Things to avoid

- Do not press "Sync now" in My Email. The mailbox has a placeholder
  credential, so the sync fails and the app asks you to reconnect. Run the
  `email` part again to repair it.
- Do not press "Suggest reply" or send a message in My WhatsApp. These call
  a live model or Meta, and the local stack has neither.
- Do not press "Approve & run" in Approvals. No handler is set for these
  actions, so the row changes to "failed". Run the `approvals` part again to
  repair it.

## Settings

The seed reads these environment variables. Each has a local default.

| Variable | Default |
|---|---|
| `DEMO_DATABASE_URL` | `postgresql://acb:acb@127.0.0.1:5436/acb_tenant` |
| `DEMO_GATEWAY_URL` | `http://127.0.0.1:8011` |
| `DEMO_GATEWAY_TOKEN` | `dev-local-token` |
| `DEMO_SIGNED_IN_EMAIL` | `dev@fracktal.in` |

The seed stops if the database or the gateway is not on `127.0.0.1` or
`localhost`.
