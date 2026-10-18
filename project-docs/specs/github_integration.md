# GitHub — a Metorite-owned GitHub App, webhooks, and pull requests on the task

**Status: 🔨 GH-1 to GH-7 BUILT on branch `github-batch-a`, fix round 1 done, in review (2026-10-18).** GH-1 to GH-6 ship dark behind `github_app_enabled`. GH-7 goes live at the merge (§16). The migration is 282. GH-0 (register the two Apps) and GH-8 (go live) wait for the owner. §16.1 records each slice and the fix round.

The owner decided G-1 to G-9 on 2026-10-18 (§3). No owner question is open (§20). **Verified against code on 2026-10-18 at `origin/main` `1625cca13`.** The GitHub facts in §4.2 were read on docs.github.com on 2026-10-18.

**Board row:** WS-54 (`work_plan.md` §2). **Consumers:** WS-57 (`task_references.md`) reads the pull request state and the checks. WS-55 (`external_agent_access.md` §12.2, EA-13) draws them on the card.

**Owner of the work:** this spec owns the GitHub App, its connect flow and the webhook receiver. It also owns the checks columns of `pm_task_refs`, the reconcile poll and the merge-to-Done rule. `task_references.md` keeps the table, the route and the Links section. `external_agent_access.md` keeps the card badge, the PR chip and the agent oversight (EA-13). `integrations_console.md` keeps the registry and the console.

## 1. The answer in one screen

1. **Metorite owns one GitHub App for each environment.** The owner registers it once, with a script and one click (§15). Staging and production each get one App, because an App has one webhook URL.
2. **An admin connects in a few clicks.** Connect GitHub, pick the repositories on GitHub, land back in Metorite, and confirm. No token, no client id, no BYOK.
3. **GitHub pushes the facts.** A webhook carries the pull request state and the CI state. A slow poll repairs a missed delivery.
4. **A pull request links itself.** The task URL in the description, or the task id in the branch name, adds the reference.
5. **A merge can close the task.** Metorite moves the task to Done only when an agent worked it, by a rule that a test holds (§12).
6. **The old token stays as "Advanced".** A pasted token still works, with no BYOK coupling. The device flow and the customer-made OAuth App go.

## 2. Scope and non-goals

**In scope:** GH-0 to GH-8 (§16).

**Not in scope:**

- **A write to GitHub.** The App asks for read permissions only (§13.2). It posts no comment, no check and no status. A later slice that needs a write names its permission and its reason first.
- **More than one installation for each organization.** One org maps to one installation in v1. The unique index of `integration_connections` already allows one `github` row for each org (§9.3). The owner confirmed this rule on 2026-10-18 (G-6).
- **User-to-server tokens for a session.** WS-56 (`external_agent_dispatch.md` D-DX-2) proposes them. This App is the App that WS-56 would use, and its manifest already names a callback URL. This spec uses a user token only once, to check an installation (§7.3), and keeps none.
- **GitLab and Bitbucket.** `task_references.md` reads only GitHub, and so does this spec.
- **Issues.** The webhook reads pull requests and checks. An issue reference keeps the lazy read of TR-1.
- **A status move other than to Done.** No move to in review on open, and never a move to cancelled. The agent routine moves to in review itself (EA3-39 item 5).
- **A change to `link_from_text`.** The lazy text links of TR-5 stay as they are (G-3).

## 3. The owner decisions (2026-10-18)

| # | Decision | Why |
|---|---|---|
| G-1 | **A Metorite-owned GitHub App.** An org admin clicks Connect GitHub in Integrations, installs the App on a GitHub account or organization, picks the repositories, and lands back in Metorite. The owner registers the App once, through a scripted App manifest flow. Staging and production each get one App. Registering the App is the OWNER-GATE slice (GH-0) | The house rule says customer setup is one click and Metorite owns the app. Today the device flow needs the customer's own OAuth App client id, and BYOK blocks it |
| G-2 | **Webhooks carry the pull request state and the CI state** (check suites, check runs and commit statuses) in near real time. A slow reconcile poll repairs a missed delivery | This replaces the 15-minute lazy read as the main path. GitHub does not redeliver a failed delivery by itself (§4.2 fact 9) |
| G-3 | **Auto-link.** A pull request links itself to a task when its description holds the Metorite task URL, or its head branch holds the task id. The id is the UUID or the short form `mt-<8 hex>` (§10.2). `link_from_text` stays as it is | `task_number` is unique only per root project, and `task_prefix` has no unique constraint (§10.2) |
| G-4 | **On merge, Metorite moves the task to Done only when an agent worked it.** An agent run existed, or the claim came through MCP. Never a task that people run. Never to cancelled. The done lane, the recurrence and the successors of the project apply. The timeline names a GitHub actor | An agent closes the loop after a person merges (EA3-32, owner answer 1 of the sixth audit). A person keeps the decision on their own tasks |
| G-5 | **The old setup stays as an "Advanced" fallback**: a pasted token, with no BYOK coupling. The customer-made OAuth App client id path and the device flow that depends on it are retired | A token reaches repositories that an App installation cannot, for example a personal account that will not install an App |
| G-6 | **One GitHub installation for each Metorite org.** A company with two GitHub organizations connects one of them in v1 | The registry already holds one `github` row for each org (§9.3), so v1 needs no new key |
| G-7 | **The production App is public,** so any customer can install it. The staging App stays private | A private App installs only on the account that owns it, and a customer must install the production App with no help |
| G-8 | **The Hathi Labs organization on GitHub owns both Apps,** staging and production | Metorite is the Hathi Labs product. The owner of an App controls its settings and its private key |
| G-9 | **The App asks for no Contents permission.** An org that needs a private agent repository's `config.json` keeps the Advanced token (G-5) | Least privilege. Only the agent config fetch needs Contents (§6.3) |

**G-6 to G-9** were the four open questions of the first draft. The owner took each proposed answer on 2026-10-18.

**Scope and tenancy (owner, 2026-10-18).** These rules bind every slice:

- An installation id maps to exactly one org. A webhook for an unknown installation is dropped.
- The App asks for the least privilege.
- The HMAC check uses a constant-time compare.
- The `X-GitHub-Delivery` id makes a delivery idempotent, and the body has a size cap.
- Every webhook write is bound to its tenant (R5).
- Installation tokens are cached and short-lived.
- The private key lives in the operator plane only (D56), and never in a customer row.

## 4. What exists today

### 4.1 The code (re-verified 2026-10-18 at `1625cca13`)

| Fact | Where |
|---|---|
| The org's GitHub token is one row of `integration_connections`: `PROVIDER = "github"`, `OWNING_APP = "integrations"`, org scope | `apps/services/gateway/gateway/connections/github.py:37` and `:39`. The readers are `token_or_none` (:164) and `token_for_org` (:173). `save_device_token` (:93) and `disconnect` (:200) write |
| The device flow and the account card | `routes/integrations.py`: `POST /integrations/github/device/start` (:2465), `POST …/device/poll` (:2508), `GET …/github/account` (:2663), `DELETE /integrations/github` (:2699), `POST …/github/connect-cli` (:2726, answers 410) |
| The device flow uses the customer's own OAuth App | `_github_org_store` (:2441) reads the org row `github:client_id` (:2457), and falls back to the deployment `github_client_id` (:2461) |
| BYOK blocks the device poll | `_refuse_provider_key_without_byok(["GITHUB_TOKEN"])` at :2528 and :2743. The gate is at :643. `GITHUB_TOKEN` is in `_PROVIDER_ENV_MAP` because it once routed the Copilot models |
| A pasted token | `_store_github_pat` (:2622), called from configure (:1083-1085) and `PUT /keys` (:1235-1237) |
| The UI | `components/GitHubDeviceConnect.tsx` (imported by `agents/page.tsx`, `AddAgentWizard.tsx`, `IntegrationSetup.tsx` and `GitHubAccountBadge.tsx`). `components/GitHubAccountBadge.tsx` still calls the retired `connect-cli`. `app/integrations/components/ApiKeysView.tsx:455-456` draws the badge for the `github` service |
| Settings that nothing reads | `packages/acb_common/acb_common/settings.py`: `github_client_id` (:847), `github_app_id` (:852), `github_app_private_key_path` (:853), `github_installation_id` (:854). Only docstrings in `acb_skills/loader.py` and the `env_guard.py:137` list name them |
| No GitHub webhook receiver | `X-Hub-Signature` appears only in the WhatsApp receiver, `routes/whatsapp/transport/webhook.py` (`verify_signature` :49) |
| The public route list | `PUBLIC_ROUTES` in `gateway/main.py:696`, matched on the route template, applied at :794 through `require_authenticated` (`acb_auth/deps.py:1137`). It holds `/webhooks/gmail` and `/webhooks/zoho` |
| The public path | `deploy/hostinger/caddy/Caddyfile` and `Caddyfile.staging`: `api.metorite.com` and `api.dev.metorite.com` send every path except `/internal/*` to the gateway. A new gateway path needs no Caddy entry |
| The store | `connections/store.py` is the one reader of the Fernet blob. Fence: `test_crm_connection.py::test_only_the_store_names_the_connections_table` |
| The signed state | `routes/email/transport/signing.py`: `sign_state(*, purpose, org, member)` (:233) and `verify_state(token, *, purpose)` (:261) |
| The tenant lookup for a webhook | `infra/postgres/230_wa_accounts_cloud_number_owner.sql`: one `SECURITY DEFINER` function returns the org of a WhatsApp number, and the route binds it. This spec copies the shape (§9.3) |
| The references | `pm_task_refs` (`255_pm_task_refs.sql`). `state` is `open`, `draft`, `merged` or `closed`. `via` has the CHECK `^(ui|chat|text|mcp:[a-z0-9:_.-]{1,60})$`. No column holds a check or a head commit |
| The lazy read | `routes/projects/refs.py`: `STALE_AFTER` 15 minutes (:70), `github_state` (:189), `_github_token` (:245), `refresh_from_github` (:258), `GET /tasks/{task_id}/refs?refresh=true` (:336) |
| The text links | `routes/projects/text_links.py:472` `link_from_text`. It stays as it is |
| The status core | `routes/projects/core.py:2907` `apply_status_transition`. It stamps `completed_at`, writes the `status_change` row and calls `recurrence.spawn_successor`. `load_default_status` is at :2756. `personal.py:2565` `complete_for_member` is the member path to done |
| The system actor shape | D-PM-4: `system:workflow:<id>` (`automation.py:92`) and `system:import:<source>` (`import_writer.py:1029`) |
| The agent runs | `routes/projects/agent_runs.py`, the only writer of `pm_agent_runs` (migration 267). The claim is `tasks.py:1484` `claim_task`, and it stamps `meta.via` through `record_activity` (`core.py:3131`) |
| A loop in the gateway | `gateway/my_day_loop.py`, started from the lifespan in `main.py:415`, with a Redis `SET NX` guard so one process runs each tick |
| The secrets drop | `scripts/secrets.sh` (`push <name>`) over `deploy/secrets/manifest.json`. The entry `app-env` merges keys into `/opt/acb/app/.env` and restarts `acb-gateway` |
| The highest migration | `281_crm_digests.sql` on 2026-10-18. Take the number at build time (R1) |

### 4.2 The GitHub facts (read 2026-10-18)

| # | Fact | Source |
|---|---|---|
| 1 | The manifest form POSTs to `https://github.com/settings/apps/new` for a personal account, and to `https://github.com/organizations/<ORG>/settings/apps/new` for an organization. Its fields are `name`, `url`, `hook_attributes`, `redirect_url`, `callback_urls`, `setup_url`, `description`, `public`, `default_events`, `default_permissions`, `request_oauth_on_install` and `setup_on_update`. `state` protects against request forgery. All three steps must finish within one hour | <https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest> |
| 2 | `POST /app-manifests/{code}/conversions` returns `id`, `slug`, `client_id`, `client_secret`, `webhook_secret` and `pem` | <https://docs.github.com/en/rest/apps/apps> |
| 3 | The App JWT is signed with `RS256`. Set `iat` 60 seconds in the past. `exp` is at most 10 minutes ahead. `iss` is the client ID or the App ID, and the docs recommend the client ID | <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app> |
| 4 | `POST /app/installations/{installation_id}/access_tokens` returns a token that expires after one hour, with `expires_at`. It can be narrowed by `repositories`, `repository_ids` and `permissions` | <https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app> |
| 5 | After an install GitHub adds `installation_id` to the setup URL. A bad actor can send a spoofed `installation_id`, so check with a user access token that the installation belongs to the user. `GET /user/installations` needs that user token | <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url>, <https://docs.github.com/en/rest/apps/installations> |
| 6 | With "Request user authorization (OAuth) during installation" on, the App has no setup URL. GitHub sends the user to the callback URL | <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app> |
| 7 | The install link is `https://github.com/apps/<slug>/installations/new`, and it takes `?state=` | <https://docs.github.com/en/apps/sharing-github-apps/sharing-your-github-app> |
| 8 | `X-Hub-Signature-256` is `sha256=` and the HMAC-SHA256 hex digest of the raw body with the webhook secret. Never compare with `==`. The headers are `X-GitHub-Hook-ID`, `X-GitHub-Event`, `X-GitHub-Delivery`, `X-GitHub-Hook-Installation-Target-Type` and `X-GitHub-Hook-Installation-Target-ID`. A payload is capped at 25 MB | <https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries>, <https://docs.github.com/en/webhooks/webhook-events-and-payloads> |
| 9 | Answer with a 2XX within 10 seconds. A redelivery keeps its `X-GitHub-Delivery` GUID. GitHub does not redeliver a failed delivery by itself. A person or the API can redeliver one from the past 3 days | <https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks>, <https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks> |
| 10 | `GET /app/hook/deliveries` and `POST /app/hook/deliveries/{delivery_id}/attempts` take the App JWT | <https://docs.github.com/en/rest/apps/webhooks> |
| 11 | `pull_request` needs Pull requests (read). `check_suite` and `check_run` need Checks (read). `status` needs Commit statuses (read). Every App gets `installation` and `installation_repositories` by default. The `requested` and `rerequested` actions need Checks (write) | <https://docs.github.com/en/webhooks/webhook-events-and-payloads> |
| 12 | `GET /repos/{o}/{r}/pulls/{n}` needs Pull requests (read). `GET …/commits/{ref}/check-runs` needs Checks (read). `GET …/commits/{ref}/status` needs Commit statuses (read). The docs say to select the minimum permissions, and an account owner must accept a new permission | <https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps>, <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app> |
| 13 | An installation token has 5,000 requests an hour, plus 50 for each repository and each user over 20, up to 12,500. An Enterprise Cloud organization has 15,000. The secondary limits are 100 concurrent requests and 900 points a minute | <https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api> |
| 14 | An App has one Webhook URL field, and `/app/hook/config` addresses one configuration | <https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app>, <https://docs.github.com/en/rest/apps/webhooks> |
| 15 | `GET /app/installations/{installation_id}` (App JWT) returns `account.login`, `account.type`, `repository_selection`, `suspended_at` and `suspended_by`. The `installation` event has the actions `created`, `deleted`, `suspend`, `unsuspend` and `new_permissions_accepted` | <https://docs.github.com/en/rest/apps/apps>, <https://docs.github.com/en/webhooks/webhook-events-and-payloads> |

**Not confirmed in the docs.** GH-3 and GH-5 measure these on staging and record the result here:

- **U1.** The docs do not say that `state` from the install link reaches the callback. §7.3 does not depend on it.
- **U2.** The docs do not name the `setup_action` values. Other sources show `install`, `update` and `request`.
- **U3.** The docs do not say whether an App with Checks (read) gets `check_suite` `completed` for a suite that another App owns, for example GitHub Actions. §11 covers the gap with the poll.
- **U4.** The docs do not say that Metadata (read) is mandatory. The manifest asks for it in any case.

## 5. The architecture

```
Integrations "Connect GitHub"
   │ POST /integrations/github/app/start      (gateway, integrations:manage)
   ▼
github.com/apps/<slug>/installations/new?state=…   (the admin picks repositories)
   │ GitHub redirects to the callback URL with code and installation_id
   ▼
app.metorite.com/api/integrations/github/callback  (BFF, member session)
   │ GET /integrations/github/app/callback     (gateway)
   │   check the pending connect, exchange the code, GET /user/installations,
   │   GET /app/installations/{id}, write the org row, drop the user token
   ▼
integration_connections  (provider github, provider_meta.auth = "app")

github.com ── POST https://api.metorite.com/webhooks/github ──► gateway
   HMAC → cap → org lookup (SECURITY DEFINER) → bind tenant → delivery key
   → handler: installation | pull_request | check_suite | check_run | status
   → pm_task_refs (state, checks, head_sha) → merge-to-Done (§12)

github_reconcile_loop (every 10 minutes) ── installation token ──► api.github.com
```

**The new files.** One module for each concern. No second store and no second token reader.

| File | Holds |
|---|---|
| `gateway/connections/github_app.py` | The App JWT, the installation token and its cache, and the installation read (§6) |
| `gateway/connections/github.py` | The one token reader, which now serves both modes (§6.3) |
| `gateway/routes/github/connect.py` | `POST /integrations/github/app/start`, `GET /integrations/github/app/callback`, and the token route of G-5 (§7) |
| `gateway/routes/github/webhook.py` | `POST /webhooks/github` and the handlers (§8) |
| `gateway/github_sync.py` | The pull request read and the checks summary, shared by the webhook, the refs route and the poll (§8.5, §11) |
| `gateway/github_reconcile_loop.py` | The reconcile poll (§11) |
| `gateway/routes/projects/github_autolink.py` | The link grammar of G-3 (§10) |
| `gateway/routes/projects/agent_worked.py` | The agent-worked rule, the one copy of its SQL (§12.2). EA-13e imports it |
| `gateway/routes/projects/github_merge.py` | The merge-to-Done rule (§12) |
| `scripts/github_app_manifest.py` | The registration script of GH-0 (§15) |

## 6. Auth: the App JWT, then the installation token

### 6.1 The App JWT

`github_app.app_jwt(now)` signs `{"iat": now - 60, "exp": now + 540, "iss": <client id>}` with `RS256` and the private key (§4.2 fact 3). It reads the key from `github_app_private_key_path`, once for each process, and keeps it in memory. A missing or unreadable key raises `GitHubAppNotConfigured`, and the caller answers 503 or skips the work. PyJWT with the `crypto` extra signs. The builder checks that the gateway declares that dependency, and adds it if not.

### 6.2 The installation token and its cache

- `github_app.installation_token(installation_id)` calls `POST /app/installations/{id}/access_tokens` with the JWT (§4.2 fact 4). It sends no `repositories` narrowing. The installation's own repository choice is the reach.
- **The cache lives in process memory only.** The key is the installation id, and the value is the token and `expires_at`. A token is reused until 5 minutes before `expires_at`. Two concurrent misses share one request through an `asyncio.Lock` for each installation id.
- **No token goes to Redis, the database, a log or the browser.** A sentinel test plants a token and scans the log output and every answer of the GitHub routes.
- `drop_installation(installation_id)` clears the entry. The uninstall, the suspend and a 401 from GitHub call it.

### 6.3 One token reader for two modes

`connections/github.py` stays the one reader (IN-7). The row gains `provider_meta.auth`, `"app"` or `"token"`. A row with no `auth` is a token row, so the rows of today keep working.

| Mode | `credentials_encrypted` | `token_or_none(org)` returns |
|---|---|---|
| `app` | Empty. The App holds no customer secret | An installation token from §6.2 for `provider_meta.installation_id` |
| `token` | The pasted token, as today | The stored token, as today |

Most callers keep their code. `refs.py` reads through `token_or_none` and needs no change. Three callers change in GH-3, because an installation token is not a user token:

- `_test_github` (`routes/integrations.py:1617`) calls `GET /user`, which GitHub refuses for an installation token. In `app` mode it calls `GET /installation/repositories?per_page=1` instead.
- `_github_connected` (`routes/integrations.py:1610`) would make a token on each status read. In `app` mode it reads the row's `status` and makes no token.
- `_org_github_token` (`routes/agent.py:1381`) fetches an agent `config.json` from a repository. That read needs Contents (read), and §13.2 asks for none. In `app` mode it returns an empty token, so a public repository still works and a private one does not. An org that needs a private agent repository keeps the Advanced token (§7.6). The owner chose this on 2026-10-18 (G-9).

**A private agent repository in `app` mode (fix round 1, item 3).** A silent failure is not acceptable here. Before the fix, the fetch got 404 and the registration made a `maf` agent with no integrations. Now three rules hold:

- **The answer is clear.** In `app` mode with no token, a `config.json` fetch that gets 403 or 404 asks `GET /repos/{owner}/{repo}` with no token, through the one GitHub door. A public repository with no `config.json` keeps the `maf` default. Any other answer is 422: "This repository is private. Add an Advanced GitHub token in Integrations to read it." `GET /agent/config` and `POST /agent` both use the rule.
- **"Configured" is for each use.** The `github` row of `GET /integrations/status` carries `github_coverage`. `pull_requests` is true for a live App row or a token row. With `?agent=`, it also carries `auth` and `agent_repo`, and `configured` is `agent_repo`. Only a `token` row reads a private repository. So the agent wizard keeps its GitHub step open for an App org, with a note.
- **A token replaces the App.** The org has one `github` row (G-6), so the two modes cannot both hold it. §7.6 says how the admin confirms first.

**The kill switch (fix round 1, F5).** `github_app_enabled` off stops every App-mode reader. `installation_of` answers `None` for every row, and `installation_token` and `app_jwt` raise `GitHubAppNotConfigured`, a cached token too. So `token_or_none`, the refs reads, the checks reads, the poll and the connection test make no token. A `token` row does not pass through these checks, so it keeps working.

The `app` row also holds `installation_id`, `account_login`, `account_type`, `repository_selection`, `app_slug` and `connected_by` in `provider_meta`. None of them is a secret.

## 7. The connect flow

### 7.1 Start

`POST /integrations/github/app/start`, behind `feature:integrations` and `integrations:manage`, as every writer of IN-0. The flag `github_app_enabled` off gives 404.

1. The gateway signs a state with `sign_state(purpose="github_install", org=current_tenant(), member=<caller>)`. The builder reads `_RESERVED_PURPOSES` (`signing.py:224`) first, and keeps `github_install` apart from each reserved purpose.
2. It writes the pending connect `SET key("github", "connect", <member>) <nonce> EX 600` through the tenant Redis wrapper (R5c). A new start replaces a pending connect (fix round 1, F4). With `NX`, a confirm that the admin cancelled left its record, and the next Connect read `expired`. The state of the older start then fails the nonce check of §7.3 step 3.
3. It answers `{"url": "https://github.com/apps/<slug>/installations/new?state=<state>"}`. The page opens the URL in the same tab.

### 7.2 GitHub

The admin picks an account and the repositories. With `request_oauth_on_install` true, GitHub then asks the admin to authorize the App. Then it sends the browser to the callback URL with `code`, `installation_id` and `setup_action` (§4.2 facts 5 and 6). A member who is not an owner of the GitHub organization sends an install request. GitHub then sends `setup_action=request` and no installation.

### 7.3 The callback

The callback URL is `https://app.metorite.com/api/integrations/github/callback`. Staging uses `https://dev.metorite.com/api/integrations/github/callback`. The BFF route relays it with the member session to `GET /integrations/github/app/callback`, as the Zoho callback of `routes/crm/connection.py` does.

The gateway does these checks in this order. Each refusal redirects to `/integrations?view=connections&github=<code>`, and the page shows a fixed text for each code.

1. **The session.** The caller holds `integrations:manage` in the org of the session. Else `forbidden`.
2. **The pending connect.** `GETDEL key("github", "connect", <member>)` returns a nonce. Else `expired`. So the callback must follow a connect that this member started in this org within 10 minutes. It trusts nothing in the query string.
3. **The state, when GitHub sends it.** A state that GitHub sends must verify with purpose `github_install`, the same org and the same member. Its nonce must also equal the nonce of the pending connect, and `hmac.compare_digest` compares the two. An empty `state=` counts as sent. Else `bad_state`. So the state of an older start, or of another tab, fails (fix round 1, item 9). The BFF forwards an empty state and drops an absent one.

   **A callback with no `state`** is the fallback (U1). The pending connect of step 2 alone binds it, and the gateway logs `github.connect.state_absent`. GH-8 counts that line on staging to measure U1. A later change may make the state required.
4. **A request.** `setup_action=request` shows "Your GitHub organization owner must approve the install. Press Connect again after they approve." Nothing is written.
5. **The code.** The gateway exchanges `code` at `https://github.com/login/oauth/access_token` with the App client id and secret. It holds the user token for this request only, and never writes it.
6. **The installation belongs to the user.** `GET /user/installations` with the user token, through every page, must list `installation_id` (§4.2 fact 5). Else `not_yours`. This is the check that the docs require, because a query string can name any installation.
7. **The user administers the account.** `GET /user/installations` also lists an installation where the user can only read one repository. So the gateway checks the account of the installation:
   - For an account of type `User`, `account.id` must equal the id from `GET /user`.
   - For an `Organization`, `GET /user/memberships/orgs/{account.login}` must give `role` `admin`. That read needs the organization permission Members (read) (§13.2).

   Else `not_admin`. So a collaborator with read access cannot take a company's installation.
8. **The installation facts.** `GET /app/installations/{id}` with the App JWT gives the account, the type, `repository_selection` and `suspended_at`. A suspended installation gives `suspended`.
9. **The callback writes nothing.** It stores `{installation_id, account_login}` in the pending key for 10 more minutes, and redirects to `/integrations?view=connections&github=confirm`. The page asks "Connect <account_login> to <org>?".
10. **The confirm.** `POST /integrations/github/app/confirm` reads and deletes the pending key, and runs §7.4. The BFF refuses a `Sec-Fetch-Site` that is not `same-origin`. So a forged callback URL that a member opens cannot bind an installation without a click of that member.
11. **One installation, one org.** The write of §7.4 fails on the unique index of §9.3 when another org holds the installation. Then `other_org`, with the text "This GitHub account is connected to another Metorite organization." Else the page reads `connected`.

### 7.4 The row

The confirm POST of §7.3 step 10 makes one upsert of the org's `github` row through `connections/store.py`, under `connections/github.lock(db, org)`. It sets `status = 'connected'`, `provider_meta` of §6.3 with `auth = "app"`, and `credentials_encrypted` to NULL. If the store cannot write a row with no blob, GH-3 adds that write to `store.py`, so the AST fence of the store still holds. **A token row becomes an App row.** The confirm dialog in the console says so before the admin leaves for GitHub: "This replaces the token that is connected now."

One `acb_audit` row records `github.app.connected`, with the org, the member, the installation id and the account login.

### 7.5 Uninstall, suspend and disconnect

| Event | Effect |
|---|---|
| `installation` `deleted` | The row goes to `status = 'disconnected'`, `last_error = 'uninstalled'`. `drop_installation` runs. The references keep their last state |
| `installation` `suspend` | `status = 'needs_reconnect'`, `last_error = 'suspended'`. `drop_installation` runs |
| `installation` `unsuspend` | `status = 'connected'`, `last_error` NULL |
| `installation` `new_permissions_accepted` | `provider_meta.permissions` refreshes from `GET /app/installations/{id}` |
| `installation_repositories` `added` or `removed` | `provider_meta.repository_selection` refreshes. No repository list is stored |
| `DELETE /integrations/github` on an `app` row | The row goes to `disconnected`, and `drop_installation` runs. Metorite does not uninstall the App at GitHub. The answer and the console give the link to the installation page at GitHub, so the admin can uninstall it there |

A webhook for a disconnected installation finds no org (§8.3), so it is dropped.

### 7.6 The Advanced token (G-5)

- **One writer.** `PUT /integrations/github/token` with `{token}`, behind `integrations:manage`. It calls `GET https://api.github.com/user` with the token, and refuses a token that GitHub refuses with 422. Then it stores the token through `save_token` (the device-token writer, renamed) as a `token` row. It does not call `_refuse_provider_key_without_byok`.
- **A token replaces a live App only after a confirm** (fix round 1, item 3). The org has one `github` row, so a token row takes the place of the App row. Pull requests and checks then stop coming by webhook, and a merge no longer moves a task. So the route answers 409 `app_connected` while a live App row exists, unless the body holds `replace_app: true`. It asks GitHub nothing first. The card shows "Replace the Metorite App with this token?" and sends `replace_app` only after the admin confirms.
- **The chat's setup token** (fix round 1, item 8). A chat agent writes `<<<SETUP:github:GITHUB_TOKEN=…>>>`. Before the fix, the executor and the chat BFF sent it to configure, which answers 422 for it. The agent still said that it saved the key. Now `orchestrator/setup_tokens.py` and `lib/setupTokens.ts` send `GITHUB_TOKEN` to this one writer, with its gates. The other keys go to configure in their own request, so a GitHub refusal does not drop them. A refusal adds one line to the answer, with no token in it. The chat sends no `replace_app`, so model output never replaces a live App. `GITHUB_TOKEN` stays in the guide's `env_vars`, because the chat setup still asks for it.
- **The other writers go.** configure and `PUT /keys` answer 422 for `GITHUB_TOKEN` with "Connect GitHub in Integrations." `POST …/device/start` and `POST …/device/poll` answer 410, as `connect-cli` does.
- **The UI.** The GitHub card in Integrations shows Connect GitHub. A fold named "Advanced: use a personal access token" holds the token field. `GitHubDeviceConnect.tsx` goes, and its four importers lose it. `GitHubAccountBadge.tsx` stops calling `connect-cli`.
- **The customer client id goes.** Nothing reads the org row `github:client_id` after GH-7. The deployment `github_client_id` setting stays, unread, until a later clean-up removes it.

## 8. The webhook receiver

### 8.1 The route and the mount

- `POST /webhooks/github` in `gateway/routes/github/webhook.py`, included in `main.py` beside the other webhook routers.
- `/webhooks/github` joins `PUBLIC_ROUTES` (`main.py:696`), in the same PR as the router (the comment at :732 refuses an entry with no router).
- **The public path.** `https://api.metorite.com/webhooks/github` and `https://api.dev.metorite.com/webhooks/github`. Caddy already proxies both, so `deploy/` does not change.
- The flag `github_app_enabled` off gives 404, with no signature check.

### 8.2 The order of checks

1. **The size cap.** The route reads the body as a stream, and stops at `GITHUB_WEBHOOK_MAX_BYTES` (5 MiB). A larger body gets 413 and no HMAC work. GitHub caps at 25 MB (§4.2 fact 8). A pull request payload is far smaller, and the poll repairs a dropped one.
2. **The signature.** `X-Hub-Signature-256` must be `sha256=` and 64 hex characters. The route computes the HMAC-SHA256 of the raw bytes with `github_app_webhook_secret`, and compares with `hmac.compare_digest`. A missing, malformed or wrong signature gets 401. The route logs `github.webhook.bad_signature` with the delivery id and no body.
3. **The headers.** `X-GitHub-Event` must be in the handled set of §8.4, or the route answers 202 and logs `github.webhook.ignored`. `X-GitHub-Delivery` must be a GUID, else 400.
4. **The installation.** The route parses the JSON and reads `installation.id`. It calls the function of §9.3. No org gives 202 and `github.webhook.unknown_installation`, and nothing is written. An `installation` `created` event for a new installation lands here too, before the callback maps it. That is expected.
5. **The tenant.** The route binds the org with `bind_tenant` and opens `tenant_session(org)`. Every write after this point is bound (R5).
6. **The delivery key.** `SET key("github", "delivery", <guid>) 1 NX EX 259200` through the tenant Redis wrapper. 259,200 seconds is 3 days, the redelivery window (§4.2 fact 9). A key that exists gives 200 `{"duplicate": true}`. If the handler fails, the route deletes the key and answers 500, so a redelivery runs again. A Redis outage logs `github.webhook.dedupe_unavailable` and goes on, because every handler is idempotent (§8.6).
7. **The handler.** It runs inside the 10-second answer budget, with a budget of 5 seconds for its own database work. A GitHub API read never runs before the answer. It goes to a background task (§8.5).
8. **The answer.** 200 `{"ok": true}`.

### 8.3 The org of an installation

`github_installation_org(p_installation_id bigint)` is a `SECURITY DEFINER` function in the shape of `230_wa_accounts_cloud_number_owner.sql`. It returns the `organization_id` of one `integration_connections` row. The row has `provider = 'github'` and `status <> 'disconnected'`, and its `provider_meta->>'installation_id'` equals the argument as text. It copies the rules of 230:

- `SET search_path = pg_catalog, pg_temp`, and qualified names.
- A WARNING when the owner cannot see through RLS.
- EXECUTE for `acb_app` only. The file revokes it from `PUBLIC`, `anon`, `authenticated` and `service_role`.
- plpgsql, not `LANGUAGE sql`, for the reason that 230 gives.

The route calls it only after the HMAC passes, and never returns the org id to GitHub.

### 8.4 The handlers

| Event | Actions | Effect |
|---|---|---|
| `installation` | `created` (mapped only), `deleted`, `suspend`, `unsuspend`, `new_permissions_accepted` | §7.5 |
| `installation_repositories` | `added`, `removed` | §7.5 |
| `pull_request` | `opened`, `reopened`, `edited`, `synchronize`, `ready_for_review`, `converted_to_draft`, `closed` | Update every reference of the org to `owner/repo#N`: `title`, `state` by `refs.github_state`, `head_sha`, `head_ref`, `pr_updated_at`, `merged_at`, `fetched_at`. A row is written only when `pull_request.updated_at` is newer than its `pr_updated_at` (§8.6). Then the auto-link of §10. `synchronize` sets `checks_state = 'pending'` for the new head and schedules a checks read. `closed` with `merged` true runs §12 |
| `check_suite` | `completed` | Schedule a checks read for `head_sha` |
| `check_run` | `created`, `completed` | Schedule a checks read for `head_sha` |
| `status` | every state | Schedule a checks read for `sha` |
| any other | | 202, ignored |

A checks event finds its references by `(organization_id, provider, head_sha)`. A head that no reference holds does nothing.

### 8.5 The checks read

`github_sync.read_checks(org, repo, sha)` runs as a background task after the answer. It holds no database session while it calls GitHub, as TR-1 does.

1. `GET /repos/{o}/{r}/commits/{sha}/check-runs?per_page=100`, at most 3 pages.
2. `GET /repos/{o}/{r}/commits/{sha}/status`.
3. It folds the two into one state:
   - `failing` when any check run concluded `failure`, `timed_out`, `cancelled`, `action_required` or `startup_failure`, or the combined status is `failure` or `error`.
   - else `pending` when any check run is `queued`, `in_progress`, `waiting`, `requested` or `pending`, or the combined status is `pending` with at least one context.
   - else `passing` when at least one check run or status exists.
   - else `none`.
4. `checks_summary` is `{"passed": n, "failed": n, "pending": n, "skipped": n, "total": n}`. `neutral` and `skipped` count as skipped.
5. It writes `checks_state`, `checks_summary` and `checks_fetched_at` in a new `tenant_session(org)`, only for rows whose `head_sha` still equals `sha`. So a late read for an old head never overwrites the new head.

A per-process limit allows 4 checks reads at once. A read that fails keeps the row, logs `github.checks.read_failed`, and leaves the repair to §11.

**One leader reads each head** (fix round 1, item 7). A workflow of 30 jobs sends about 60 `check_run` events, and one read per event made about 120 calls. Now `github_sync.read_checks_joined` reads for each `(org, head_sha)`:

1. `SET key("github", "checks", <sha>, "lead") NX` through the tenant Redis wrapper picks one leader, in every process.
2. A job that does not lead sets the `again` mark and stops.
3. The leader reads at once. Then it waits `JOIN_WINDOW_S` (20 seconds), and reads again only while a mark is there. This trailing read catches the last `completed`. It stops after 15 trailing reads.
4. A webhook job runs the wait and the trailing reads in a separate task. The answer does not wait for the window.

So a burst gives two reads, and a long run gives one read in each window. A Redis outage reads once, with no join. §11 repairs a mark that comes in the last moment after the leader stops, because it reads a pending head after 5 minutes.

### 8.6 Idempotency and order

GitHub can send events out of order, and can send one twice. Each handler holds these rules, and a test holds each rule:

- A `pull_request` write needs `pull_request.updated_at >= pr_updated_at`, or `pr_updated_at IS NULL`. An older event writes nothing. **The same second writes too** (fix round 1, item 5). GitHub gives whole seconds, so an open and a merge in one second carry one stamp, and `>` dropped the close. A replay writes the same facts again, so `>=` is safe. One tie-break holds: at the same second, a row in `merged` or `closed` never goes back to `open` or `draft`.
- **One write path.** `github_sync.apply_pull_request` writes the facts, and runs §12 when the write puts the reference in `merged` or `closed`. The webhook, the poll (§11) and the lazy read of `GET /refs` (§11) all call it.
- A checks write needs `head_sha = sha`.
- The auto-link writes through the unique key of `pm_task_refs`, so a second link is a no-op (`refs.insert_ref`).
- The merge rule skips a task that is already in a closing category (§12.3 step 1).

So a replay that passes the delivery key, for example during a Redis outage, changes nothing.

## 9. The data

### 9.1 `pm_task_refs` gains eight columns

One expand-only migration (R6). Take the number at build time and check it again at merge (R1). The highest on 2026-10-18 was 281.

| Column | Type | Holds |
|---|---|---|
| `head_sha` | `text NULL`, CHECK `^[0-9a-f]{40}$` | The head commit of the pull request |
| `head_ref` | `text NULL`, at most 255 characters | The head branch name |
| `checks_state` | `text NULL`, CHECK in `pending`, `passing`, `failing`, `none` | The folded CI state (§8.5). NULL means never read |
| `checks_summary` | `jsonb NULL` | The counts of §8.5 step 4 |
| `checks_fetched_at` | `timestamptz NULL` | When §8.5 last wrote |
| `pr_updated_at` | `timestamptz NULL` | GitHub's `updated_at`, for the order rule of §8.6 |
| `merged_at` | `timestamptz NULL` | GitHub's `merged_at`. The summary strip of EA-13e counts it |
| `unreadable_since` | `timestamptz NULL` | When an App read of the pull request was first refused (fix round 1, item 6). A good read clears it. §12.3 step 4 reads it |

Every column is nullable with no default, so the code that runs now meets the new schema (R6).

### 9.2 The CHECK of `via`, and two indexes

- **`via` gains `github`.** The migration drops `pm_task_refs_via_check` and adds it again as `^(ui|chat|text|github|mcp:[a-z0-9:_.-]{1,60})$`, `NOT VALID`, then validates it in a guarded block (migration 148 is the reference). The builder reads the real constraint name first.
- `CREATE INDEX IF NOT EXISTS pm_task_refs_provider_ref ON pm_task_refs (organization_id, provider, ref)`. The pull request handler reads by it.
- `CREATE INDEX IF NOT EXISTS pm_task_refs_head_sha ON pm_task_refs (organization_id, head_sha) WHERE head_sha IS NOT NULL`. The checks handler reads by it.

### 9.3 One installation, one org

- A partial unique index: `CREATE UNIQUE INDEX IF NOT EXISTS integration_connections_github_installation ON integration_connections ((provider_meta->>'installation_id')) WHERE provider = 'github' AND provider_meta ? 'installation_id' AND status <> 'disconnected'`. A pre-check names the count of duplicates and refuses the migration, as 230 does. An index holds across tenants under RLS, so org B cannot take org A's installation.
- The function `github_installation_org` of §8.3.
- No new table. So `gen_tenant_migration.py` and `EXEMPT` do not change. The builder still runs `tests/unit/test_tenant_coverage.py`.

## 10. Auto-link (G-3)

### 10.1 What links

On `pull_request` `opened`, `reopened`, `edited`, `synchronize` and `ready_for_review`, `github_autolink.candidates(body, head_ref, app_host)` returns task ids from two places:

1. **The description.** A Metorite task URL: `https://<app host>/projects?…task=<uuid>…`, where `<app host>` is the host of `app_public_url`. Staging accepts `dev.metorite.com` and production accepts `app.metorite.com`, so a staging URL never links in production.
2. **The head branch.** A full UUID, or the short form of §10.2. It must be a whole segment. A segment ends at `/`, `_`, `-` or an end of the name.

### 10.2 The short form `mt-<8 hex>`

**The form.** `mt-` and the first 8 hex characters of the task UUID, lower case. A branch reads `feat/mt-1a2b3c4d-login-fix`.

**Why not `#N` or `PRJ-N`.** `task_number` is unique only inside a root project (`146_projects.sql:233`). `pm_projects.task_prefix` has no unique constraint. So `#12` and `RND-12` can each name two tasks in one org. EA3-39 item 2 refuses `PRJ-N` for the same reason.

**Why 8 hex.** The UUID is unique in the product, and the prefix needs no new column or index. 8 hex characters are 32 bits. In an org of 100,000 tasks, a given prefix matches a second task with a chance of about 1 in 43,000. The rule below turns that collision into no link, never a wrong link.

**The match.** The read is `id >= '<8 hex>-0000-0000-0000-000000000000' AND id <= '<8 hex>-ffff-ffff-ffff-ffffffffffff'` in the bound org, so the primary key serves it. It links only when exactly one task matches.

### 10.3 The rules

- **The org is the installation's org.** The read runs in the bound tenant. A task of another org never matches.
- **Live tasks only.** An archived task, or a task that does not exist, links nothing.
- **No personal tasks.** A task in a personal project (D52) links nothing, because it is private to one member.
- **No outsider links.** A pull request links only when its head repository is its base repository, or its `author_association` is `OWNER`, `MEMBER` or `COLLABORATOR`. So a pull request from a fork of a public repository cannot write a reference into a task.
- **At most 5 tasks for each pull request.** More candidates link the first 5 and log `github.autolink.capped`.
- **One insert.** Each link calls `refs.insert_ref` with `kind = 'pull_request'`, `provider = 'github'`, `via = 'github'` and `created_by = 'system:github'`. It writes the `link` activity that the route writes.
- **Never removed by an edit.** A later edit that drops the URL keeps the link, as TR-5 does.
- **A link that a person removed stays removed** (fix round 1, item 2). The auto-link runs on each `synchronize` and `edited`. Before the fix, a wrong link that a person deleted came back with the next push, and the merge then closed the unrelated task. The tombstone is the `link` timeline row that `DELETE …/refs/{id}` writes, with `ref_action = removed`. The auto-link skips a (task, pull request) pair whose newest `link` row is a removal, and logs `github.autolink.removed_by_person`. A person who adds the link again by hand writes a newer `added` row, and that clears it. The rule needs no migration and no second store.
- **One grammar.** `tests/fixtures/github_autolink_cases.json` holds the cases. The gateway test reads it, and EA-13f reads it for the short form that the agent puts in a branch name.

## 11. The reconcile poll

`gateway/github_reconcile_loop.py`, started from the lifespan in the shape of `my_day_loop.py`. The flag `github_app_enabled` gates the start inside the start function, as `main.py:40-50` says a loop must.

- **The org list.** The loop copies `my_day_loop._organization_ids` (`my_day_loop.py:207`), an unbound read of the RLS-exempt `organization` table. GH-5 registers the new site in `tests/unit/test_db_engine_seam.py`, as `my_day_loop.py` is (R5b).
- **The tick is 10 minutes.** For each org with a connected `app` row, `SET key("github", "reconcile", <tick>) NX EX 900` picks one process.
- **What it reads.** Two sets of references:
  - an open or draft pull request whose `fetched_at` is older than 30 minutes.
  - a reference with `checks_state = 'pending'` whose `checks_fetched_at` is older than 5 minutes.
- **How many.** At most 50 references for each org in a tick, the oldest first.
- **How.** The pull request read and the checks read of `github_sync`, outside any database session, then one write session.
- **A merge it finds.** A reference that moves to `merged` in the poll runs §12, as the webhook does. So a missed `closed` delivery still closes an agent's task.
- **The budget.** 50 references need at most about 150 requests for each org in a tick, which is 900 an hour. That is well under 5,000 an hour (§4.2 fact 13). A `403` or `429` with a rate-limit header stops the org's tick and logs `github.reconcile.rate_limited`.
- **The lazy read stays.** `GET /refs?refresh=true` keeps its 15-minute read for issues and for `token` rows. For an `app` row, it reads the checks too.
- **The lazy read of an `app` row uses the one write path** (fix round 1, item 1). Before the fix, the lazy read wrote `merged` or `closed` with its own UPDATE and ran no §12. The poll reads only `open` and `draft` rows, so the row was never read again. A `closed` delivery lost in a restart then left the agent's task open, with no log line. Now an `app` org's lazy read of a pull request goes through `github_sync.read_pull_request` and `apply_pull_request` (§8.6), and the events go out after the commit. A refused read marks the row (§12.3 step 4). Its issues keep the TR-1 read.

## 12. Merge-to-Done (G-4)

### 12.1 The trigger

A `pull_request` `closed` event, merged or not, or a poll read or a lazy read (§11) that writes `merged` or `closed`. A close without a merge can still finish a task whose other pull request merged earlier (§12.3 step 4). `github_merge.on_merged(db, org, ref, merged_at, merged_by)` runs in the bound tenant, in the transaction of the write. `github_sync.apply_pull_request` is the one caller (§8.6). A refused read past the bound of step 4 calls `github_merge.on_unblocked` for the tasks of that reference.

### 12.2 The agent-worked rule

A task is **agent-worked** at time `T` when either of these rows exists, with `created_at < T`:

1. **A run.** A `pm_agent_runs` row with `task_id` = the task, in any status.
2. **An act through MCP.** Any `pm_activities` row with `task_id` = the task and `meta->>'via'` that matches `^mcp:`. A claim writes an `assignment` row (`tasks.py:1484`), and `record_activity` stamps the `via`. A claim of a task that is already assigned and started writes no row. So the rule also counts the agent's comment, status move and link on the task.

`agent_worked.AGENT_WORKED_SQL` is the one copy of this predicate. `agent_worked.reason(db, task_id, T)` returns `"agent_run"` with the run id, `"mcp_act"` with the activity id, or None. The timeline row names the reason (§12.4), so a person can audit each move. Both come from one WHERE text for each half (`_RUN_WHERE` and `_MCP_WHERE`, fix round 1, F3). A test holds that the boolean and the reason agree on every case, the edge of `T` included.

### 12.3 The steps

For each task with a `pull_request` reference to the merged pull request:

1. **Skip a closed or held task.** Skip when the task is archived, or its status category is `completed`, `cancelled` or `triage`.
2. **Skip a task that people run.** Skip when `agent_worked.reason` is None.
3. **Skip a task that a person took back.** Look at the newest `status_change` row of the task. Skip when all three are true:
   - its `meta.via` is absent, or does not start with `mcp:` and is not `github`. A move through the in-product chat carries `chat:projects-assistant` (`skill_projects/client.py:52`), and it is a person's move.
   - its `created_by` does not start with `system:`.
   - it is newer than the newest agent row of §12.2.

   A person who moved the task after the agent keeps the decision.
4. **Wait for the other pull requests.** Skip when a `pull_request` reference of the task has `state` `open` or `draft`. Skip when no reference of the task has `state` `merged`. The last merge or close of the set runs the rule again. A skip for an open reference logs `github.merge.blocked_by`, with the blocking references.

   **An unreadable reference does not block for ever** (fix round 1, item 6). A pull request of the token era, or one in a repository outside the installation, can stay `open` because the App cannot read it. Then the rule waited with no sign. The rule now is this:
   - An App read that GitHub refuses (404, 410, 403 with no rate limit, or 301) stamps `unreadable_since` with the time of the FIRST refusal. A good read clears it. A 5xx is not a refusal.
   - A reference whose `unreadable_since` is older than `UNREADABLE_AFTER` (1 hour) does not block. Two refused reads of the poll, 30 minutes apart, cross the bound.
   - When a refused read finds the bound crossed, `github_merge.on_unblocked` runs the rule again for each task of the reference. It uses the task's newest `merged` pull request, so the timeline names the merge that finished the task.
5. **Find the done lane.** `load_default_status(db, status_owner_id(db, task.project_id), COMPLETED_CATEGORY)`, the lane rule of `complete_for_member`. A set with no done lane skips the task and logs `github.merge.no_done_lane`.
6. **Move.** Set the `ACTOR_VIA` context value (`core.py:81`) to `github`, then call `apply_status_transition(db, task, lane.id, created_by="system:github", automation=True)`. The recurrence successor comes from that helper. Then write one `system` activity row with the reason (§12.4). The subtasks stay open, as EA3-32 says.
7. **After the commit.** Emit `pm.task.updated` and `pm.task.status_changed`, as `claim_task` does. A blocked task that waits on this one sees the change through the events that a member's move sends. ⚠️ **A merge can start a workflow.** The Workflows catalog offers `pm.task.status_changed` as a trigger (`routes/workflows/catalog.py`). So a published workflow on that trigger runs after a merge-to-Done, as it runs after a member's move.

An open agent run on the task stays open. The agent finishes it. Nothing moves a task to `cancelled`.

### 12.4 The timeline row

`apply_status_transition` does not change. Its `status_change` row gets `meta.via = github` from the context value and `meta.automation = true`. The merge rule then writes one `system` row through `record_activity`, with this meta:

```json
{"via": "github", "automation": true,
 "github": {"repo": "owner/repo", "number": 12, "merged_by": "login", "delivery": "<guid>"},
 "reason": "agent_run", "agent_run_id": "<uuid>"}
```

`reason` is `mcp_act` with `activity_id` for the other branch of §12.2. EA-13b draws the row as "GitHub moved this to Done after owner/repo#12 merged".

## 13. Security and tenancy

### 13.1 Threats

| Threat | Control | Fence |
|---|---|---|
| A forged webhook | HMAC-SHA256 over the raw bytes, `hmac.compare_digest`, before any parse or read | GH-4 W1, W2 |
| A replayed webhook | The delivery key of §8.2 step 6, and idempotent handlers (§8.6) | GH-4 W5, W6 |
| A large body | The stream cap of 5 MiB, before the HMAC | GH-4 W3 |
| A webhook for another org's installation | The org comes from the installation id through the definer function, never from the body. An unknown installation writes nothing | GH-4 W4, W7 |
| A spoofed `installation_id` at the callback | The pending connect, the user-token check of `GET /user/installations`, the admin check, and the unique index | GH-3 C2 to C5, C11 |
| One installation in two orgs | The partial unique index of §9.3 | GH-2 M3 |
| A login request forgery at the callback | The pending connect for this member, the state when present, and the same-origin confirm | GH-3 C2, C3, C12 |
| A fork links itself to a task | The `author_association` rule of §10.3 | GH-4 L5 |
| A leaked private key | The key lives in a root-owned file on the box, read by the gateway user only. It never enters a customer row, a log or an answer | GH-1 A4 |
| A leaked installation token | Memory only, one hour, never logged | GH-1 A3 |
| A person's task closed by a machine | The agent-worked rule, the take-back rule and the closed-task skip of §12.3 | GH-6 D1 to D9 |
| A wrong link that comes back and closes an unrelated task | The tombstone rule of §10.3 | `test_item2_*` |
| The production key on the staging box | The `host` pin on each GitHub secrets entry (§15) | `test_secrets_drop.py` pin tests |
| Model output that swaps the org's GitHub connection | The chat sends no `replace_app`, so a live App is never replaced from a chat (§7.6) | `test_github_chat_setup.py` |
| The state of an older start at the callback | The nonce compare of §7.3 step 3 | `test_item9_*` |

### 13.2 The permissions of the App

| Permission | Level | Why |
|---|---|---|
| Metadata | read | Asked in any case (U4). It names the repositories of the installation |
| Pull requests | read | `pull_request` events and `GET …/pulls/{n}` |
| Checks | read | `check_suite` and `check_run` events and `GET …/check-runs` |
| Commit statuses | read | `status` events and `GET …/status` |
| Members (organization) | read | `GET /user/memberships/orgs/{org}` for the admin check of §7.3 step 7. Only a user token uses it |
| Contents | none | Not needed (G-9). `GET …/pulls/{n}` works with Pull requests (read) (§4.2 fact 12) |

No permission is `write`. GitHub lists `GET /user/memberships/orgs/{org}` under Members (read), for user tokens only (<https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps>). The App subscribes to `pull_request`, `check_suite`, `check_run` and `status`. The two installation events need no subscription.

### 13.3 R5, item by item

- **(a)** No new table. The new columns sit on `pm_task_refs`, which is under FORCE RLS.
- **(b)** No new connection site. The webhook uses `tenant_session(org)` after the bind. The definer function runs on the same seam.
- **(c)** Every new Redis key goes through the tenant wrapper: the pending connect, the delivery key and the reconcile guard.
- **(d)** The session idiom of the seam, as `routes/whatsapp/transport/webhook.py` uses it after its own lookup.
- **(e)** The org of a webhook comes from the signed body's installation id and the database. The org of the callback comes from the session. No request input names a tenant.

### 13.4 The operator plane (D56)

The App id, the slug, the client id, the client secret, the webhook secret and the private key path are operator settings. They live in `/opt/acb/app/.env` and in a key file on the box. `scripts/secrets.sh` pushes them (§15). The console shows "set" or "not set" only.

## 14. Two Apps: staging and production

An App has one webhook URL (§4.2 fact 14). Staging (`dev.metorite.com`) and production (`app.metorite.com`) each need their own, so each gets its own App.

| | Staging | Production |
|---|---|---|
| App name | `Metorite (staging)` | `Metorite` |
| Homepage | `https://dev.metorite.com` | `https://metorite.com` |
| Webhook URL | `https://api.dev.metorite.com/webhooks/github` | `https://api.metorite.com/webhooks/github` |
| Callback URL | `https://dev.metorite.com/api/integrations/github/callback` | `https://app.metorite.com/api/integrations/github/callback` |
| Public | No. Only the Hathi Labs organization installs it (G-7, G-8) | Yes. Any customer installs it (G-7) |
| Box | `srv1747539` (`metorite-dev`) | `srv1914284` (`metorite`) |
| Secret file | `github-app-staging.env` and `github-app-staging.pem` | `github-app.env` and `github-app.pem` |

The code reads the same settings in both places, and only the values differ.

Each box holds eight: `GITHUB_APP_ENABLED`, `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY_PATH` and `APP_PUBLIC_URL`. `APP_PUBLIC_URL` is `https://app.metorite.com` in production and `https://dev.metorite.com` on staging. `acb-gateway` and `acb-agent-edge` both read it (EA-13f). It names the origin, so §10.1 accepts only its own environment's task URLs. The hard-coded `APP_URL` of `routes/whatsapp_channel/views.py:43` stays out of scope.

## 15. The owner runbook: register the two Apps (GH-0)

⚠️ **Run the script on your own PC.** It writes a private key into `%USERPROFILE%\.metorite\secrets`, and the key must not reach the repo.

The script is `scripts/github_app_manifest.py`, from GH-1. It holds the manifest of §13.2 and §14, so you type no permission.

1. Open a terminal in the repo. Run `uv run python scripts/github_app_manifest.py --env staging --owner Hathi-Labs`. The Hathi Labs organization owns both Apps (G-8).
2. The script opens a page in your browser. Click **Create GitHub App for <organization>**.
3. GitHub returns to the script on `http://127.0.0.1:<port>`. The script exchanges the code and writes `github-app-staging.env` and `github-app-staging.pem`. It prints the App name and the file names, and no value.
4. Run the same command with `--env production`, and click Create again.
5. Tell the agent "the GitHub Apps are registered". The agent does the rest. It pushes the key file of each App first, and then its env file:
   - `METORITE_SSH_HOST=metorite-dev scripts/secrets.sh push github-app-staging-key`
   - `METORITE_SSH_HOST=metorite-dev scripts/secrets.sh push github-app-staging`
   - `scripts/secrets.sh push github-app-key`
   - `scripts/secrets.sh push github-app`
   - It checks that the gateway restarted, and that `/webhooks/github` answers 401 to an unsigned POST.

   ⚠️ **The manifest pins each entry to its box** (fix round 1, item 4). The two env entries write the same app env file. Before the fix, `push --all` sent all four entries to the one `METORITE_SSH_HOST`, production last, so the production private key could land on the staging box. Now each GitHub entry carries a `host`: `metorite-dev` for the staging pair and `metorite` for the production pair. `push <name>` to another host fails before any probe. `push --all` skips an entry with a pin to another host, and names it.

**What the script does.**

1. It serves one local page with a hidden form. The form POSTs the manifest to `https://github.com/organizations/<org>/settings/apps/new?state=<random>` (§4.2 fact 1).
2. It checks the returned `state`, and calls `POST /app-manifests/{code}/conversions` within the hour.
3. It writes `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET` and `GITHUB_APP_WEBHOOK_SECRET` to the `.env` file. It also writes the three fixed settings of §14: `GITHUB_APP_ENABLED=false`, `GITHUB_APP_PRIVATE_KEY_PATH=/opt/acb/github-app.pem` and `APP_PUBLIC_URL`. So the push lands all eight with the flag off. It writes the `pem` to the key file.
4. It refuses to overwrite a file that exists. An empty template of `scripts/secrets.sh init` holds no value, so the script replaces it.
5. It stops the local server after one exchange or after 15 minutes.

**The manifest.**

- `redirect_url` is the script's local URL, and `callback_urls` is the callback of §14.
- `request_oauth_on_install` is true, so there is no `setup_url` (§4.2 fact 6).
- `hook_attributes` is `{"url": <webhook URL>, "active": true}`.
- `default_permissions` and `default_events` are §13.2, and `public` is §14.

**If it goes wrong.** A code older than one hour fails the exchange. Delete the half-made App in GitHub's App settings, and run the command again.

## 16. Slices

Each slice is **AGENT-SAFE** unless it says **OWNER-GATE**. Every slice ships dark behind `github_app_enabled` (default false), except GH-0, GH-7 and GH-8.

⚠️ **GH-7 goes live at the merge, not dark.** It retires the device flow and moves the pasted token to `PUT /integrations/github/token`. Those are changes to the paths that work today, so no flag can hold them back. The device flow needs the customer's own OAuth App client id, which G-5 retires, and BYOK blocks it. A dark copy would keep a path that the owner chose to remove.

Three things stay safe. An org's token row keeps working with no action. `GITHUB_TOKEN` from the chat goes to the new writer (§7.6). The Advanced fold works with the App flag off, and the App half of the card shows "not turned on" until GH-8.

| # | Slice | Files | Done when | Gate |
|---|---|---|---|---|
| GH-0 | **Register the two Apps, and push their secrets.** The owner runs §15 steps 1 to 4. The agent runs step 5. Needs GH-1 merged | None in the repo | Both Apps exist with the permissions of §13.2. Both boxes hold the eight settings of §14, with the flag off. An unsigned POST to each `/webhooks/github` answers 401 with the flag on, or 404 with it off | 🔴 OWNER-GATE: a registration at a third party under the owner's GitHub identity. The push is AGENT-SAFE under the grants `secrets` and `env-write` |
| GH-1 | **The App auth core and the registration script.** §6.1, §6.2, §15. The settings `github_app_enabled` (default false), `github_app_slug`, `github_app_client_id`, `github_app_client_secret`, `github_app_webhook_secret` and `app_public_url` (default `https://app.metorite.com`), beside the two that exist. Two manifest entries in `deploy/secrets/manifest.json` | `connections/github_app.py`, `packages/acb_common/acb_common/settings.py`, `scripts/github_app_manifest.py`, `deploy/secrets/manifest.json`, `tests/unit/test_github_app_auth.py` | **A1** the JWT decodes with the public key, with `iat` 60 seconds back, `exp` at most 600 seconds ahead and `iss` the client id. **A2** a second call within the window makes no second token request, and a call 5 minutes before `expires_at` makes one. Two concurrent misses make one request. **A3** a sentinel token appears in no log line and no answer. **A4** a missing key file raises `GitHubAppNotConfigured`, and the message holds no path content. **A5** the script builds the manifest of §13.2 and §14 for each `--env`, refuses a wrong `state`, and refuses to overwrite a file. Each item red first under a named mutation | AGENT-SAFE. The manifest edit needs the grant `deploy-write` |
| GH-2 | **The migration.** §9.1 to §9.3 | `infra/postgres/<next>_github_app.sql`, `tests/unit/test_github_migration_r8.py` | R8 on a real database, as `acb_app`, on the promoted catalog. **M1** the eight columns exist and are nullable. **M2** `via = 'github'` inserts, and `via = 'gitlab'` fails. **M3** a second live `github` row with the same installation id in another org fails, and a `disconnected` one passes. **M4** `github_installation_org` returns the org with no tenant bound, returns no row for an unknown or disconnected id, and `anon` cannot execute it. **M5** the migration runs twice with no error. **M6** a pre-check with a planted duplicate refuses with the count | AGENT-SAFE |
| GH-3 | **The connect flow and the one reader.** §6.3, §7.1 to §7.5. The GitHub card's Connect GitHub button and the confirm dialog | `routes/github/connect.py`, `connections/github.py`, `routes/integrations.py` (`_test_github` and `_github_connected` only), `routes/agent.py` (`_org_github_token` only), the BFF routes `app/api/integrations/github/app/start/route.ts`, `app/api/integrations/github/app/confirm/route.ts` and `app/api/integrations/github/callback/route.ts` (in the shape of `api/integrations/mcp/oauth/callback/route.ts`), a new `components/GitHubAppConnect.tsx`, `tests/unit/test_github_connect_r8.py` | R8 with GitHub mocked at the HTTP layer. **C1** start answers the install URL with a state, and writes the pending key. **C2** a callback with no pending key gives `expired`, and writes nothing. **C3** a state of another member or org gives `bad_state`. **C4** an installation that `GET /user/installations` does not list gives `not_yours`. **C5** an installation held by org B gives `other_org` for org A. **C6** `setup_action=request` writes nothing. **C7** a good callback writes nothing, and the confirm POST writes one `app` row with no blob and one audit row. The user token appears nowhere. **C8** `token_or_none` returns an installation token for an `app` row and the stored token for a `token` row. **C9** a member without `integrations:manage` gets 403 at start and at the callback. **C11** a user who only reads a repository of the organization gets `not_admin`, and so does a `User` account of another id. **C12** a cross-site confirm POST gets 403, and a confirm with no pending key writes nothing. **C13** in `app` mode, `_test_github` calls `GET /installation/repositories`, `_github_connected` makes no token, and `_org_github_token` returns an empty token. Visual review of the card in light mode, at compact density and under a changed accent | AGENT-SAFE |
| GH-4 | **The webhook receiver and the auto-link.** §8.1 to §8.4, §8.6, §10 | `routes/github/webhook.py`, `routes/projects/github_autolink.py`, `gateway/main.py` (the router and `PUBLIC_ROUTES`), `tests/fixtures/github_autolink_cases.json`, `tests/unit/test_github_webhook_r8.py`, `tests/unit/test_github_autolink.py` | **W1** a wrong signature gets 401 and writes nothing. **W2** a compare with `==` fails a source fence that requires `hmac.compare_digest`. **W3** a 6 MiB body gets 413 with no HMAC call. **W4** an unknown installation gets 202 and writes nothing. **W5** a second delivery with one GUID writes nothing. **W6** an older `pull_request` event after a newer one writes nothing. **W7** an event for org A's installation never writes a row of org B (R8, two orgs). **W8** the flag off gives 404. **W9** a handler that raises answers 500 and deletes the delivery key, and a redelivery of the same GUID then runs. **L1** to **L6**: the cases file links a description URL, a full UUID branch and an `mt-` branch. It refuses a staging URL in production, an archived task, a prefix with two matches, a fork from an outsider, and the sixth candidate. `/webhooks/github` is in `PUBLIC_ROUTES`, and `test_default_deny_auth.py` stays green | AGENT-SAFE. Full review loop: it adds a public route |
| GH-5 | **The checks read and the reconcile poll.** §8.5, §11, and the checks fields in the refs route | `gateway/github_sync.py`, `gateway/github_reconcile_loop.py`, `routes/projects/refs.py`, `gateway/main.py` (the lifespan), `tests/unit/test_db_engine_seam.py`, `tests/unit/test_github_checks.py`, `tests/unit/test_github_reconcile_r8.py` | **K1** the fold of §8.5 passes a table of at least 15 cases. **K2** a read for an old head writes nothing. **K3** no database session is open during a GitHub call (the fence of EA12-4, R11). **K4** a poll tick runs in one process only, reads at most 50 references, and stops on a rate-limit answer. **K5** a poll that finds `merged` calls `on_merged` once. **K6** `GET /refs` returns `checks_state`, `checks_summary`, `head_ref` and `merged_at`. **K7** the org list site is registered in `test_db_engine_seam.py` | AGENT-SAFE |
| GH-6 | **Merge-to-Done.** §12. No change to `core.py` | `routes/projects/github_merge.py`, `routes/projects/agent_worked.py`, `tests/unit/test_github_merge_r8.py` | R8, each item red first under a named mutation. **D1** an agent-run task in review moves to the done lane of its own project, with `completed_at`. **D2** a task with no run moves when an MCP act exists: a claim, or only a comment through MCP. **D3** a task with no run and no MCP claim does not move. **D4** a person's later status move keeps the task. **D5** an open second pull request keeps the task, and its merge then moves it. **D5b** when the first pull request merged and the second closes without a merge, the close moves the task. A task whose only pull request closes without a merge does not move. **D6** a cancelled, done, triage or archived task does not move. **D7** a recurring task makes exactly one successor. **D8** the `status_change` row holds `created_by = system:github` and `meta.via = github`, and one `system` row holds the reason. **D9** a second delivery moves nothing. **D10** open subtasks stay open. **D11** `core.py` has no diff. **D12** a person's later move through the chat (`chat:projects-assistant`) keeps the task | AGENT-SAFE. Full review loop: a machine writes a member's task |
| GH-7 | **The Advanced token, and the device flow retires.** §7.6 | `routes/github/connect.py`, `routes/integrations.py`, `connections/github.py`, `components/GitHubDeviceConnect.tsx` (deleted), its four importers, the BFF routes `api/integrations/github/device/` and `connect-cli/` (deleted), a new BFF route `api/integrations/github/token/route.ts`, `components/GitHubAccountBadge.tsx`, `app/integrations/components/ApiKeysView.tsx`, `tests/unit/test_github_per_org_r8.py` | **T1** `PUT /integrations/github/token` stores a token that `GET /user` accepts, with BYOK off. **T2** a token that GitHub refuses gets 422. **T3** configure and `PUT /keys` with `GITHUB_TOKEN` get 422. **T4** device start and poll get 410. **T5** no source file reads `github:client_id` (a fence). **T6** `tsc` and vitest pass with `GitHubDeviceConnect` gone. Visual review of the card | AGENT-SAFE. It changes behaviour for an org that uses the device flow today: its token row keeps working |
| GH-8 | **Go live.** Staging first, then production at the daily release | `.env` on each box | On staging: install the staging App on a test repository, open a pull request with a task URL, see the reference, the checks and a merge-to-Done on an agent task. Record the delivery ids. Measure U1, U2 and U3 with a GitHub Actions run, and record them in §4.2. Then the same on production for Fracktal | The flag flip is AGENT-SAFE under the grant `enforcement-flip`, with the box and the flag named in the same message. 🔴 OWNER-GATE: the install on Fracktal's GitHub organization, because it acts as the owner at a third party |

**Order.**

- GH-1 and GH-2 run in parallel. GH-0 follows GH-1.
- GH-3 follows GH-1 and GH-2. GH-7 follows GH-3.
- GH-4 follows GH-2 and GH-3, because its tests use the fixtures of GH-3.
- GH-6 follows GH-4. GH-5 follows GH-6, because K5 calls `on_merged`.
- GH-8 comes last.

### 16.1 Build record

| # | Status | Fence |
|---|---|---|
| GH-0 | 🔴 Waits for the owner. The script and the four secrets entries are ready | — |
| GH-1 | 🔨 BUILT on branch `github-batch-a`, in review. 13 mutations, and 6 more in fix round 1, each red | `test_github_app_auth.py` |
| GH-2 | 🔨 BUILT on branch `github-batch-a`, in review. Migration 282, with an eighth column from fix round 1. 6 mutations, each red | `test_github_migration_r8.py` |
| GH-3 | 🔨 BUILT on branch `github-batch-a`, in review. 18 mutations, and 9 more in fix round 1, each red. Visual review done | `test_github_connect_r8.py`, `githubConnect.test.ts`, the BFF route tests |
| GH-4 | 🔨 BUILT on branch `github-batch-a`, in review. 17 mutations, and 2 more in fix round 1, each red | `test_github_webhook_r8.py`, `test_github_autolink.py` |
| GH-5 | 🔨 BUILT on branch `github-batch-a`, in review. 13 mutations, and 10 more in fix round 1, each red | `test_github_checks.py`, `test_github_reconcile_r8.py` |
| GH-6 | 🔨 BUILT on branch `github-batch-a`, in review. 10 mutations, and 7 more in fix round 1, each red. `core.py` has no diff | `test_github_merge_r8.py` |
| GH-7 | 🔨 BUILT on branch `github-batch-a`, in review. 9 mutations, and 8 more in fix round 1, each red. Visual review done. Goes live at the merge (§16) | `test_github_per_org_r8.py`, the token route test |
| GH-8 | 🔴 Waits for GH-0 and the merge | — |

**What the build chose, where the spec left room.**

- **Four secrets entries, not two.** One entry of `scripts/secrets.sh` holds one file. So each App has a key entry (`github-app-staging-key`, `github-app-key`) and an env entry. §15 step 5 names all four.
- **The script writes all eight settings.** The three fixed ones join the five from GitHub, so the push lands with the flag off.
- **The token route has its own router** (`connect.token_router`), because the App flag must not gate the Advanced path.
- **The checks read of the refs route runs in the background.** The list answers at once, and the next list shows the result (§11).
- **A pull request that the auto-link links takes its facts in the same event.** The link writes first, then the facts.
- **R8 runs on a real Redis when `GITHUB_R8_REDIS_URL` names one**, and on a fake under the real `TenantRedis` wrapper in CI.

**Fix round 1 (2026-10-18).** Three reviews found 4 P1, 5 P2 and 5 points of the verifier. Each fix has a test that goes red under a named mutation. The test modules list them. 42 new mutations, each red when run:

| # | Fix | Slice | Section |
|---|---|---|---|
| 1 | The lazy read of an `app` org uses the one write path, so a lost `closed` still runs §12 | GH-5, GH-6 | §8.6, §11 |
| 2 | A link that a person removed stays removed (the tombstone is the timeline row) | GH-4 | §10.3 |
| 3 | The App reads no private agent repo. A clear 422, "configured" for each use, and a token replaces the App only after a confirm | GH-3, GH-7 | §6.3, §7.6 |
| 4 | A `host` pin on each GitHub secrets entry | GH-1 | §15 |
| 5 | An equal `updated_at` writes, with a tie-break for a terminal row | GH-5 | §8.6 |
| 6 | An unreadable reference stops blocking after 1 hour, and the rule logs `blocked_by`. Migration 282 gains `unreadable_since` | GH-2, GH-6 | §9.1, §12.3 |
| 7 | One leader reads the checks of each head | GH-5 | §8.5 |
| 8 | A GitHub token from the chat reaches its one writer, and a refusal shows in the answer | GH-7 | §7.6 |
| 9 | The callback compares the state nonce with the pending connect | GH-3 | §7.3 |
| F1, F2 | Fences for the take-back time compare and the `:aw_before` bound | GH-6 | §12.2 |
| F3 | One WHERE text for the boolean and the reason | GH-6 | §12.2 |
| F4 | A new start replaces the pending connect | GH-3 | §7.1 |
| F5 | `github_app_enabled` is a full kill switch | GH-1, GH-3 | §6.3 |

## 17. Verification commands

First run `eval "$(bash scripts/dev_db.sh --export)"`, or use a private ladder database. Never run two R8 runs at once. A new R8 suite starts with `pytestmark = _DB_GATE`, or `deploy.yml` errors.

```
uv run pytest tests/unit/test_github_app_auth.py tests/unit/test_github_migration_r8.py tests/unit/test_github_connect_r8.py -v -rs
uv run pytest tests/unit/test_github_webhook_r8.py tests/unit/test_github_autolink.py tests/unit/test_github_checks.py tests/unit/test_github_reconcile_r8.py tests/unit/test_github_merge_r8.py -v -rs
uv run pytest tests/unit/test_github_per_org_r8.py tests/unit/test_task_refs_route.py tests/unit/test_task_refs_text_links.py tests/unit/test_crm_connection.py tests/unit/test_default_deny_auth.py tests/unit/test_tenant_coverage.py tests/unit/test_projects_actor_via.py tests/unit/test_launch_defang_kill_switches.py -v -rs
uv run ruff check apps/services/gateway/gateway/connections apps/services/gateway/gateway/routes/github apps/services/gateway/gateway/github_sync.py apps/services/gateway/gateway/github_reconcile_loop.py apps/services/gateway/gateway/routes/projects scripts/github_app_manifest.py
uv run mypy --ignore-missing-imports apps/services/gateway/gateway/connections/github_app.py apps/services/gateway/gateway/routes/github
cd workbench/control_plane && npx tsc --noEmit && npx vitest run src/app/integrations src/components
```

Six vitest failures are known and come from CRLF line ends only: `layoutBoundary` (two), `railSessions`, `emailLink` and `reportsLiveOverview` (two). Any other failure is real. With `-rs`, a skipped R8 test shows its reason, and a skip is not a pass.

## 18. The build batches

Two batches build in parallel. Each owns its files, so neither edits a file of the other.

**Batch A, the GitHub foundation (this spec): GH-1 to GH-7.** One branch and one pull request, with GH-0 as the owner act between GH-1 and GH-3.

| Batch A owns | |
|---|---|
| Gateway | `connections/github.py`, `connections/github_app.py`, `routes/github/` (new), `github_sync.py`, `github_reconcile_loop.py`, `routes/integrations.py`, `routes/projects/refs.py`, `routes/projects/github_autolink.py`, `routes/projects/github_merge.py`, `routes/projects/agent_worked.py`, `routes/agent.py` (`_org_github_token` only), `main.py`. Batch A does not edit `routes/projects/core.py` |
| Shared | `packages/acb_common/acb_common/settings.py`, `infra/postgres/<next>_github_app.sql`, `deploy/secrets/manifest.json`, `scripts/github_app_manifest.py` |
| UI | `app/integrations/`, `components/GitHubAppConnect.tsx`, `components/GitHubDeviceConnect.tsx`, `components/GitHubAccountBadge.tsx`, `app/agents/page.tsx`, `AddAgentWizard.tsx`, `IntegrationSetup.tsx`, the BFF route `app/api/integrations/github/` |
| Tests | `test_github_*.py`, `tests/fixtures/github_autolink_cases.json`, `test_github_per_org_r8.py`, `test_db_engine_seam.py` |

**Batch B, EA-13 (`external_agent_access.md` §12.2): EA-13a to EA-13f.** Batch B owns every file of EA-13 and none of the list above. §12.2 of that spec names them. Three EA-13 slices read Batch A's work, so they start after a Batch A merge:

- EA-13d (the PR chip) reads the columns of GH-2.
- EA-13e (the summary strip and the filter) imports `agent_worked.py` from GH-6 and reads `merged_at` from GH-2.
- EA-13f (the routine) reads `app_public_url` from GH-1 and the cases file from GH-4.

EA-13a, EA-13b and EA-13c read nothing of Batch A, so they start at once.

## 19. Engineering decisions (GH-E1 to GH-E10, 2026-10-18)

The orchestrator took these when it wrote the spec. None is a product or pricing call.

| # | Decision |
|---|---|
| GH-E1 | **One `github` row for each org, in two modes.** `provider_meta.auth` is `app` or `token`. An App connect replaces a token row. So every caller of `token_or_none` keeps its code |
| GH-E2 | **Ask for OAuth during the install,** and check the installation with `GET /user/installations`. The docs require that check (§4.2 fact 5). The gateway also checks that the user administers the account, and the admin confirms in Metorite before the write. Together they cost two more clicks |
| GH-E3 | **The pending connect in Redis binds the callback,** because the docs do not promise that `state` comes back (U1) |
| GH-E4 | **The webhook path is `/webhooks/github`,** beside `/webhooks/gmail` and `/webhooks/zoho` in `PUBLIC_ROUTES` |
| GH-E5 | **The org lookup is a `SECURITY DEFINER` function** in the shape of migration 230, not a new exempt table. `gen_tenant_migration.EXEMPT` is full |
| GH-E6 | **The delivery key lives in Redis for 3 days,** and every handler is idempotent. So a Redis outage costs a duplicate read, never a wrong write |
| GH-E7 | **The checks read runs after the answer,** because GitHub wants a 2XX within 10 seconds and a read can take longer |
| GH-E8 | **The short form is `mt-<8 hex>`** (§10.2) |
| GH-E9 | **The installation token lives in process memory only.** One request for each process an hour is cheap, and a token in Redis is a credential at rest |
| GH-E10 | **Disconnect does not uninstall at GitHub.** Metorite unlinks and gives the admin the link. An uninstall is the customer's act on their own GitHub account |

## 20. Owner questions

None is open. The owner answered the four questions of the first draft on 2026-10-18, and §3 records the answers as G-6 to G-9. The owner keeps the choice of a D-number for G-1 to G-9 in `work_plan.md` §3.

## 21. Shell manifest (R9)

R9 does not apply. This spec adds no app. The Integrations app's manifest is in `integrations_console.md` §5, and the card that EA-13 adds is in the Projects app.

## 22. Change log

- 2026-10-18 — Written from the owner decisions G-1 to G-5. The code anchors were read at `1625cca13`, and the GitHub facts on docs.github.com the same day.
- 2026-10-18 — The spec audit gave GO-NARROWED for both specs, and this round closes its 14 points. The callback checks that the user administers the account, and the admin confirms before the write. The delivery key goes on a handler error. A close without a merge can finish a task, and any MCP act counts as agent work. A chat move is a person's move. Three token callers change in `app` mode. The merge rule leaves `core.py` alone. GH-0 names eight settings. The order and two anchors are corrected.
- 2026-10-18 — The owner answered the four questions of §20 and took each proposed answer. §3 records them as G-6 to G-9. They are one installation for each org, a public production App, Hathi Labs as the owner of both Apps, and no Contents permission. §20 now holds no open question.
- 2026-10-18 — Batch A built GH-1 to GH-7 on branch `github-batch-a`, with migration 282. §16.1 records each slice and what the build chose. §15 step 5 now pushes four secrets entries, a key file and an env file for each App.
- 2026-10-18 — Fix round 1 closes 4 P1, 5 P2 and 5 verifier points (§16.1). The new rules are in §6.3, §7.1, §7.3, §7.6, §8.5, §8.6, §9.1, §10.3, §11, §12.1 to §12.3, §13.1 and §15. GH-7 goes live at the merge, and §16 says why. A merge can start a published workflow (§12.3 step 7).
