// ── Departmental Centers — the registry ───────────────────────────────────
//
// A Center is a scoped PROJECTION of the one platform, not a separate app:
// a department's slice of apps, agents, memory, and workflows, gated by a
// `center.<slug>` feature (feature_catalog, migration 140) and — once access
// control Phase 2 lands — backed by an org_group of the same slug.
//
// This file is the single source of truth for the scaffold: the sidebar
// (lib/nav.ts), the landing pages (app/centers/[slug]), the home grid, and
// the route→feature map (lib/access.ts) all derive from it. Sub-apps carry a
// build status so the scaffold is honest about what exists: `live` items link
// to a real surface today (sometimes unscoped — noted per item); `planned`
// items are the buildout backlog, visible so the destination is legible.
//
// Spec trail: project-docs/specs/org_access_control.md §5 (modules are
// Phase 2), groups_sessions_authority.md §1 (org_group is the primitive),
// docs/multiplayer/agent-kinds.md §6 (team-instanced agents per department).

export type CenterAppStatus = "live" | "planned";

type CenterAppBase = {
  label: string;
  /** What this sub-app is for — real content, shown on the Center page. */
  note: string;
  /** Lucide icon name (resolveIcon handles the full set). */
  icon: string;
  /** Caveat when the live surface is not yet scoped to the Center. */
  caveat?: string;
};

/**
 * A sub-app on a Center page.
 *
 * ⚠️ **Discriminated on `status`, deliberately, so `live ⇒ href` is a compile
 * error rather than a rendering bug.** The Center page renders a live tile as
 * a link to `app.href ?? "#"`, so a `live` item that forgot its href ships as
 * a clickable card that navigates nowhere — and the only test near this file
 * (`test_centers_registry_matches_the_feature_vocabulary`) reads each
 * Center's `feature:` field and nothing else, so it cannot see a mistake in
 * `apps[]` at all. `planned` items must NOT carry an href for the same
 * reason in reverse: a destination that exists is not planned, it is live and
 * mis-labelled, and the Center page files it under "coming soon".
 * Runtime twin: `centers.test.ts`.
 */
export type CenterApp =
  | (CenterAppBase & {
      status: "live";
      /** Where it opens today. Required — that is what `live` means. */
      href: string;
    })
  | (CenterAppBase & {
      status: "planned";
      href?: never;
    });

export type Center = {
  slug: string;
  name: string;
  icon: string;
  tagline: string;
  /** feature_catalog slug gating this Center (migration 140). */
  feature: string;
  /** The org_group.slug this Center projects, once groups have an admin UI. */
  group: string;
  apps: CenterApp[];
};

export const CENTERS: Center[] = [
  {
    slug: "sales",
    name: "Sales Center",
    icon: "Handshake",
    tagline: "Pipeline, quotations, and follow-ups for the sales team",
    feature: "center.sales",
    group: "sales",
    apps: [
      {
        label: "Projects & tasks",
        note: "This Center's slice of the Projects app — the departments and projects granted to group:sales",
        icon: "FolderKanban",
        status: "live",
        href: "/projects?center=sales",
      },
      {
        label: "Sales dashboard",
        note: "Pipeline health, quota progress, follow-ups due — the team's morning view",
        icon: "LayoutDashboard",
        status: "planned",
      },
      {
        label: "Tasks",
        note: "The sales team's slice of tasks and projects",
        icon: "CheckSquare",
        status: "live",
        href: "/tasks",
        caveat: "Opens the full Tasks app today — the team-scoped slice arrives with groups",
      },
      {
        label: "Proposal generator",
        note: "Quotations and proposals from templates — deal terms in, branded PDF out",
        icon: "FileText",
        status: "planned",
      },
      {
        // WS-26c. The native CRM replaced the Zoho mirror as the destination:
        // Metorite is becoming the system of record and Zoho the import
        // source (specs/crm_app.md §1), so the tile names ours.
        label: "CRM",
        note: "Deals, leads, contacts and organizations — the pipeline the team works",
        icon: "KanbanSquare",
        status: "live",
        href: "/crm",
      },
      {
        label: "Shared mailbox",
        note: "sales@ as a team-owned account with per-member access",
        icon: "Mail",
        status: "planned",
      },
      {
        label: "Lead intake workflow",
        note: "Webhook lead → triage agent → CRM draft staged for approval",
        icon: "Workflow",
        status: "planned",
      },
    ],
  },
  {
    slug: "marketing",
    name: "Marketing Center",
    icon: "Megaphone",
    tagline: "Campaigns, content, and the public face of the company",
    feature: "center.marketing",
    group: "marketing",
    apps: [
      {
        label: "Projects & tasks",
        note: "This Center's slice of the Projects app — the departments and projects granted to group:marketing",
        icon: "FolderKanban",
        status: "live",
        href: "/projects?center=marketing",
      },
      {
        label: "Marketing dashboard",
        note: "Campaign performance, content calendar, channel health at a glance",
        icon: "LayoutDashboard",
        status: "planned",
      },
      {
        label: "Campaigns",
        note: "Plan and track campaigns across channels with agent-drafted assets",
        icon: "Target",
        status: "planned",
      },
      {
        label: "Content studio",
        note: "Brochures, datasheets, and case studies — drafted, reviewed, versioned",
        icon: "Palette",
        status: "planned",
      },
      {
        label: "Website & SEO",
        note: "Site updates and search visibility, proposed as approvable changes",
        icon: "Globe",
        status: "planned",
      },
      {
        label: "Social scheduler",
        note: "Queue and publish posts; agents draft, humans approve",
        icon: "CalendarClock",
        status: "planned",
      },
      {
        label: "Newsletter",
        note: "Audience segments and sends — the Hostinger Reach integration is already connected",
        icon: "Send",
        status: "planned",
      },
    ],
  },
  {
    slug: "finance",
    name: "Finance Center",
    icon: "Landmark",
    tagline: "Receivables, payments, and the numbers that run the company",
    feature: "center.finance",
    group: "finance",
    apps: [
      {
        label: "Projects & tasks",
        note: "This Center's slice of the Projects app — the departments and projects granted to group:finance",
        icon: "FolderKanban",
        status: "live",
        href: "/projects?center=finance",
      },
      {
        label: "Finance dashboard",
        note: "Cash position, receivables aging, collections due this week",
        icon: "LayoutDashboard",
        status: "planned",
      },
      {
        label: "Invoices & receivables",
        note: "Outstanding invoices from the books, with aging and status",
        icon: "Receipt",
        status: "planned",
      },
      {
        label: "Payment follow-ups",
        note: "Dunning sequences drafted by the billing agent, gated by approval",
        icon: "BellRing",
        status: "planned",
      },
      {
        label: "Expenses & approvals",
        note: "Expense capture and approval routing",
        icon: "Wallet",
        status: "planned",
      },
      {
        label: "Purchase orders",
        note: "PO lifecycle against vendors, linked to procurement",
        icon: "ClipboardList",
        status: "planned",
      },
    ],
  },
  {
    slug: "operations",
    name: "Operations Center",
    icon: "Factory",
    tagline: "Production, inventory, dispatch, and service for the machines",
    feature: "center.operations",
    group: "operations",
    apps: [
      {
        label: "Projects & tasks",
        note: "This Center's slice of the Projects app — the departments and projects granted to group:operations",
        icon: "FolderKanban",
        status: "live",
        href: "/projects?center=operations",
      },
      {
        label: "Ops dashboard",
        note: "Builds in progress, dispatches due, open service tickets",
        icon: "LayoutDashboard",
        status: "planned",
      },
      {
        label: "Production tracker",
        note: "Printer builds and assembly stages from order to QC",
        icon: "Cog",
        status: "planned",
      },
      {
        label: "Inventory & BOM",
        note: "Stock levels against bills of material; flag shortages before they block builds",
        icon: "Boxes",
        status: "planned",
      },
      {
        label: "Procurement",
        note: "Vendor orders and lead times, linked to finance POs",
        icon: "ShoppingCart",
        status: "planned",
      },
      {
        label: "Dispatch & installations",
        note: "Shipping, delivery, and on-site installation scheduling",
        icon: "Truck",
        status: "planned",
      },
      {
        label: "Service & AMC tickets",
        note: "Support queue, warranty and AMC coverage, spare-part requests",
        icon: "LifeBuoy",
        status: "planned",
      },
    ],
  },
  {
    slug: "people",
    name: "People Center",
    icon: "Users",
    tagline: "The team itself — directory, onboarding, leave, and hiring",
    feature: "center.people",
    group: "people",
    apps: [
      {
        label: "Projects & work",
        note: "The whole portfolio — every department, project and task. This Center is the app's home; the others see their granted slice of the same app, never a fork",
        icon: "FolderKanban",
        status: "live",
        href: "/projects",
      },
      {
        label: "People dashboard",
        note: "The Center landing — headcount, who's away, load spread, and the health of the record itself",
        icon: "LayoutDashboard",
        // WS-28l (§5.9): a projection of the workload rollup and the §5.10
        // quality counts, narrowed to what exists — open roles and onboarding
        // stay with their own planned entries below. Needs `admin:members:read`
        // like the two surfaces it projects.
        status: "live",
        href: "/people/overview",
      },
      {
        label: "My profile",
        note: "Your own record — skills, CV, working hours and the details the assignment suggester reads",
        // `User`, not `UserCircle`: the icon registry maps a curated set onto
        // Fluent and Material, an unmapped name silently falls back to Lucide,
        // and `icon-registry.test.ts` fails a NAV icon that is not in every
        // pack — which is how this was caught rather than shipped as one
        // sidebar entry drawn in the wrong style.
        icon: "User",
        // WS-28g: the self-service half. Same app, same feature slug, same
        // panels as the person page — what differs is which row, resolved from
        // the caller's own address (people_center_app.md §5.3, D-PC-1).
        status: "live",
        href: "/people/me",
      },
      {
        label: "Directory & org chart",
        note: "Members and groups — the same org_group records that scope the Centers",
        icon: "Network",
        // WS-28b: the read view WS-13 asked for. Live at /people — one app,
        // not a People-Center fork of one, the same (app + scope) rule the
        // Projects entries follow.
        status: "live",
        href: "/people",
      },
      {
        label: "Who can help?",
        note: "Capability search — stated skills, résumé evidence and related work, each match showing its reasoning. Suggests, never assigns",
        icon: "Search",
        // WS-28d. Needs `admin:members:read`: a skills query by definition
        // (people_center_app.md §5.5, §4.2's oracle rule).
        status: "live",
        href: "/people/search",
      },
      {
        label: "Workload dashboard",
        note: "Who is behind, at risk, overloaded or idle — every person's projects, deadlines and committed hours against the week they actually have",
        // `Gauge`, mapped in Fluent and Material both (the registry check on
        // NAV icons is what catches an unmapped name before it ships as one
        // entry drawn in the wrong style).
        icon: "Gauge",
        // WS-28j1. Needs `admin:members:read` on top of `feature:people`: it is
        // skills, capacity and hours for everybody at once, so §4.2's oracle
        // rule applies to the whole surface (people_center_app.md §5.7.5).
        status: "live",
        href: "/people/dashboard",
      },
      {
        label: "Working week",
        note: "Working days, hours per day and shifts — what everybody's contracted hours are derived from",
        icon: "CalendarDays",
        // WS-28p. Readable by any holder (a person cannot understand their own
        // schedule without the layer underneath it); editable with
        // `admin:members:manage`.
        status: "live",
        href: "/people/schedule",
      },
      {
        label: "Onboarding",
        note: "Checklists that provision accounts, access, and first-week tasks",
        icon: "UserPlus",
        status: "planned",
      },
      {
        label: "Leave & attendance",
        note: "Requests and approvals, visible to the person and their lead",
        icon: "CalendarDays",
        status: "planned",
      },
      {
        label: "Hiring pipeline",
        note: "Roles, candidates, and interview stages",
        icon: "UserSearch",
        status: "planned",
      },
    ],
  },
  {
    slug: "company",
    name: "Company Center",
    icon: "Building2",
    tagline: "The founder's cockpit — every Center rolled up into one view",
    feature: "center.company",
    group: "company",
    apps: [
      {
        label: "All projects",
        note: "Every department's work in one place — the full portfolio, which needs data:org:read",
        icon: "FolderKanban",
        status: "live",
        href: "/projects",
      },
      {
        label: "Company dashboard",
        note: "The org-wide overview, fed by every Center",
        icon: "LayoutDashboard",
        status: "live",
        href: "/dashboard",
      },
      {
        label: "Live activity",
        note: "Every agent and model activation across the company, in real time",
        icon: "Activity",
        status: "live",
        href: "/observability",
      },
      {
        label: "Approvals",
        note: "The one inbox for outward writes awaiting a human decision",
        icon: "ShieldCheck",
        status: "live",
        href: "/approvals",
      },
      {
        label: "Weekly digests",
        note: "Scheduled workflow roll-ups from each Center into one executive brief",
        icon: "Newspaper",
        status: "planned",
      },
      {
        label: "Company knowledge",
        note: "Org-wide memory every agent reads — reviewed, corrected, versioned",
        icon: "BookOpen",
        status: "planned",
      },
    ],
  },
];

export function centerBySlug(slug: string): Center | undefined {
  return CENTERS.find((c) => c.slug === slug);
}
