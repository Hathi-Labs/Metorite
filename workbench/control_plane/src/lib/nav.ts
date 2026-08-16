// ── Navigation structure for Metorite Control Plane ─────────────────
//
// The sidebar is organised around the Centers model (one platform, many
// projections — see project-docs/specs/org_access_control.md §5):
//   1. Personal Center — apps mapped one-to-one with the signed-in user
//   2. Centers         — departmental projections (Sales, Marketing, …),
//                        each gated by its `center.<slug>` feature and
//                        landing on /centers/<slug>
//   3. Studio          — cross-cutting creation surfaces, scoped per person
//                        or department at the object level (chat sessions,
//                        workflows, apps, agents)
//   4. Admin           — platform configuration and governance
//
// Used by the desktop Sidebar and the mobile navigation drawer so both stay
// in sync. `PANES` (flat list) is kept for backward-compatible consumers.
//
// icon = Lucide icon name (rendered via dynamic import in Sidebar / AppShell).

import { CENTERS } from "@/lib/centers";

export type NavPane = {
  href: string;
  label: string;
  /** Lucide icon name, e.g. "MessageCircle", "Zap", "Wrench" */
  icon: string;
  note: string;
  badge?: string;
  /**
   * Feature slug guarding this pane (org access control). Matches
   * `feature_catalog.slug` in infra/postgres/130_org_access_control.sql
   * (+ 140_center_features.sql for the Centers). A pane without one is
   * visible to every signed-in member.
   */
  feature?: string;
  /**
   * Pane gated on the resolved admin flag instead of a feature slug —
   * mirrors canSeePath()'s admin-surface rule in lib/access.ts.
   */
  adminOnly?: boolean;
};

export type NavSection = {
  id: string;
  label: string;
  /** When true the section heading is rendered as a smaller, muted subheading
   *  (like Studio / Admin) instead of a prominent section header. */
  sub?: boolean;
  items: NavPane[];
};

export const NAV_SECTIONS: NavSection[] = [
  // ── Personal Center — the user's own slice of every personal app ──────
  {
    id: "personal",
    label: "Personal Center",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: "LayoutDashboard",
        note: "Your day across your apps · company view today",
        feature: "dashboard",
      },
      {
        href: "/email",
        label: "Email",
        icon: "Mail",
        note: "AI-powered inbox",
        feature: "email",
      },
      {
        href: "/whatsapp",
        label: "WhatsApp",
        icon: "MessageSquare",
        note: "AI-powered WhatsApp inbox",
        feature: "whatsapp",
      },
      {
        href: "/tasks",
        label: "Tasks",
        icon: "CheckSquare",
        note: "AI task manager",
        feature: "tasks",
      },
      {
        href: "/notes",
        label: "Notes",
        icon: "StickyNote",
        note: "AI note taker",
        feature: "notes",
      },
      {
        href: "/memory",
        label: "Memories",
        icon: "Brain",
        note: "Facts · episodic · knowledge graph",
        feature: "memory",
      },
      // Deliberately UNGATED — the only item in the sidebar that is. This is
      // the page that explains why a pane is missing, so gating it would hide
      // it from exactly the person who needs it, and "I don't have access to
      // this" would stay an unanswerable sentence. In Personal Center rather
      // than Admin because it is a fact about YOU, and because a plain member
      // must be able to reach it without an Admin heading appearing for them.
      // Deliberately UNGATED, for the same reason "Your access" below is
      // (WS-28g-2 / people_center_app.md D-PC-15). `feature:people` gates the
      // DIRECTORY — other people — and is `is_default false`; your own record
      // is not the directory. Gating this hid the one surface whose entire
      // purpose is "every person maintains their own profile" from everybody
      // who had not been granted the org roster.
      {
        href: "/people/me",
        label: "My profile",
        icon: "User",
        note: "Your skills, CV, working hours — what the assignment AI reads",
      },
      {
        href: "/access",
        label: "Your access",
        icon: "ShieldCheck",
        note: "What you can reach, and why anything else is hidden",
      },
      {
        href: "/artifacts",
        label: "Artifacts",
        icon: "FolderOpen",
        note: "All agent files · inputs · outputs · data",
        feature: "artifacts",
      },
    ],
  },

  // ── Centers — departmental projections of the same platform ──────────
  {
    id: "centers",
    label: "Centers",
    items: [
      ...CENTERS.map((c) => ({
        href: `/centers/${c.slug}`,
        label: c.name,
        icon: c.icon,
        note: c.tagline,
        feature: c.feature,
      })),
      // A Center MODULE, not a Center: /crm is the Sales Center's pipeline app
      // (lib/centers.ts links to it from there too), and it is gated on its
      // own `crm` feature rather than on `center.sales`. It sits in this
      // section because that is where somebody looks for it, and it is a
      // literal rather than derived from CENTERS because a Center's apps are
      // not all separate panes. `feature:crm` is `is_default false` in
      // migration 144, so until an admin grants it this pane is owner/admin
      // only (specs/crm_app.md §5, D-CRM-3).
      {
        href: "/crm",
        label: "CRM",
        icon: "KanbanSquare",
        note: "Pipeline, leads and customers",
        feature: "crm",
      },
      // Same shape, same reasoning: /projects is the People Center's work
      // module and every other Center's slice of it. ONE pane, not one per
      // Center — a Center item is (app + scope), and forking the app per
      // department is the bloat failure mode department_centers.md §1 rule 2
      // says to refuse in review. The Centers link into it with `?center=<slug>`
      // (lib/centers.ts), which pre-filters the tree; the server's grants are
      // what actually scope the data.
      {
        href: "/projects",
        label: "Projects",
        icon: "FolderKanban",
        note: "Departments, projects and team tasks",
        feature: "projects",
      },
      // The people behind the work, next to it. Its own feature slug rather
      // than riding `tasks`: a manager who needs the org chart and the
      // assignee picker should not have to be handed the personal GTD task
      // manager to get them (people_center_app.md §6).
      {
        href: "/people",
        label: "People",
        icon: "Users",
        note: "Directory, skills and org chart",
        feature: "people",
      },
    ],
  },

  // ── Studio — create things; each object is personally or team scoped ──
  {
    id: "studio",
    label: "Studio",
    sub: true,
    items: [
      {
        href: "/chat",
        label: "Chat",
        icon: "MessageCircle",
        note: "AI conversations · sessions · rooms",
        feature: "chat",
      },
      {
        href: "/workflows",
        label: "Workflows",
        icon: "Workflow",
        note: "Visual automation across agents · tools · integrations",
        feature: "workflows",
      },
      {
        href: "/build/apps",
        label: "App Workshop",
        icon: "PlusSquare",
        note: "User-created applications",
        feature: "build.apps",
      },
      {
        href: "/build/agents",
        label: "Agent Workshop",
        icon: "Wrench",
        note: "MAF agents & skills",
        feature: "build.agents",
      },
    ],
  },

  // ── Admin — platform configuration and governance ─────────────────────
  {
    id: "admin",
    label: "Admin",
    sub: true,
    items: [
      {
        href: "/settings/models",
        label: "Models",
        icon: "Cpu",
        note: "LLMs · tiers · providers",
        feature: "models",
      },
      {
        href: "/agents",
        label: "Agent Registry",
        icon: "Bot",
        note: "Register · manage · commits · remove",
        feature: "agents",
      },
      {
        href: "/approvals",
        label: "Approvals",
        icon: "ShieldCheck",
        note: "Action Broker · outward writes awaiting review",
        feature: "approvals",
      },
      {
        href: "/integrations",
        label: "Integrations",
        icon: "Plug",
        note: "APIs · MCP servers · plugins",
        feature: "integrations",
      },
      {
        href: "/observability",
        label: "Live Activity",
        icon: "Activity",
        note: "Agent & model activations in real time",
        feature: "observability",
      },
      {
        href: "/settings/members",
        label: "Members & Roles",
        icon: "UserCog",
        note: "People · teams · roles · per-user access",
        adminOnly: true,
      },
      {
        // The company's own identity inside the product — the logo every
        // member sees top-left. Admin-only: it changes everyone's shell.
        href: "/settings/organization",
        label: "Organization",
        icon: "Building2",
        note: "Company logo · how your org appears to its members",
        adminOnly: true,
      },
      // NO BILLING ENTRY YET — deliberately. The billing console is built and
      // reachable by URL, but the Control Plane it reads from is undeployed
      // ("where it runs is an open owner decision", work_plan.md §2 WS-31), so
      // the page fails closed with "Billing is not configured for this
      // deployment (CONTROL_PLANE_URL / CONTROL_PLANE_ORG_KEY)". Promoting it
      // into the sidebar hands every customer admin a menu item that always
      // errors, in our internal environment-variable vocabulary.
      //
      // A previous version of this file added the entry with the note
      // "reachable, at last" — reading URL-only as an oversight. It was not an
      // oversight, it was CLAUDE.md §4's "ship dark": new behaviour lands
      // behind a flag, default OFF, and flipping it is the owner's act. This
      // entry goes in when the Control Plane has somewhere to run.
      {
        // Ungated on purpose: choosing your own theme is a personal
        // preference, not an admin capability. The org-wide default on the
        // same page is authorized at the gateway.
        href: "/settings/appearance",
        label: "Appearance",
        icon: "Palette",
        note: "Themes · colour mode · density · accent",
      },
    ],
  },
];

/** Flat list of all nav panes — kept for backward compatibility. */
export const PANES: NavPane[] = NAV_SECTIONS.flatMap((s) => s.items);

/**
 * Drop panes the member cannot reach, and any section left empty.
 *
 * Presentation only — the gateway authorizes every request regardless of what
 * the sidebar shows. Passing `null` (access not yet resolved) returns the full
 * list so the nav does not flicker from complete to filtered on first paint.
 */
export function visibleSections(
  allowedFeatures: string[] | null,
  isAdmin = false
): NavSection[] {
  if (allowedFeatures === null) return NAV_SECTIONS;
  const allowed = new Set(allowedFeatures);
  return NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((p) => {
      if (p.adminOnly) return isAdmin;
      return !p.feature || allowed.has(p.feature);
    }),
  })).filter((section) => section.items.length > 0);
}
