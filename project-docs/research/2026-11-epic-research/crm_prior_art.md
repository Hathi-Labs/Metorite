# CRM prior art for Metorite (research date 2026-11-10)

## Evidence key and honesty note
- **verified** = I read it on the vendor page or repo in this session (page date = fetch date 2026-11-10).
- **claimed** = third-party or vendor-marketing blog found by search (cited, not independently checked).
- **memory** = from my training knowledge (cutoff Jun 2026). Treat as a hypothesis. Re-check before you build on it.
- Limits: Pipedrive pricing page returned 403. Zoho pricing page showed no INR figures. HubSpot, Twenty, Salesforce data are partly verified. Most vendor sections (data models, "10 features users pay for") are **memory** and say so.

## 0. Verified facts (this session)
| Fact | Status | Source |
|---|---|---|
| HubSpot Sales Hub: Free (2 users), Starter $7/seat/mo, Pro $90/seat/mo (+$1,500 one-time onboarding), Enterprise $150/seat/mo (+$3,500 onboarding). Credits 500 / 3,000 / 5,000 per month, extra $0.010 each | verified | hubspot.com/pricing/sales |
| HubSpot limits: pipelines 15/100/350; sales workflows 50 (restricted triggers)/300/1,000 (sequence triggers); lead scores 5/10/10; custom reports 100 (Pro), 500 (Ent); forecasting from Pro; calling minutes 500/3,000/12,000; Agent Builder simple (Starter) vs event-triggered (Pro+) | verified | same page |
| Zoho CRM: Blueprint is a Professional-tier "process automation". Zia predictive intelligence in Professional, Zia AI sales assistant in Enterprise. Page showed no INR prices, no Canvas, no WhatsApp | verified (partial) | zoho.com/crm/zohocrm-pricing.html |
| Twenty: apps defined as TypeScript (objects, fields, views, agents, logic functions), REST + GraphQL, webhooks, OAuth, sandboxed apps by role; custom objects get the same tables and permissions as People/Companies; stack NestJS, BullMQ, Postgres, Redis, React, Nx; about 60.7k GitHub stars | verified | docs.twenty.com, github.com/twentyhq/twenty |
| Twenty license AGPL-3.0, self-host same product as cloud; cloud Pro $9 and Organization $19 per user/mo (snapshot 2026-05-06) | claimed (blogs; repo page did not name license in the fetch) | dev.to/beton/twenty-crm-pricing-teardown-2026, prospeo.io |
| Salesforce: Starter Suite $25, Pro Suite $100 (annual), Core $195, Advanced $395, Max $550 per user/mo. Agentforce price sources conflict ($125/user + credits vs $5/user + $2/conversation). | claimed (aggregators citing salesforce.com/sales/pricing) | redresscompliance.com, zeeg.me |
| AI-native CRMs: Clarify strongest on auto-capture (email/calendar/meeting sync, pipeline updates); Day.ai on unstructured data, thin CRM structure; Attio relies on data model + AI attributes (web-research agents), no native meeting bot. Sources are competitor blogs. Thin independent reviews. | claimed | coffee.ai, folk.app, breakcold.com |
| India: Zoho has marketplace telephony for Exotel, Ozonetel, Knowlarity; IndiaMART leads do not flow into Zoho unaided (marketplace or webhook needed); Zoho native WhatsApp only on higher tiers, else BSP (AiSensy, Wati); MyOperator documents a LeadSquared integration (call log + recording). | claimed | aaxonix.com, myoperator.com, telecrm.in |
| India privacy: DPDP Rules notified 13 Nov 2025; 18-month phase-in (full duty about 13 May 2027); consent specific, withdrawable, per purpose; clinic is Data Fiduciary, CRM vendor is Processor | claimed (law-firm and vendor blogs; check MeitY primary text) | taxaj.com, kratikal.com |

## 1. Per-product notes
Format: 5 lines (strengths / weaknesses), data model, top 10 paid features. All of this block is **memory** unless the section 0 table says otherwise.

### HubSpot
- Strength: easiest all-in-one (CRM + marketing + service), huge ecosystem, strong free tier.
- Strength: Breeze AI (assistant, agents, prospecting, notetaker) bundled with credits (verified above).
- Weakness: per-seat jumps (Starter $7 to Pro $90) plus onboarding fee and contacts-based marketing cost (memory).
- Weakness: key features gated by tier (workflows, custom objects, reports are Pro+ - memory).
- Weakness: Indian price/WhatsApp depth thin (memory).
- Model: Contacts, Companies, Deals, Tickets, Leads; custom objects (Enterprise - memory); association labels and association limits; timeline events API; lifecycle stage and lead status properties.
- Paid: pipelines, sequences, meetings link, quotes, workflows, forecasting, custom reports, calling, lead scoring, Breeze/Notetaker.

### Salesforce (Sales Cloud, Agentforce)
- Strength: deepest customization, ecosystem, enterprise permissions and governance.
- Strength: Starter/Pro Suite aimed at SMB (price claimed above).
- Weakness: admin cost, consultant dependency, slow UX (memory).
- Weakness: AI pricing confusing (sources conflict).
- Weakness: SMBs use a small slice: leads, opportunities, reports, email integration, a few flows.
- Model: Account, Contact, Lead, Opportunity, Case, Campaign; custom objects with lookup/master-detail relationships; record types; sharing rules; field-level security.
- Paid: forecasting, flows, approvals, CPQ-lite, territories, einstein/Agentforce, sandboxes, API limits, permission sets, reports/dashboards.

### Pipedrive
- Strength: pipeline-first, simple, fast adoption; automations and Campaigns add-on; AI sales assistant (memory).
- Weakness: shallow data model, custom objects limited, add-ons stack up in price (memory; pricing page not fetched).
- Model: Persons, Organizations, Deals, Leads, Activities, Products; custom fields; no true custom objects in lower tiers (memory).
- Paid: visual pipeline, workflow automation, email sync + sequences, Smart Docs/quotes, web forms, caller, reporting, Campaigns, AI assistant, API.

### Zoho CRM
- Strength: breadth for price, Blueprint (stage-by-stage guided process, verified Pro tier), Zia AI (verified tiers), Canvas (drag-drop UI builder - memory), Zoho One/Books/Desk suite, strong India base.
- Weakness: UI clutter, uneven module quality, WhatsApp native only on upper tiers (claimed).
- Model: Leads, Contacts, Accounts, Deals (Potentials), custom modules, lookup relations, layouts, profiles and roles.
- Paid: Blueprint, workflows, Zia, Canvas, territories, WhatsApp/SMS, telephony, Zoho Books sync, sandbox (Ultimate - memory), analytics.

### Freshsales (Freshworks)
- Strength: built-in phone, email, chat, Freddy AI scoring; good price/UX, India roots (memory).
- Weakness: reporting and customization shallower than Zoho/Salesforce (memory).
- Model: Contacts, Accounts, Deals, custom modules (higher tiers).
- Paid: built-in telephony, lead scoring, sequences, workflows, territories, Freddy AI, WhatsApp, forecasting, multiple pipelines.

### Close
- Strength: calling-first for inside sales: power dialer, SMS, call recording, transcripts, sequences (memory).
- Weakness: narrow ICP (outbound SMB sales), not for clinics or marketing; no India telephony depth (memory).
- Model: Leads (company-level) with Contacts, Opportunities; custom fields; smart views.
- Paid: dialers, SMS, sequences, AI call notes, workflows, Zoom, reporting, API, custom activities.

### Attio
- Strength: data-model-first: custom objects, typed attributes, relationships, lists with per-list attributes; modern UI; auto-enrichment (claimed).
- Weakness: needs config time, no native meeting bot (claimed), young integration set.
- Model: Standard objects (People, Companies, Deals, Users, Workspaces) + custom objects; attributes typed (select, status, record reference, actor reference); Lists as separate objects with own attributes.
- Paid: custom objects, workflows, sequences, call intelligence, reports, AI attributes, enrichment, API/webhooks.

### Twenty (open source)
- Strength (verified): TypeScript apps-as-code, REST + GraphQL + webhooks, custom objects equal to built-ins, role-sandboxed apps, self-host via Docker Compose.
- Strength: large community (60.7k stars verified).
- Weakness (claimed, third-party blogs): younger feature set (marketing, quotes, reporting depth, telephony) - check.
- Built: objects, fields, views, workflows, agents, messaging, timeline, Slack/Linear/Granola integrations (verified).
- Skipped / thin (from memory, unverified): marketing automation, quotes/invoicing, native dialer, deep forecasting. License AGPL-3.0 (claimed): copying code into a proprietary SaaS is a legal problem. Copy ideas only.

### monday CRM
- Strength: board metaphor, no-code automations, strong for teams already on monday work management (memory).
- Weakness: seat minimums, tier gating, not CRM-native data model (memory).
- Model: Boards/items/columns with connect-boards; Contacts/Accounts/Deals boards.
- Paid: automations, email sync + sequences, dashboards, forms, quotes/invoices, AI, integrations.

### Copper
- Strength: Gmail/Google Workspace native: lives in sidebar, auto-log email, simple (memory).
- Weakness: Google only, light on automation and reporting, pricey for features (memory).
- Model: People, Companies, Opportunities, Projects, Tasks; custom fields.
- Paid: Gmail sidebar, workflows, pipelines, reports, Google contacts sync, API.

### Odoo CRM (open source community, Enterprise paid)
- Strength: one suite: CRM + quotes + invoicing + accounting + inventory; Indian localization (GST) exists in Odoo (memory).
- Weakness: CRM module simple alone; Enterprise features paywalled; heavy to customize (memory).
- Model: Leads/Opportunities, Partners (contacts+companies), Stages, Activities; custom models via Studio.
- Paid: Enterprise, Studio, VoIP, e-sign, quotes templates, invoicing, marketing automation, IAP lead enrichment.

## 2. FEATURE TIER LIST
| Tier | Features |
|---|---|
| Table stakes (every CRM) | Contacts + companies, deals/opportunities, multiple pipelines with stages, activities/tasks/notes, email sync/logging, custom fields, saved views/filters, import/export CSV, basic reports/dashboards, roles/permissions, mobile app, public API, web forms (Pipedrive, HubSpot, Zoho, Freshsales all list these - memory + HubSpot verified limits) |
| Differentiators (why people choose) | 1 Fast adoption/low admin (Pipedrive, Close). 2 Data model flexibility (Attio, Twenty, Salesforce). 3 Built-in calling/messaging (Close, Freshsales). 4 Process guidance (Zoho Blueprint, HubSpot deal rules). 5 Suite breadth (HubSpot, Zoho, Odoo). 6 Auto-capture of email/meetings (Attio, Clarify claim). 7 Price/self-host (Twenty, Odoo). 8 Local fit: WhatsApp, GST, telephony (Zoho, LeadSquared, Indian CRMs) |
| Bloat (often unused) | Territory management, CPQ, deep forecasting hierarchies, lead-scoring models nobody tunes, 300 workflows, campaign ROI attribution, social media modules, gamification, 100+ dashboards. Reason: SMB teams of 3-20 use pipeline, activity list, a few reports. (memory; reasoning, not measured) |

## 3. AI: real vs hype
| Feature | Verdict | Basis |
|---|---|---|
| Call/meeting summaries + action items | **Real, most used** (HubSpot Notetaker listed on page - verified; Close, Attio, Freshsales ship it - memory) | Saves typing. Low risk. |
| Email drafting / reply suggestions | Real, modest value | Commodity. |
| Enrichment (company/person data) | Real | Quality varies; Indian SMB data weak (memory). |
| Duplicate detect + merge | Real, underrated | Needs human confirm for merge. |
| Natural-language reports/search | Real when over a typed model | Fails if schema messy. |
| Agents that update the CRM from email/meetings | Real but trust problem. Clarify/Day.ai claim it (claimed, vendor blogs); needs review queue, diffs, undo. |
| Lead scoring | Mixed. HubSpot lets 5-10 scores (verified) but SMB volumes too small for ML; rule scoring fine. |
| Next best action | Mostly hype at SMB scale; works as "stale deal / no next step" rules. |
| Deal risk / forecast AI | Hype for small teams (need history volume). |
Rule: AI that removes typing wins. AI that predicts without data volume loses.

## 4. India expectations
- WhatsApp-first: chat inside the record, templates, 24h window, opt-in. Zoho native only on upper tiers, else BSP like AiSensy/Wati (claimed). Build via WhatsApp Cloud API (Meta) - your repo already has WhatsApp Meta setup state.
- GST quotes/invoices: GSTIN, state-wise CGST/SGST vs IGST, HSN/SAC, e-invoice/e-way for larger firms. Odoo and Zoho Books have India localization (memory). **Not verified this session.**
- UPI/payment links: Razorpay/Cashfree payment links with status written back to deal (memory, not verified).
- Regional languages: UI and message templates in Hindi/Tamil etc. (memory; no CRM verified).
- Tally / Zoho Books sync: common ask; Zoho strongest with own Books (memory).
- IndiaMART / JustDial lead capture: needed; Zoho needs marketplace extension or webhook (claimed, aaxonix); Indian CRMs (TeleCRM) advertise native connectors (claimed, vendor).
- Telephony: Exotel, Knowlarity, Ozonetel, MyOperator integrations with click-to-call, call log, recording (claimed). Cloud-telephony webhook = call events into timeline.
- Pricing: Zoho about INR 800/user, LeadSquared INR 2,500-5,000 (claimed, sources conflict).
- Not verified: IndiaMART/JustDial API terms and rate limits, per-vendor connector availability.

## 5. Clinics and service businesses
- Needs: client/patient record (identity, contact, family link, history), appointments with reminders (WhatsApp/SMS), no-show follow-up, recalls (e.g. 6-month checkup), referral source and referrer tracking, consent log, packages/memberships, simple billing, staff-owned views.
- Compliance (claimed): India DPDP Rules 2025 (notified 13 Nov 2025). Per-purpose, withdrawable, dated consent; separate service vs marketing flags; clinic is fiduciary, CRM is processor. HIPAA is US only and irrelevant for India sales. Check MeitY text.
- How CRMs adapt: general CRMs bend via custom objects (Appointment, Treatment) + workflows + WhatsApp - works for sales funnel, weak for clinical records (memory). Vertical products (practice management, salon booking) own this. **I did not verify any vendor's clinic template.**
- Advice: treat clinic as a "service business" preset, not EMR. Store no clinical notes beyond what you must; avoid medical-device-grade claims.

## 6. Integration and platform
- Table stakes: REST API, webhooks, OAuth, CSV import with field mapping, import from Zoho/HubSpot/Salesforce/Pipedrive (one-click migrators are a real sales tool - memory), marketplace, SSO (SAML/OIDC) on higher tiers, audit log, sandbox (Salesforce, Zoho Ultimate - memory).
- Twenty verified: REST + GraphQL + webhooks + OAuth + sandboxed apps (docs.twenty.com).
- Gap: Zapier/Make/n8n presence expected by SMBs.

## 7. Data model patterns worth copying
1. Custom objects with typed fields, same engine as built-ins (Twenty verified, Attio, Salesforce).
2. Association labels (HubSpot; "decision maker", "referrer") plus many-to-many.
3. Timeline events as one append-only stream per record (calls, WhatsApp, email, stage change, payment).
4. Lifecycle stage vs deal stage kept separate (HubSpot).
5. Pipelines with stage rules: required fields at stage exit, not at creation (Blueprint idea, verified existence).
6. Activity types as data (call, visit, demo), not hard-coded.
7. Lists/segments that hold their own attributes (Attio).
8. Field-level permissions and view-level sharing (Salesforce). Fits Metorite visibility model.
9. Apps/schema as code (Twenty verified).

## 8. Pricing and packaging lessons
- Seat tiers with hard feature gates and onboarding fees (HubSpot Pro $1,500) are a visible customer complaint (memory). Metorite flat INR 500/seat is a sharp contrast in India.
- Usage add-on = credits (HubSpot 500/3,000/5,000, extra $0.01 each - verified). Matches Metorite credit design.
- Gate by scale and governance (SSO, audit, sandbox), not by core CRM function.
- Salesforce AI price confusion is a lesson: one meter, one price.

## 9. Traps
1. Complexity creep: every vendor tier adds objects and workflows nobody uses. Ship presets, hide the rest.
2. Mandatory fields kill adoption: require at stage exit, only the few fields that matter.
3. Workflow spaghetti: HubSpot allows 300-1,000 workflows (verified limits). Need run log, owner, and test mode.
4. Duplicate data: dedupe at import and at capture, merge with undo.
5. Data decay (nobody updates the CRM): AI-native CRMs (Clarify, Day.ai, Attio) claim auto-capture from email/calendar/meetings (claimed; competitor sources; thin independent reviews). Treat as promising, unproven. Safe version: capture proposes, human confirms in batch.

## 10. Build / skip for Metorite (my judgment)
Build: contacts/orgs/deals with custom fields; pipelines with exit-rules; unified timeline; WhatsApp inbox in record; click-to-call via Exotel/Knowlarity/MyOperator webhooks; GST quote + payment link; IndiaMART/JustDial lead intake; migrators (Zoho first, then HubSpot/Pipedrive); meeting/call summaries; dedupe; natural-language query; clinic preset with consent + recalls; API/webhooks/audit.
Skip (v1): territories, CPQ, ML forecasting, campaign attribution, marketing suite, social modules, full custom-app SDK.
