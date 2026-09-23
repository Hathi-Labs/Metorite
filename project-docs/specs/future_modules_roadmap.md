# Future Modules Roadmap — the owner's module roster beyond the D19 catalog

**Status:** ROADMAP — owner-directed 2026-08-09 (D21, work_plan.md §3). Nothing here
is dispatchable: no module below has an owning spec meeting the §1 seven-point
contract, and none may be built from this document. This doc exists so (a) the
roster is written down, (b) each future module starts from what already exists
rather than being invented parallel to it, and (c) MT-2's `module_catalog` knows
rows are coming (adding one must stay a data change — `saas_multitenancy.md` §2.4).

**SKU posture (rewritten 2026-08-10 for D23 — there is no a-la-carte list and no
D20 tiers):** a future module ships as an internal atom that **joins a Center
package** — Marketing powers the Marketing Center package (₹600), Support powers
the Support Center package (₹600), Knowledge Base joins the base cross-cutting
slices riding in **every** package — or, exceptionally, sells as a new org-wide
add-on (the Workflows/Builder shape). The owner call at spec time is *which
package or class it belongs to*, not a price. Complete's wildcard row picks up
every GA addition automatically (D20.5, kept by D23). R5 (tenant-ready by
construction) binds any of this work whenever it starts.

---

## 1. Knowledge Base — 🆕 new module

**Owner's intent:** an AI-enabled, graph-based system in the spirit of
Obsidian/Notion — the company's memory, visualizable and organized by department.

**Builds on (do not reinvent):**
- The memory subsystem: Mem0 (pgvector, per-scope — `memory_architecture.md`),
  the org/agent/user scope model, and D17's tenant binding. The KB is best framed
  as a *surface over* organizational memory plus authored documents, not a fourth
  memory store.
- Graphiti is already in-tree and **OFF by design** (`memory-subsystem-state`);
  a graph-based KB is the first real consumer that could justify turning it on —
  that flip is 🔴 OWNER-GATE (§6 `GRAPHITI_ENABLED`) and has a standing cost
  concern recorded in `agent_platform_hardening` Part 5.
- Notes app (documents that already exist), the side-panel document editor, and
  `acb_graph` (the entity graph the sales views read).
- Department organization = Centers (D12 visibility: private → Center → org);
  "KB organized by department" is a projection question, not a new ACL system.

**Open questions for the spec:** authored-pages-vs-derived-memory boundary;
whether the graph view is Graphiti, acb_graph, or a view over links; editor
choice; how agent writes enter the KB (HITL?); per-Center KB spaces vs one org
KB with Center lenses.

## 2. Marketing — 🆕 new module (a Center exists, a module does not)

**Owner's intent:** centralized social-media management across platforms; website
pages and new-site creation; Google Business Page / Search Console / Ads and
campaign insights from Facebook, Google and other channels.

**Builds on:** the Marketing *Center* already exists in `lib/centers.ts` and the
`center.marketing` feature slug (today it carries only email/mail surfaces —
`saas_multitenancy.md` §2.4 maps them to the `email` module). The Integration
Registry + workflow engine (D6) are the substrate for channel connectors and
scheduled posting; the Action Broker gates outward publishes (an outbound social
post is exactly the shape §6's outbound-send gates exist for).

**Open questions:** which channels at launch; website builder scope (this is a
large product by itself — buy/embed vs build); ad-spend data ingestion vs
management (read-only insights first is the cheap slice); how campaign analytics
relate to the dashboards module below; per-channel credential custody (per-org,
migration 158's `provider_keys` shape).

## 3. Customer Support & Success — 🆕 new module

**Owner's intent:** tickets and issues; customer-facing status/support pages on
the customer's DNS; AI ticket assignment; AI resolution drawing on the Knowledge
Base; internal and external chatbots for self-serve troubleshooting.

**Builds on:** email ingestion (a ticket's most common birth channel), WhatsApp
(second channel), the CRM's `crm_activities`/entity model (a ticket is an
activity against a contact/org), agent chat + AgentChat surfaces (the chatbot),
and the KB module above (deflection content). The **dependency on Knowledge Base
is real** — AI resolution quality is bounded by the KB. *(D23 note: since KB
rides in every Center package as a base slice, a Support Center package always
carries it — the `requires` edge is satisfied by construction; what the Support
spec still owes is scoping resolution quality against however much KB content
the org has actually written.)*

**Open questions:** ticket model (own tables vs CRM-activity extension); SLA
machinery; the customer-DNS-pages story (CNAME provisioning, TLS — this
intersects the multi-tenant placement plane, `saas_multitenancy.md` §0.9.5);
external chatbot identity + rate limiting (an unauthenticated, internet-facing
LLM surface is a new threat class — MT-0-grade review required before any tenant
exposure).

📌 **AI ticket assignment has its building block** *(added 2026-09-23,
D75)*. The `decide` task returns a pick from a set of queues or people, with a
calibrated confidence (`customer_console.md` §6A.14). Two facts bind the
future spec. First, Jev accepts 255 options, but nobody has measured its
accuracy at that count. A similar model scored 0.43 on 77 labels. So route by
team first and then by person, and measure both steps. Second, a low confidence goes to a person, not
to a guess. This is still roadmap, and nothing here dispatches. The nearest
live surface is the Projects intake queue (`routes/projects/intake.py`), where
triage is manual today.

## 4. Department Dashboards — ⚠️ mostly EXISTS as planned work (WS-15)

**Owner's intent:** each department configures its own dashboard; leadership
aggregates all departments into company-wide dashboards.

**This is WS-15's mandate** (`department_centers.md` Phase D: Center dashboards,
personal dashboard, Company Center rollup, weekly digests) — blocked today only
on WS-13's owner review. **Do not mint a new module or spec for this**; the new
information from the owner's 2026-08-09 statement is scope colour, recorded here:
dashboards must be *configurable per department* (widget/data selection, not a
fixed layout) and leadership aggregation may produce *multiple* company-wide
dashboards, not one. Carry both into WS-15's acceptance when it dispatches.
SKU-wise (D23): the personal dashboard is `core`; **configurable Center
dashboards are the Dashboards base slice inside every Center package** — not an
independent upsell line.

## 5. Agents App Builder & Workflows as cross-department substrate — ⚠️ EXISTS; the slicing is doctrine

**Owner's intent:** Projects, the App Builder and Workflows are reusable across
the organization; each department sees only its relevant slice; leadership sees
all slices and the whole.

**This is not a new module.** Builder (`build.apps`/`build.agents`, ₹500) and
Workflows (₹300) sell as **org-wide add-ons** (D23) that light up in all a
user's Centers; **Projects is never sold alone — it rides inside every Center
package as a base slice**. "Each
department sees only its slice" is exactly the D12 visibility doctrine (private →
Center → org, `group:<slug>` grants) plus Centers-as-projections; "leadership
sees all" is the org tier plus the admin capability set (mind D14: `manager`'s
real breadth is `admin:members:read`, and org-wide read paths are the thing D12
constrains — leadership visibility must be built as explicit grants/tier, not a
bypass). The one *standing rule this adds*, worth enforcing in review: **new
Builder apps and workflow definitions must declare a visibility tier at creation**
(the D12 standing review rule) so cross-department reuse never silently defaults
to org-wide. Recorded here; enforced via the existing D12 rule, no new machinery.

---

## Sequencing posture

None of these precede the SaaS track (WS-29/MT-2..5, WS-30) or the in-flight app
workstreams. Expected order when capacity opens, per the dependency shape:
**Dashboards (WS-15, already sequenced) → Knowledge Base → Support (requires KB)
→ Marketing** (largest unknowns, least platform leverage today). Each graduates
from this roadmap by getting an owning spec meeting the §1 contract and a WS row;
its row supersedes its section here (R4).
