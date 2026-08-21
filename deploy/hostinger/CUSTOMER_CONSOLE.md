# Deploying the Customer Console (VPS service → Supabase data)

The Customer Console is the subscription / seat / AI-metering engine
(`project-docs/specs/customer_console.md`, WS-31). This is the runbook to stand
it up and to **manually onboard a customer** (bank-transfer / no Razorpay).

## Architecture (D34 + D47)

- **Data plane:** the dedicated **Supabase Console project** (managed Postgres,
  Mumbai). D34.
- **Service:** runs on **this VPS** as its own systemd unit
  (`acb-customer-console.service`), bound to **`127.0.0.1:8090`**. D47. The
  gateway/BFF reach it locally; its only public surface is the Razorpay webhook,
  which is deferred — so nothing is exposed and no Caddy vhost is needed yet.
- Relocation trigger (D47): the day a second deployment exists, the service
  leaves the box — re-point `CUSTOMER_CONSOLE_URL`, the data doesn't move.

Everything below is **OWNER-GATE** (deploy/SSH, secrets, flag flips). Ships dark:
until the flags in step 5 are on, sign-in and metering behave exactly as today.

## Prerequisite (owner)

The Supabase Console project's **connection string** (direct `:5432` or the
session pooler — *not* the transaction pooler; the service uses multi-statement
transactions).

## 1 — Service env file

```bash
cd /opt/acb/app
cp deploy/hostinger/customer_console.env.example \
   apps/services/customer_console/.env
chmod 600 apps/services/customer_console/.env
# edit the file: paste CUSTOMER_CONSOLE_DATABASE_URL (Supabase) + HOME_STATE,
# and generate the three secrets:
for v in OPERATOR_TOKEN INTERNAL_TOKEN ENCRYPTION_KEY; do
  echo "CUSTOMER_CONSOLE_${v}=$(openssl rand -hex 32)"
done   # paste these three lines into the .env (replacing the blanks)
```

Keep `CUSTOMER_CONSOLE_OPERATOR_TOKEN` handy — it's the credential you onboard
with. Razorpay vars stay unset.

## 2 — Apply the schema ladder to Supabase

```bash
set -a; . apps/services/customer_console/.env; set +a
bash scripts/apply_customer_console_migrations.sh   # idempotent; safe to re-run
```

This applies `infra/customer_console/001…007.sql` to the Supabase project. Re-run
it whenever a new ladder file lands (it is NOT part of the tenant deploy).

## 3 — Start the service

`deploy.sh` installs `acb-customer-console.service` automatically on the next
deploy, but (like every service) does not enable/start it — do that once:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acb-customer-console
curl -fsS http://127.0.0.1:8090/health && echo OK
sudo systemctl status acb-customer-console --no-pager
```

After a Console **code** change (a merged PR touching `customer_console/`), the
box's `uv sync` updates the venv but does not restart this unit — run
`sudo systemctl restart acb-customer-console` to pick it up.

## 4 — Wire the gateway to the Console

Mint a deployment key with the gateway's capabilities, using the operator token:

```bash
OP="$(grep '^CUSTOMER_CONSOLE_OPERATOR_TOKEN=' apps/services/customer_console/.env | cut -d= -f2-)"
curl -fsS -X POST http://127.0.0.1:8090/keys \
  -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' \
  -d '{"label":"gateway","capabilities":["resolve","provision","seat_admin"]}'
# → returns a cc_depl_… key ONCE. Copy it.
```

Then add to the **gateway** env `/opt/acb/app/.env`:

```
CUSTOMER_CONSOLE_URL=http://127.0.0.1:8090
CUSTOMER_CONSOLE_DEPLOYMENT_KEY=cc_depl_…
```

`sudo systemctl restart acb-gateway` (and the workbench if it reads these).

## 5 — Go-live flags (enforcement-flip)

Set on the box, then restart the gateway/workbench. Requires an
`enforcement-flip` grant.

- `CUSTOMER_CONSOLE_RESOLVE_ENABLED=true` — turns on Console-backed sign-in +
  entitlements. **Needed for both** manual onboarding and self-serve signup.
- `SELF_SERVE_SIGNUP_ENABLED=true` — turns on the landing page's "Sign up"
  self-serve flow (CP-2c). Optional; only for self-serve.

## 6 — Customer sign-in config (owner, Azure/Google consoles)

So customers' users can authenticate (they need a Microsoft work/school or Google
account; email/OTP is CP-2d, unbuilt):

- **Microsoft Entra:** set the app registration to **multi-tenant** (accounts in
  any org directory).
- **Google:** configure the Google OAuth app / consent screen and add the client
  id/secret the workbench expects.

## Manually onboarding a customer (bank transfer)

With the operator token (`$OP`), against `http://127.0.0.1:8090`:

```bash
# 1. Create their org; their admin's email becomes the owner (gets a tenant
#    account + owner role, so they can sign in immediately).
curl -fsS -X POST http://127.0.0.1:8090/orgs/provision \
  -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' \
  -d '{"slug":"acme","owner_email":"admin@acme.com","core_seats":5,
       "deployment_label":"…"}'

# 2. Activate their PAID plan + seats + credits — no Razorpay (the bank-transfer
#    grant, §6 item (j)). `reference` records the transfer.
curl -fsS -X POST http://127.0.0.1:8090/billing/subscriptions/activate \
  -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' \
  -d '{"org_slug":"acme","plan_slug":"…","seats":5,"credits":250,
       "reference":"NEFT ref 12345"}'
```

The customer signs in at `https://app.metorite.com` with Microsoft/Google and is
admitted to their org; their admin invites the rest of the team
(`POST /admin/members` → set active).

> Exact request fields: confirm against the route models in
> `apps/services/customer_console/customer_console/main.py`
> (`ProvisionRequest`, `ManualActivationRequest`) at deploy time — they are the
> source of truth.
