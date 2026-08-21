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

- `SELF_SERVE_SIGNUP_ENABLED=true` — turns on the `/signup` flow. **Required** —
  it is how a customer gets their tenant account (see onboarding below) *and*
  what the landing-page "Sign up" CTA needs. Default OFF.
- `CUSTOMER_CONSOLE_RESOLVE_ENABLED=true` — makes sign-in CONSULT the Console for
  admit/entitlements. **Optional for the MVP:** leave OFF and sign-in is admitted
  by the tenant account self-signup created; turn it ON when you want Console-
  enforced sign-in/entitlements. Either position lets a signed-up customer in.

## 6 — Customer sign-in config (owner, Azure/Google consoles)

So customers' users can authenticate (they need a Microsoft work/school or Google
account; email/OTP is CP-2d, unbuilt):

- **Microsoft Entra:** set the app registration to **multi-tenant** (accounts in
  any org directory).
- **Google:** configure the Google OAuth app / consent screen and add the client
  id/secret the workbench expects.

## Manually onboarding a customer (bank transfer)

The tenant-side account a customer signs in with is created **only by self-serve
signup** — the gateway provision route is session-email-only (the owner is always
the signed-in user, R11), and no operator route or script creates a customer's
tenant org, while the Console's `/orgs/provision` writes only the Console
registry (it can't reach the tenant DB). So the flow is **self-serve signup +
your manual paid activation**:

**1 — Customer self-signs-up** (needs `SELF_SERVE_SIGNUP_ENABLED=true`, step 5).
They sign in via Microsoft/Google (org-less), open
`https://app.metorite.com/signup`, and submit their org slug + display name +
registered state (+ optional GSTIN). That single submit creates their **tenant
org + owner account** AND the **Console registry row + Core seats** (the gateway
mirrors to the Console via the deployment key). They can sign in and use the
product **immediately** — on Core seats, no subscription yet.

**2 — You activate their paid plan** once the bank transfer clears, with the
operator token, against the Console:

```bash
OP="$(grep '^CUSTOMER_CONSOLE_OPERATOR_TOKEN=' apps/services/customer_console/.env | cut -d= -f2-)"
curl -fsS -X POST http://127.0.0.1:8090/billing/subscriptions/activate \
  -H "Authorization: Bearer $OP" -H 'Content-Type: application/json' \
  -d '{"org_slug":"acme","plan_slug":"…","seats":5,"credits":250,
       "reference":"NEFT ref 12345"}'
```

→ `org_subscription` goes `active` (provider `manual`), paid seats + AI credits
granted (§6 item (j)). `org_slug` is the slug the customer chose at signup.

**3 —** Their admin invites the rest of the team (`POST /admin/members` → set
`active`, in-app).

> **Pure operator onboarding** — creating the customer's tenant org WITHOUT them
> self-signing-up — is NOT wired. It would need a small tenant-side CLI calling
> `provision_local_organization(slug, owner_email=<customer>)` inside the tenant
> container, then mirroring to the Console via the `/orgs/provision` deployment-key
> arm. Add it only if the single self-signup step is unacceptable.

> Exact request fields: confirm against the route models in
> `apps/services/customer_console/customer_console/main.py`
> (`ManualActivationRequest`) and the signup form — they are the source of truth.

> Exact request fields: confirm against the route models in
> `apps/services/customer_console/customer_console/main.py`
> (`ProvisionRequest`, `ManualActivationRequest`) at deploy time — they are the
> source of truth.
