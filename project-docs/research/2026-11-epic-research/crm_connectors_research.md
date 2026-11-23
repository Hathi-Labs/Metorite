# CRM connector and mirror framework: research (2026-11-10)

Method: Part A read from origin/main (git show). Parts B and C come from web research by two sub-agents; each claim is tagged verified / claimed / memory with its URL. Part D is design judgement (memory plus Part A). Read-only: nothing written to the repo or to Metorite.

## BLUF
- **Reuse verdict:** the Zoho stack is already about 60% provider-neutral in shape. Keep the `CrmSource` protocol, the credential store, the run lock, the cursor-in-same-transaction rule, the mapping-plan tables, the executor, the preview and the AI mapping pipeline. Rewrite the Zoho mapping module, the write path (dead since CRM-Z5), the webhook (Phase-0, shared secret) and the `discover()` payload (raw Zoho keys).
- **Biggest gap:** the live engine is READ-ONLY. Two-way sync is old code that nothing reaches (`sync_zoho.py`), and its state sits in `zoho_*` columns. A generic framework needs a neutral write state machine.
- **Second gap:** Metorite's CRM today = four fixed entities plus `crm_module_records` (JSONB) plus `crm_field_defs`. There is no association table and no typed custom-object schema. Part D0 lists the minimum to add.

---
# PART A. Zoho integration in the repo (origin/main)

## A1. Modules and paths
| Area | Path |
|---|---|
| Connector seam (protocol, shapes, errors) | `apps/services/gateway/gateway/crm_sources/base.py` (12 ops), `registry.py` (slug to adapter and mapping module) |
| Zoho adapter | `crm_sources/zoho/{auth.py 385, client.py 1208, mapping.py 1593, links.py}` |
| Sync engine (read-only mirror) | `gateway/crm_sync/{engine,runs,scheduler,executor,field_rules,fill,holds,profiles,map_pipeline,catalogue,recover,questions,note_labels,note_vectors,digest}.py` |
| Credential store | `gateway/connections/store.py` (table `integration_connections`, migration 245) |
| Routes | `gateway/routes/crm/{sync,import_zoho,sync_zoho,cutover,mapping_plans,suggestions,field_rules,stage_metadata,settings,modules}.py` |
| OAuth callback and UI | `workbench/control_plane/src/app/api/crm/oauth/zoho/callback/route.ts` (forwards to gateway `/crm/oauth/zoho/callback`), `components/ZohoInCrm.tsx`, `app/crm/components/CutoverCard.tsx` |
| Legacy (Phase 0, CRM-Z11 deletes) | `ingestion/sources/zoho/{client,normaliser,webhook,writer}.py`, `scripts/zoho_sync.py`, `docs/ZOHO_OAUTH_SETUP.md` |
| Migrations | 144 crm, 145 zoho_sync, 241 tenancy, 243 settings, 244 mirror columns + `crm_field_defs`, 245 connections, 246 `crm_sync_runs`, 247 profiles/pipelines, 251 `crm_module_records`, 253 field_rules, 260 field studio, 269 holds, 277 `crm_mapping_plans`, 311 labels + cutover stamps, 341 change log |
| Specs | `project-docs/specs/crm_platform.md` (§3.4 cutover, §5 connector seam, §18.7 adaptive mirror, §18.9 mapping), `crm_app.md` (§7.1 old two-way, §7.3 cutover) |
| Tests and evals | `tests/unit/test_crm_sources_*`, `test_crm_zoho_*`, `test_crm_cutover_*_r8.py`, `test_crm_executor.py`, `evals/crm_mapping/` |

## A2. Mirrored-record data model
- Entity tables (`crm_companies`, `crm_contacts`, `crm_leads`, `crm_deals`, `crm_activities`) carry `ext_source`, `ext_id`, `ext_modified_at`, `custom JSONB`, with `UNIQUE (organization_id, ext_source, ext_id)` (migration 244; spec §5.3). Legacy `zoho_id` stays.
- Other modules: `crm_module_records(module, ext_source, ext_id, name, owner_email, fields JSONB, ext_url, ext_created_at, ext_modified_at)`, unique on `(org, ext_source, module, ext_id)` (251).
- Field definitions: `crm_field_defs(entity, key, label, type, options, source, provider_api_name, position, visible)` (244). The sync upsert skips rows with `source='metorite'`, so admin-owned fields survive (260).
- Cursors: one per entity. It advances in the SAME transaction as the page it covers. The unique key makes a replay safe. A SAVEPOINT wraps each record (engine.py docstring). The old table `crm_sync_cursors(module PK, last_pulled_at)` is from 145.
- Run state: `crm_sync_runs`, one `running` row per org (partial unique index, 246) = cross-worker lock. A heartbeat older than 15 min is taken over. The `error` column holds short codes, never provider text.
- Reconcile: a module that hides `Modified_Time` is read by offset, so the engine compares a full id list and deletes the missing local rows, at most every 24h per entity, and whenever the last good run is over 50 days (Zoho keeps deletes 60 days).
- Old two-way state (dead code): `zoho_dirty`, `zoho_synced_at`, retry trio (`attempts`, `error`, `next_attempt_at`) on each table, and `crm_zoho_tombstones` (a native delete writes it inside the delete transaction). Echo suppression: a pull-applied write sets neither dirty nor synced. Conflict policy: record-level last-writer-wins, counted and logged, never silent. Pull runs before push, so a lost conflict is corrected first.

## A3. Tokens and secrets
- `integration_connections` (general registry, org or member scope): `provider`, `status`, `provider_meta` (plain public facts: accounts_server, api_domain, location), `credentials_encrypted` (Fernet through `acb_llm.key_store`, key `ACB_MASTER_KEY`), scopes, `connected_by`, `last_error`. `store.py` is the ONLY module that names the blob column (AST fence `test_crm_connection.py`).
- The adapter takes its credential through the constructor and never reads settings, env or disk (fence `test_crm_sources_no_global_creds.py`). It may refresh mid-read. The caller re-reads `.credential` and stores it. `fresh_zoho_credential(org)` refreshes under a row lock and sets `needs_reconnect` on a dead refresh token. The engine never retries a dead token.
- Host allowlist on every URL (`UntrustedHost`, data-centre check in `auth.py`). Signed `state` on the OAuth callback (email seam `transport/signing.py`). Platform OAuth client (`OAuthClientConfig`) is global per provider; `client_secret` is kept out of repr.
- Gap: the store method is Zoho-named. Generalise to `fresh_credential(provider)`.

## A4. Schema discovery and mapping
- `discover()` (client.py:1079): `GET org`, `settings/modules`, `settings/layouts` and `settings/fields` for 8 modules, `settings/pipeline` per Deals layout. About 11 credits. Metadata only. Partial failures go in `failed`. `build_profile` (mapping.py:1466) turns it into a per-org source profile (247). `generic_modules()` ranks custom modules for the generic store.
- Fixed map: `mapping.map_record` (Zoho field to canonical column; leftovers to `custom` JSONB). `map_module_field_def` types fields of other modules.
- Admin rules: `crm_field_rules` (live) and `crm_mapping_plans` (versions: draft, approved, superseded; one approved per org; approve writes the snapshot into the live rules in one transaction). Rule kinds: `field`, `role` (which module plays an empty core slot), `target` (which field fills a column), `stage` (value to won/lost/open), catalogue.
- Executor (`crm_sync/executor.py`): pure functions, one path for any rule value. The sync, the backfill and `POST /crm/mapping/preview` (dry run on sampled records, writes nothing) all call it, so the preview equals what the sync writes. Transforms: copy, cast, value_map, normalize, coalesce, link. Unmapped counts are kept without values.

## A5. The AI field-mapping step (dark)
- `crm_sync/map_pipeline.py` (CRM-Q20-1): a fixed pipeline, not an agent. (1) shape and fill stats and pattern classes, 0 tokens; (2) find empty core slots; (3) code rules (module named like the slot, field 90% email-shaped, known stage names, never offer a 0%-fill field); (4) one `decide_features.ask` per entity with open questions, on the decide tier, candidates passed as named objects (`fields.f7`) so values are not copied; (5) re-ask under confidence `SURE` or inside `MARGIN` with `REASK_SAMPLES` sample values (PII-looking values go as a pattern class only); (6) each item runs through the executor on sampled records and keeps its unmapped count.
- Output is ONE draft plan, never a live rule. The admin approves. Features `crm.map_role`, `crm.map_target`, `crm.map_stage`, `crm.map_catalogue` each have off/shadow/on, gated by `CRM_DECIDE_ORGS`. Shadow compares with the provider's own marks (Zoho won/lost) as an answer key and logs agreement counts only.
- Cost controls: `TOKEN_CAP`, `RUN_REQUESTS`, per-org `DAILY_REQUESTS` counted in Redis before each call, schema hash in a cursor row so an unchanged key asks nothing, payer = the `connected_by` member, no field name or value in logs. Evals in `evals/crm_mapping/`.
- Gap: it fills Metorite's FIXED slots only. It does not propose new custom objects, associations or field types.

## A6. Rate limits, retry, webhook vs polling
- Retry (client.py:68, 258-286): `MAX_TRIES=5`; triggers HTTP 429, body code TOO_MANY_REQUESTS, 502/503/504; wait = finite `Retry-After`, else 1,2,4,8 s, capped 60 s; then `RateLimited`. A throttled token endpoint is handled apart.
- Budget: a run spends at most 10% of the edition's daily credits (500 floor); `BudgetExhausted` is raised before the call; credits remaining come from response headers; `daily_credits(edition, licences)` holds Zoho's table.
- Polling: the scheduler loop checks each org every 60 s (mode=mirror, connected, no run, interval elapsed; default 10 min). "Sync now" button. Flags `CRM_MIRROR` and kill switch `crm_org_allowed`.
- Webhooks: only the Phase-0 `ingestion/sources/zoho/webhook.py` (shared secret, fail-closed, enqueue only), slated for retirement. The new mirror is poll-only. Deletes come from `list_deleted` plus reconcile.

## A7. Zoho-specific vs generic
| Generic now | Zoho-specific |
|---|---|
| `CrmSource` protocol, `SourceRecord/Cursor/Page`, error taxonomy (NeedsReconnect, RateLimited, BudgetExhausted, SourceScopeError, SourceModuleUnavailable, UntrustedHost), "credential via constructor" fence | `auth.py` DataCentre; `client.py` URLs, `If-Modified-Since`, keyset/offset workaround for hidden Modified_Time, `ZohoTieTooWide`, credits |
| `registry.py`, `integration_connections`, `store.py` | `mapping.py` (1593 lines: field names, layouts, pipelines, `daily_credits`, `edition_of`, module scopes) |
| Engine order, lock, cursor-with-page transaction, SAVEPOINT per record, reconcile, budget, scheduler | Engine still names Zoho in constants and calls `fresh_zoho_credential` |
| `crm_mapping_plans`, `crm_field_rules`, executor, preview, `map_pipeline` structure | Prompt candidates; "Zoho marks won/lost" answer key; forecast category to stage type |
| `crm_field_defs`, `crm_module_records`, `ext_*` columns | `zoho_*` columns, `crm_zoho_tombstones`, `crm_sync_cursors`, writer.py, sync_zoho.py |
| Cutover (`cutover.py`) apart from the final-sync call | `/crm/oauth/zoho/*` routes, `ZohoInCrm.tsx` |

## A8. Cutover (Z10) and gaps
- Modes: Native, Mirror, none. Z10b: `POST /crm/cutover` = final sync, then mode flip Mirror to Native, records kept; the admin types `SWITCH`; dark behind `CRM_CUTOVER_ORGS` (ships empty). Z10a preview (counts, what stays in Zoho). Z10c: the lock follows the mode, not `ext_source`. Z10d: custom modules editable in Native. Z10g: labels keep the source names (migration 311 adds `crm_settings.labels`, `cutover_at`, `cutover_by`). `fold_cutover_overlay` keeps the board identical across the switch. It rewinds `crm_auto_lead_cursors.processed_watermark` because Mirror mode skips auto-lead mail. Fences: `test_crm_cutover_switch_r8.py`, `cutoverSwitch.test.ts`.
- Open tasks (all Backlog, project CRM; read via Metorite search): **#1827** the switch dialog says "The final sync did not finish" after ONE failed status read while the final sync still runs (poll tolerance bug). **#803** CRM-Z10e: copy Zoho attachments into Metorite before cutover (attachments stay in Zoho today; due 2026-12-15). **#806** CRM-Z10-run: ask Fracktal and Shaurrya Tele, then run the production cutover (owner-gated; due 2026-12-15).
- Other gaps: attachments and custom-module associations not mirrored; CRM-Z9 (opt-in write-back) not built, so two-way is off; no webhook ingestion; no federated query.

## A9. REUSE VERDICT
**Keep as the provider-neutral spine (rename only):** `CrmSource` protocol, shapes, error taxonomy and the credential fence; `registry.py`; `integration_connections` and the sealed-blob store (generalise `fresh_zoho_credential`); the engine's lock, cursor-with-page transaction, per-record SAVEPOINT, reconcile, budget and scheduler; `crm_sync_runs`; `crm_field_defs`, `crm_module_records`, `ext_*` columns; the mapping-plan stack (plans table, executor, preview, approve, holds, change log); the AI pipeline structure (code first, ask on ambiguity, re-ask with samples, draft never live, caps, shadow mode); the cutover machinery apart from the Zoho final-sync call.
**Rewrite per provider:** adapter (auth, paging, delta read, deletes, rate limiting), the mapping defaults (shrink to a field-type table plus rule defaults instead of hand-written per-entity functions), and the discovery payload (a neutral `Shape` dataclass instead of raw Zoho keys).
**New generic work:** write-back state machine (a `crm_sync_state` table instead of `zoho_dirty` columns), a webhook receiver with per-provider signature checks, associations and typed custom objects, a federated query mode, delete handling for providers with no deleted feed.

## Proposed connector interface (6 methods)
1. `connect(params) -> Credential`: consent URL, finish, refresh, revoke as one lifecycle object (the `credential` property stays).
2. `discover() -> Shape`: objects, fields (provider type, options, required, read-only), associations (labels, cardinality), pipelines and stages, users, edition and limits. Metadata only.
3. `read(object, cursor, mode=incremental|full|ids) -> Page[SourceRecord]`: records plus next cursor, `modified_at`, `archived` flag. The cursor kind (timestamp, token, offset) stays hidden.
4. `deleted(object, cursor) -> Page[ids]`, or `snapshot_ids(object)` for reconcile when the provider has no feed.
5. `write(object, op, records, idempotency_key) -> Result[ext_id, error per record]`: create, update, upsert, delete. Off by default, broker-gated.
6. `subscribe(callback_url, objects)` plus `verify_and_parse(request) -> [Change]`: webhook registration and signed-payload check, with polling as fallback.
Cross-cutting: `limits() -> Budget` and `deep_link()` as small helpers, not extra methods.

---


# PART B. Vendor APIs (web; tags in each line)
# Part B: CRM vendor API research (2026-11-10)
Tags: [verified URL] = read just now. [claimed URL] = search snippet / third party. [memory] = not checked.
Coverage note: many official doc pages returned 403/404 (Salesforce developer.salesforce.com, some HubSpot, Pipedrive readme). Those items are [memory]. Treat [memory] as unchecked.

## HubSpot
1. Introspect: Properties API per object (`/crm/v3/properties/{objectType}`), Schemas API (`/crm/v3/schemas`) for custom objects, Associations v4 labels. [memory]. Custom object schema creation needs Enterprise. [memory]
2. Incremental read: Search API with `hs_lastmodifieddate` BETWEEN filter (ms timestamps), paging via `after`, limit max 200, 10,000 results cap per query, archived records not returned. [verified https://developers.hubspot.com/docs/api-reference/search/guide]
3. Receive changes: webhooks, up to 1,000 subscriptions per app. [verified https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines]. Event types, retry, batching: [memory] (webhook guide URLs 404).
4. Write back: CRM Objects API create/update/batch. [memory]
5. Limits:
   - Private apps: Free/Starter 100 per 10s per app and 250,000/day; Pro 190/10s and 625,000/day; Enterprise 190/10s and 1,000,000/day. Daily limit shared across private apps in the account; resets midnight account time zone. [verified usage-guidelines URL above]
   - API Limit Increase: 250/10s, +1,000,000/day per increase, max two. [verified same]
   - Public OAuth (marketplace) apps: 110 per 10s per installing account on every tier; the increase does not raise it. [verified same]
   - Search API: 5 requests/s per account [verified search guide URL]; excluded from the 110/10s public cap [verified usage-guidelines]. Query max 3,000 chars; 5 filterGroups, 6 filters per group, 18 total. [verified search guide]
   - Error rate must stay under 5% of daily requests for marketplace certification. [verified usage-guidelines]
   - Bulk/export: CRM exports and batch endpoints (100 per batch read/write) [memory].
6. Marketplace listing:
   - OAuth must be sole auth method; at least 3 active unique installs (production accounts not affiliated with you, successful activity in last 30 days). [verified https://developers.hubspot.com/docs/apps/developer-platform/list-apps/listing-your-app/app-marketplace-listing-requirements]
   - Initial review within 10 business days; full review process up to 60 days from feedback; one app submitted at a time; HubSpot can unpublish anytime. [verified same]
   - Must use every requested scope; shared-data table must match scopes. [verified same]
   - Certification (separate, optional): 60 active installs since 2024-05-15, listed 6+ months. [claimed https://developers.hubspot.com/changelog/active-install-requirement-increase-for-app-certification]
7. Plan gating: API and OAuth work on Free and Starter (limits above). [verified usage-guidelines]. Custom objects Enterprise-only. [memory]. Scopes: granular `crm.objects.{x}.read/write`, `crm.schemas.*`. [memory]

## Salesforce
1. Introspect: REST `describeGlobal`, `sobjects/{X}/describe`; SOQL; Tooling API for custom metadata. [memory]
2. Incremental read: SOQL `WHERE SystemModstamp > :t` (also getUpdated/getDeleted; `IsDeleted` via queryAll); Bulk API 2.0 query jobs for large sets. [memory]
3. Receive changes: Change Data Capture over Pub/Sub API (gRPC) or CometD; Platform Events; 3-day event retention; CDC needs entity selection. [memory]. Official CDC page returned 403.
4. Write back: sObject REST create/update/upsert by external ID; composite and sObject Collections (200 records); Bulk API 2.0 ingest. [memory]
5. Limits:
   - Total API calls per rolling 24h = 100,000 base + per-license allotment (1,000 per license for Enterprise and API-enabled Professional) + add-ons. [claimed https://developer.salesforce.com/docs/platform/salesforce-app-limits-cheatsheet/guide/salesforce-app-limits-platform-api.html via search summary; page itself 403]
   - Developer Edition: 15,000 per 24h. [claimed same]
   - Unlimited/Performance per-license (5,000 vs 1,000): unverified, third-party only. [claimed https://www.taskrhino.ca/salesforce-alternatives/api-limits.md]
   - 25 concurrent long-running (20s+) requests; Bulk API 2.0 daily batch/record caps. [memory]
6. AppExchange listing: Partner Program signup, mandatory security review (fee, queue weeks), managed package for ISV. [memory]
7. Plan gating: Enterprise, Performance, Unlimited, Developer have API by default [claimed https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api/intro_rest_compatible_editions.htm via search]. Professional needs a paid API add-on; Additional API Calls does not enable access alone. [claimed https://help.salesforce.com/HTViewSolution?id=000005140 via search]. Starter/Pro Suite: [memory] limited. OAuth via Connected App; new creation of connected apps restricted in favour of External Client Apps (2025-26). [memory]

## Pipedrive
1. Introspect: `GET /api/v2/dealFields` (also personFields, organizationFields); POST/PATCH/DELETE manage custom fields; field types varchar, text, double, monetary, date, enum, set, user, org, people, phone, address etc. varchar 255 chars, text 65k. Default page 100, max 500, cursor paging. [verified https://developers.pipedrive.com/docs/api/v1/DealFields]
2. Incremental read: `GET /api/v2/deals` accepts `updated_since`/`updated_until` (RFC3339) on `update_time`; `status=deleted` shows deals deleted up to 30 days ago; `custom_fields` param (max 15 keys), `include_option_labels`. Limit default 100, max 500, cursor. [verified https://developers.pipedrive.com/docs/api/v1/Deals]
3. Receive changes: Webhooks v2 default since 2025-03-17; `event_action` create/change/delete/*; `event_object` deal, person, organization, lead, activity, note, pipeline, product, stage, user, *. Public non-redirecting URL, optional basic auth; sent only if the `user_id` user can access the object. Retry behavior not on that page. [verified https://developers.pipedrive.com/docs/api/v1/Webhooks]
4. Write back: POST/PATCH `/api/v2/deals` etc. Update costs 10 tokens. [verified https://pipedrive.readme.io/docs/core-api-concepts-rate-limiting]
5. Limits: daily token budget = 30,000 x plan multiplier x seats (Lite 1, Growth 2, Premium 5, Ultimate 7) plus top-ups, shared by the company, resets midnight server time. Costs: get single 2, list 20, update 10, delete 6, search 40. After budget: 429 until reset; abuse can bring Cloudflare 403. v2 cheaper than v1. [verified same]
6. Marketplace approval: Marketplace Manager submission, review by Pipedrive. Details [memory] (readme page 404).
7. Plan gating: API token and OAuth on all plans (budget scales with plan). [verified rate-limit page above for scaling]. API tokens vs OAuth: tokens are per-user; OAuth required for marketplace apps. [memory]

## Zoho CRM
1. Introspect: Fields Meta API (GET, scopes `ZohoCRM.settings.fields.READ`); per-field `api_create`/`api_update` operation flags; fields response excludes layout-specific mandatory/picklist data. Modules, Layouts, pipeline endpoints are separate Meta APIs. [verified https://www.zoho.com/crm/developer/docs/api/v8/field-meta.html for fields; modules/layouts/pipeline: memory]
2. Incremental read: `If-Modified-Since` header and Get Records `modified_time`; COQL queries; Bulk Read API: POST job with module, fields, criteria; result is CSV in ZIP (ICS for Events only); optional callback URL or poll job id. Max records per job: not on page. [verified https://www.zoho.com/crm/developer/docs/api/v8/bulk-read/overview.html]
3. Receive changes: Notifications API: per-module channels, create/update/delete, from v6 field-level `notification_condition`, per-channel expiry. Max expiry (1 day claimed elsewhere) not confirmed. [verified https://www.zoho.com/crm/developer/docs/api/v8/notifications/overview.html]
4. Write back: Records Insert/Update/Upsert (100 per call [memory]).
5. Limits (rolling 24h credits): Free 5,000; Standard 50,000 + 250/user (max 100,000); Professional 50,000 + 500/user (max 3,000,000); Enterprise 50,000 + 1,000/user (max 5,000,000); Ultimate 50,000 + 2,000/user (unlimited). Concurrency per org/app: Free 5, Standard 10, Pro 15, Enterprise 20, Ultimate 25. Sub-concurrency 10 for sorted/cvid reads, upserts over 10 records, Composite, etc. Bulk Read init costs 50 credits. [verified https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html]
6. Marketplace: Zoho Marketplace submission and review. [memory]
7. Plan gating: API on all editions including Free, scaled by credits (above). [verified api-limits URL]

## Freshsales (Freshworks CRM)
1. Introspect: `GET /api/settings/contacts/fields` (`?include=field_group`), `/api/settings/sales_accounts/fields`, custom modules `api/settings/:entity_type/forms`. Caller must be authorized. [verified https://developers.freshworks.com/crm/api/]
2. Incremental read: no documented incremental parameter. Views via `/api/contacts/filters` then `/api/contacts/view/{id}`; sort by `updated_at`; page default 25. Sort-by-updated_at approach is my inference, not documented. [verified same]
3. Receive changes: webhooks not on the API page; workflow-based webhooks exist in product. [memory]
4. Write back: REST create/update; bulk upsert up to 100 entities per request, max 10 concurrent bulk-upsert jobs per account, Job Status API. [verified same]
5. Limits: 1,000 requests per hour per account, 429 on excess. [verified same]
6. Marketplace: Freshworks Marketplace app review via developer platform. [memory]
7. Plan gating: API key in Profile Settings > API Settings, auth `Authorization: Token token=<key>`. Per-plan gating not stated on page. [verified same]. Some plans limit API/webhooks [memory].

## Odoo
1. Introspect: `fields_get` on any model, plus `ir.model` and `ir.model.fields` search_read. Page lists these sections. [verified https://www.odoo.com/documentation/18.0/developer/reference/external_api.html (first 100k chars of 134k read)]
2. Incremental read: `search_read` with domain `[('write_date','>',t)]`, limit/offset. Deletions not tracked by write_date. [memory]
3. Receive changes: no native webhook push in the External API; automated actions can call a webhook (v17+) [memory]. Not verified.
4. Write back: `create`, `write`, `unlink` via `execute_kw`. [verified same page headings]
5. Limits: no published numeric rate limit on that page; Odoo Online may throttle. [memory]
6. Marketplace: Odoo Apps store review by Odoo (module code review). [memory]
7. Plan gating: external API limited to Custom pricing plans, not One App Free or Standard. [verified same page]. API keys since v14 replace password; shown once. XML-RPC needs a password set on Odoo Online. [verified same]. JSON-RPC (`/jsonrpc`) and Odoo 19 JSON-2 API: [memory], not seen on the page read.

## Attio
1. Introspect: objects/attributes endpoints (`/v2/objects`, `/v2/objects/{x}/attributes`); only event types seen: `object-attribute.created/updated`. [memory for endpoints; events verified https://docs.attio.com/rest-api/endpoint-reference/webhooks/create-a-webhook]
2. Incremental read: record query with filter/sort; no documented updated-since cursor read. [memory]
3. Receive changes: `POST /v2/webhooks` (scope `webhook:read-write`, workspace token, HTTPS). Events: record.created/updated/deleted/merged, list-entry.*, note.*, task.*, comment.*, list.*, activity.*, workspace-member.created. Filters `$and`/`$or` with equals/not_equals. Status active/degraded/inactive; degraded 7 days becomes inactive. Signing secret returned once. Each URL+event+filter unique (409). Max count and retry policy not stated. [verified same URL]
4. Write back: create, update, assert (upsert by matching attribute) records. [memory]
5. Limits: reads 100/s, writes 25/s; list records and list entries also complexity-scored over a 10s sliding window across all apps/tokens; 429 with `Retry-After`. [verified https://docs.attio.com/rest-api/guides/rate-limiting]
6. Marketplace: Attio app directory approval [memory].
7. Plan gating: API on all plans [memory]; not checked.

## monday CRM
1. Introspect: GraphQL `boards`, `columns` (types/settings), `items_page`. monday CRM is boards-based. [memory]
2. Incremental read: `items_page` cursor with query_params rules; `activity_logs` on board; no updated-since filter documented here. [memory]
3. Receive changes: webhooks via `create_webhook` mutation (events like change_column_value, create_item); challenge handshake; app lifecycle webhooks. Webhook page read had only lifecycle events listed. [verified https://developer.monday.com/apps/docs/webhooks for lifecycle only; rest memory]
4. Write back: `create_item`, `change_multiple_column_values` mutations. [memory]
5. Limits: complexity budget; single query 5M points; app tokens 5M/min read and 5M/min write each; personal tokens 10M/min combined (1M for trial/NGO/free). Daily calls (resets midnight UTC): Free 200, Standard/Basic 1,000, Pro/Trial 10,000, Enterprise 25,000. Per minute queries: Enterprise 5,000, Pro 2,500, other 1,000. Concurrency: Enterprise 250, Pro 100, other 40. Items query max 100 items. [verified https://developer.monday.com/api-reference/docs/rate-limits]
6. Marketplace: monday app review and publishing process [memory].
7. Plan gating: API on all plans, daily caps scale by tier (above). [verified rate-limits URL]

## Not verified at all (priority to re-check)
- Salesforce: CDC, Pub/Sub, Bulk 2.0, security review, ECA/Connected App; official pages gave 403.
- HubSpot: webhook delivery/retry, custom object edition gating, Associations v4, bulk export.
- Pipedrive, Zoho, Freshsales, monday, Attio marketplace approval processes.
- Odoo JSON-RPC/JSON-2 and rate limits.

# PART C. Build vs buy (web)
# Part C: CRM connector platforms (research date 2026-11-10)

**Recommendation: hybrid. Own the mirror, mapping and schema model. Adopt Nango (self-hosted, Enterprise terms) or build thin own OAuth+extract per top-3 CRM. Do not adopt a hosted unified API (Merge, Apideck) for the mirror path.** Reasons: data must stay in Metorite, per-account cost is high, and unified models flatten custom objects.

Legend: [verified URL] = read just now. [claimed URL] = seen on a vendor page only as marketing. [memory] = my recall, unchecked.

## Comparison table

| Option | OAuth mgmt | Schema discovery | Model | Webhooks | License | Entry price / per-account | Vendor stores customer data? | Fit for in-house data |
|---|---|---|---|---|---|---|---|---|
| **Nango** | Yes (core product) [verified https://www.nango.dev/pricing] | Not stated; you write sync code in TS [memory] | Passthrough proxy + custom syncs you write; 1,200+ APIs [verified https://www.nango.dev/] | Yes, receives and routes API webhooks [verified https://www.nango.dev/pricing] | Elastic License 2.0 [verified https://raw.githubusercontent.com/NangoHQ/nango/master/LICENSE]. Whole-repo claim from that file; some dirs may differ [memory] | Free: 10 connections, 10 h compute, 10 GB. PAYG $50/mo incl. credits, $0.29/connection, $0.72/h compute, $0.50/GB egress. Enterprise: from $0.01/connection at scale [verified https://www.nango.dev/pricing] | Pricing page silent [verified https://www.nango.dev/pricing]. Syncs cache records in Nango DB, which you read by API [memory] | Good if self-hosted. Self-host and BYOC are Enterprise-tier on pricing page [verified https://www.nango.dev/pricing]. A free self-host tier exists with limits [memory] |
| **Airbyte** | Per-source auth in the connector; no multi-tenant embed OAuth product [memory] | Strong. Salesforce discovers standard and custom objects by user permissions [verified https://docs.airbyte.com/integrations/sources/salesforce]. Zoho builds a schema per module incl. custom [verified https://docs.airbyte.com/integrations/sources/zoho-crm] | Raw streams per object, not unified | No (batch, incremental on cursor) [memory] | ELv2 and MIT badges; split not stated on the page [verified https://github.com/airbytehq/airbyte]. Core free to self-host under ELv2 per FAQ [verified https://airbyte.com/pricing] | Cloud Standard from $20/mo (5 credits ~1 GB). Plus from $189/mo. Pro custom. Enterprise Flex = your VPC [verified https://airbyte.com/pricing]. Not per-account | Cloud: yes, it moves data (via its infra). Self-host: no | Good for bulk extract. Weak for per-tenant OAuth and two-way writes (it is read-oriented [memory]) |
| **Meltano / Singer** | None; you hold tokens | Singer catalog discovery [memory] | Raw streams | None | Meltano MIT [memory] | Free, OSS | No (you run it) | Many tap-hubspot variants exist; default is Stitch's singer-io, "Active (Stable)" [verified https://hub.meltano.com/extractors/tap-hubspot]. Quality is uneven and maintainer-dependent. Not recommended as a base |
| **Merge.dev** | Yes, embedded Link [memory] | Field Mapping, custom fields, Remote Field Classes [verified https://docs.merge.dev/supplemental-data/overview/] | Unified common model + remote data + authenticated passthrough [verified same] | Yes [memory] | Proprietary SaaS | Launch: 3 prod linked accounts free, then $650/mo for up to 10, then $65 per linked account. Pro/Enterprise by contract [verified https://www.merge.dev/pricing] | Yes. Merge syncs and caches data [memory]. Page lists log retention 3/30/90+ days only [verified https://www.merge.dev/pricing] | Poor. Data resides at vendor. EU region page exists, single-tenant and self-hosted "available for purchase" [verified https://www.merge.dev/pricing]. Cost scales per customer |
| **Apideck** | Yes, Vault [memory] | Custom field mapping on Scale+ [verified https://www.apideck.com/pricing] | Unified CRM API + Passthrough API on all plans [verified https://www.apideck.com/pricing] | Native + virtual [memory] | Proprietary | Launch $599/mo (25 consumers, 1 API category, no custom mapping). Scale $1,299/mo (100 consumers, 3 categories). Per consumer $23.96 at 25, $8.50 at 500 [verified https://www.apideck.com/pricing] | Pricing page lists "Database Sync" and 30+ day logs, so some retention [verified https://www.apideck.com/pricing]. Claims real-time pass-through in docs [claimed https://www.apideck.com] | Medium. Real-time proxy suits live reads, not a mirror. Custom fields need Scale |
| **Unified.to** | Yes [memory] | Passes through custom fields/objects per docs [memory] | Unified models, real-time. "Every request hits the source API live. No caching or sync jobs." "No customer records at rest." Credentials and metadata are encrypted at rest [verified https://www.unified.to/] | Native and virtual (polling scheduler) [verified https://www.unified.to/] | Proprietary | 2,175 sources, 313 CRM integrations; usage-based on API volume, not customers; 30-day trial; price not shown [verified https://www.unified.to/] | No customer records (vendor claim) [verified https://www.unified.to/ as claim, not audited] | Best hosted fit for "data stays in house". You still build the mirror and diffing. Vendor sees data in transit |
| **Paragon** | Yes, embedded Connect Portal | Dynamic field mapping is Enterprise [verified https://www.useparagon.com/pricing] | Workflow/passthrough, not unified | Yes [memory] | Proprietary. Self-host or forward-deploy on Enterprise [verified https://www.useparagon.com/pricing] | Free "Platform" start, no dollar figures; usage by tenants; sales call [verified https://www.useparagon.com/pricing] | Not stated on page | Built for workflow/triggers. Poor match for bulk mirror |
| **Pipedream Connect** | Yes: runs OAuth, stores and refreshes tokens, BYO OAuth clients [verified https://pipedream.com/connect] | None | Passthrough proxy to 3,000+ APIs | Via triggers [memory] | Proprietary | Dev free. Production price not on page [verified https://pipedream.com/connect] | Credentials only, encrypted [verified https://pipedream.com/connect] | OK as OAuth vault only. Vendor holds tokens |

## Connector quality notes (Airbyte)
- HubSpot: support level "Airbyte". Mix of incremental, client-side incremental and full-refresh streams. Custom properties slow syncs [verified https://docs.airbyte.com/integrations/sources/hubspot].
- Salesforce: standard and custom objects, dynamic discovery, incremental dedupe, deletes via isDeleted, graceful stop at daily rate limit [verified https://docs.airbyte.com/integrations/sources/salesforce].
- Pipedrive: certified since 5.0.1. 37 streams, only 10 incremental. Custom fields come as hash keys and need a join to `*_fields` [verified https://docs.airbyte.com/integrations/sources/pipedrive].
- Zoho CRM: "Marketplace" level, not Airbyte-maintained. Dynamic per-module schema incl. custom modules, all streams incremental [verified https://docs.airbyte.com/integrations/sources/zoho-crm].
- Catalog: 700+ connectors, 22.4k stars [verified https://github.com/airbytehq/airbyte].

## Notes and traps
- **ELv2 (Nango, Airbyte Core):** you may not offer the software to third parties as a hosted service giving access to substantial features. Metorite uses it as an internal component, not resold as an integration platform. Counsel should confirm. [verified https://raw.githubusercontent.com/NangoHQ/nango/master/LICENSE]
- **Nango self-host:** pricing page puts self-host and BYOC under Enterprise [verified https://www.nango.dev/pricing]. My fetch of the free self-host doc returned 404. Check the current limits before relying on a free tier. [memory]
- **Cost shape:** Merge is $65+ per linked account on Launch and Apideck $8.50 to $24 per consumer per month. At Metorite's flat INR 500/user seat that eats margin. Nango at $0.29 per connection, or self-hosted, does not.
- **Odoo and Freshsales:** coverage not verified in any vendor. Check per vendor before committing. Odoo has JSON-RPC/XML-RPC and no OAuth by default [memory].
- Unified models hide custom objects and fields. Merge needs Field Mapping, Apideck needs the Scale plan. Metorite's schema-discovery goal fits raw per-object access better.
- Write-back on unified APIs is limited to the common model plus passthrough [verified https://docs.merge.dev/supplemental-data/overview/ for Merge].

## Recommendation: own thin SDK vs adopt
**Hybrid.**
1. **Own:** the mirror tables (RLS, `organization_id`), the mapping layer, schema discovery storage, change detection and write-back rules. These are the product, and they must follow the R5/R6 tenancy rules.
2. **Adopt for OAuth + token refresh + extract:** self-hosted Nango (Enterprise terms, or confirm free self-host) to cut weeks of per-CRM OAuth work. Alternative: own thin connectors for HubSpot, Salesforce, Pipedrive and Zoho (4 OAuth flows, about 5 REST APIs). This is modest work for the top CRMs.
3. **Airbyte as bulk backfill only (optional):** use its Salesforce, HubSpot and Pipedrive streams as a reference for pagination, rate limits and custom-field handling. Running a second platform adds operational load. Skip it in v1.
4. **Skip:** Merge (data at vendor, per-account price), Meltano taps (uneven upkeep), Paragon (workflow tool).
5. **Fallback for the long tail:** Unified.to passthrough for CRMs you will not build (313 CRMs claimed). Gate it by a flag, since the vendor sees data in transit.

**Risks**
- ELv2 terms and Nango's Enterprise gating for self-host (verify before commit).
- Nango's cached sync records are a second copy of customer data. Confirm retention and encryption, and RLS cannot reach it, so tenant isolation must live in your own mirror.
- A seam rule applies: one DB seam, tokens through the secrets path. Nango holds OAuth tokens outside the Metorite vault, which needs a decision.
- Vendor API limits bind regardless of tool: HubSpot daily caps 250k to 1M per account [verified https://docs.airbyte.com/integrations/sources/hubspot], Salesforce daily limits.
- Unverified: Nango and Paragon data retention, Merge storage details, Odoo and Freshsales coverage.


---
# PART D. Shape fitting: foreign schemas onto a flexible object model
Source: engineering judgement and memory, plus Part A. Not web-verified unless a URL appears in Part B.

## D0. Target model Metorite needs
Today: four fixed entities, `crm_module_records.fields` JSONB, `crm_field_defs`. For a general mirror add (expand-only R6, tenant-scoped with FORCE RLS R5):
- `crm_objects(org, key, label, ext_api_name, kind core|custom)`. Core slots stay as overlays.
- `crm_field_defs` gains `data_type`, `is_array`, `read_only`, `calculated`, `enum_values (ext_value, label)`, `ext_type` (raw provider type), `ref_object`.
- `crm_association_defs(from, to, label, inverse_label, cardinality)` and `crm_associations(from_id, to_id, label, ext_source, ext_id)`.
- `crm_sync_state(object, record_id, ext_id, base_hash, remote_hash, state, attempts, error, next_attempt_at)` replaces per-table `zoho_dirty`. A neutral tombstone table replaces `crm_zoho_tombstones`.
- `extra` JSON keeps unmapped fields with their provider type. Never drop a field.

## D1. Field type mapping
| Source concept | Target type | Notes |
|---|---|---|
| text, email, phone, URL | text plus `format` hint | normalise phone to E.164 in a separate column, keep the raw value |
| long text, rich text, HTML | long_text | sanitise HTML; cap size (about 64 KB); overflow to blob; never log |
| integer, decimal | number (precision, scale) | HubSpot returns numbers as strings; SF `double` for money |
| currency | money = (amount, currency code) | keep rate and converted amount as read-only |
| date | date | no timezone shift |
| datetime | timestamptz | assume UTC unless the provider documents a user zone; keep raw string in extra when ambiguous |
| boolean | boolean | tri-state if null is allowed |
| picklist | enum | map by stable id or internal value, not label |
| multi-picklist | multi_enum (array) | HubSpot and SF join with `;`, so split |
| lookup, reference | reference(object, local id) | resolve after both sides import; keep pending refs |
| user, owner | member reference by email, else a `source_user` row | never auto-create members |
| file, image | attachment ref | out of v1 (task #803 is the Zoho case) |
| formula, rollup, autonumber, system timestamps | read_only, `calculated=true` | mirror, never push back |
| unknown | `extra` plus `ext_type` | show as "unmapped" in the UI |

## D2. Picklists, associations, pipelines, owners
- Picklist: store `(ext_value, label)`, map by ext value. A source rename breaks nothing. An unseen value adds an option flagged "new from source" instead of failing. Inactive options are hidden, not deleted.
- Associations: typed edges with labels (HubSpot v4 labels, SF lookup vs junction object, Pipedrive person-org, Zoho lookup plus related lists). Read cardinality from metadata. N:N always uses an edge table. Keep a "primary" flag on the edge. Import edges after nodes and retry unresolved ones.
- Pipelines and stages: `pipeline(ext_id, name)` and `stage(ext_id, name, order, probability, kind open|won|lost)`. Probability from the source (HubSpot, Pipedrive, SF DefaultProbability, Zoho) else a code or AI suggestion. Won/lost from provider flags first (SF IsWon/IsClosed), AI only as fallback (as the repo does). A deal's stage must belong to its pipeline.
- Owners: match by case-folded email. No match keeps the name and shows "Not in Metorite" (the repo behaviour). Teams and territories go to extra.

## D3. Lifecycle and hygiene
- Deleted/archived: map soft-delete (`IsDeleted`, HubSpot `archived`, Pipedrive `status=deleted`) to `archived_at`. Hard delete only through reconcile or a confirmed cutover. Recycle windows differ (Zoho 60 days per repo comment, Pipedrive 30 days verified in Part B), so reconcile must run inside the window.
- Timezone and currency: store UTC, keep the source zone as metadata, keep the original currency, convert only for display with a dated rate.
- Large text and attachments: truncate with a pointer; attachments only on an explicit scope; hash for dedupe.
- ID round-trip: unique `(ext_source, ext_id)` per org (exists). Store the id a push returns. Keep the provider's modified time and deep link. Use `ext_source` = connection id, not provider slug, so two HubSpot portals never collide.
- Dedupe on import: key on `(ext_source, ext_id)` only. Never merge by email or name at import. Offer a separate human-confirmed duplicates report on normalised email, domain and phone.

## D4. Two-way conflict policy
- Per-field source of truth in the approved plan: `source_wins` (mirror default), `metorite_wins` (after cutover), `last_writer_wins` (record level, as the old engine did), `manual` (queue).
- Detect with a three-way compare: `base_hash` (last synced) vs local vs remote. Merge at field level when different fields changed. Conflict only on the same field. The old engine did record-level LWW only.
- Echo suppression: a pull-applied write does not mark dirty (exists). Push carries an idempotency key. Ignore webhook events our own app caused.
- Never push read-only or calculated fields. Pre-check destination required fields in a dry run. Keep the retry trio with a give-up threshold so one bad row cannot starve the queue (the old design learned this).

## D5. Schema drift
- Re-run `discover()` on a schedule and on a schema event (Attio `object-attribute` webhook is verified in Part B). Hash per object (the repo's `map_pipeline` already keeps a schema hash per key).
- Added field: land in `extra`, mark "new, unmapped", ask AI for that field only. Removed field: keep the column, mark `retired`, stop reading. Type change: block that field, alert the admin, never coerce silently. Label rename: update the label, keep the key from the provider API name. Picklist change: see D2.
- Drift creates a new DRAFT plan version. The approved plan stays live until an admin approves (the table already has the states).

## D6. How an LLM proposes a mapping safely
1. Deterministic first: type table, exact names, provider flags. The LLM handles only the ambiguous remainder (the repo does this).
2. Inputs: names, labels, types, fill rates, pattern classes, a few masked samples. No raw PII, pattern class only (repo rule).
3. Output is JSON validated against a schema. The target must exist and the type must agree (`types_agree`). Drop invalid proposals, do not repair them.
4. Propose, never apply. Write a DRAFT. The UI shows each rule with confidence, reason and before/after sample rows from the same executor.
5. Dry run on N records. Count unmapped and rejected values per rule. Flag a rule with a high unmapped rate.
6. Human confirm per rule or per plan. Approval snapshots a version. Import runs only against the approved version.
7. Undo: stamp each record with `import_run_id` and each plan with a version. "Undo import" removes unedited rows of that run and restores the prior plan. Rows edited in Metorite since are listed, not deleted. The plan table and the change log (migration 341) exist. `import_run_id` is new.
8. Cost caps, one decision per object (not per row), cache by schema hash (all exist).
9. A push-direction rule needs a second explicit approval.

## D7. Federated vs mirror vs migrate
| Mode | What it does | Right when | Cost and risk |
|---|---|---|---|
| Federated live query | Metorite calls the source API per question and keeps no copy | A trial or proof; a strict no-copy policy; the source stays the system of record; tiny data; a source with strong search (HubSpot search, SOQL) | Slow; spends API budget per question (HubSpot search 5 rps and Pipedrive search token cost 40, both verified in Part B); no cross-object joins or history; source outage means Metorite outage; weak for pipeline analytics |
| Mirror | Copy, then keep fresh by incremental poll or webhook; local copy is read-only | The default. AI over the data, search, joins, insights. The customer keeps its CRM for reps | Storage; freshness lag; API budget; deletes; schema drift. Two-way adds conflicts |
| Migrate (cutover) | Mirror, then make Metorite the system of record and freeze or retire the source | The customer leaves the CRM (the Fracktal and Shaurrya Tele path) | Highest risk. Needs a final sync, count verification, associations and attachments complete, owners mapped, a pause on source writes, and a plan for integrations that pointed at the old CRM |
Order of use: federated for "try now" and for sampling during discovery; mirror as the standard; migrate as an explicit admin-typed switch (as Z10b). All three use the same connector, shape and mapping plan.

# Recommendation (C): own thin SDK for the mirror and mapping; optionally self-hosted Nango for OAuth and extract; Airbyte only as a reference; skip Merge, Paragon, Meltano; Unified.to passthrough as a flagged long-tail fallback.
