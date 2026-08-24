// ── Navigation structure for Metorite Control Plane ─────────────────
//
// Owning spec: project-docs/specs/launch_surface.md §2 (the allowlist of
// record) and §3 (what `preview` means). D49, 2026-08-24.
//
// The sidebar has FOUR sections:
//   1. Personal Center — apps mapped one-to-one with the signed-in user.
//      Kept by name under D49: it is a category of personal apps, and never
//      was a departmental projection.
//   2. Apps            — everything that is not personal and not a studio
//                        surface. This is where the retired "Centers" section's
//                        two real apps (Projects, CRM) live now.
//   3. AI Studio       — cross-cutting creation surfaces, scoped per person or
//                        team at the object level (chat sessions, workflows,
//                        apps, agents). Renamed from "Studio".
//   4. Admin           — platform configuration and governance.
//
// ⚠️ **There is no "Centers" section, deliberately** (D49). The Center CODE is
// untouched — `lib/centers.ts`, `/centers/<slug>`, the `center.*` features and
// the `group:<slug>` slice grants D12's live Projects model rests on. A Center
// stopped being a *destination*; it is still the *scoping primitive*. The six
// landing pages survive below as `preview` panes so they stay reachable with
// the preview flag on, and reachable by URL always.
//
// Used by the desktop Sidebar, the mobile navigation drawer, and the home grid
// (`app/page.tsx`) so all three stay in sync — LS-2 requires that they render
// `visibleSections()` and never map `NAV_SECTIONS` themselves.
//
// icon = Lucide icon name (rendered via dynamic import in Sidebar / AppShell).

import { CENTERS } from "@/lib/centers";

/**
 * Whether a pane is part of what we sell today.
 *
 * ⚠️ **This is NOT a permission**, and must never become one
 * (`launch_surface.md` §3.4). Access answers *may this member reach it*;
 * launch status answers *are we offering it yet*. Hiding an unfinished app by
 * revoking `feature:email` would turn a product decision into a data
 * migration, and would make `/access` report "you lack the grant" about a pane
 * no grant can reveal.
 *
 * - `live`    — shipped. Renders in navigation for members who hold its gate.
 * - `preview` — in the application, absent from navigation. Routes, API and
 *               tests all intact; the gateway still authorizes it exactly as
 *               before. It simply is not offered.
 */
export type LaunchStatus = "live" | "preview";

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
  /**
   * Launch status — REQUIRED, so adding a pane forces the decision rather than
   * defaulting into the customer's sidebar. `nav.test.ts` pins the live set
   * against `launch_surface.md` §2, so promoting an app is a one-line edit
   * plus a test update, which is what makes it a deliberate act.
   */
  launch: LaunchStatus;
};

export type NavSection = {
  id: string;
  label: string;
  /** When true the section heading is rendered as a smaller, muted subheading
   *  (like AI Studio / Admin) instead of a prominent section header. */
  sub?: boolean;
  items: NavPane[];
};

/**
 * Whether `preview` panes are restored to the navigation.
 *
 * The one escape hatch (`launch_surface.md` §3.3). A **build-time** Next
 * variable, so flipping it is a redeploy of the control plane — CLAUDE.md §4's
 * ship-dark posture — and it changes NOTHING about authorization: a preview
 * pane restored to the nav is still refused by the gateway to a member without
 * the grant.
 *
 * Read through a function rather than inlined at module scope so tests can
 * exercise both sides without reloading the module graph.
 */
export function previewAppsVisible(
  env: Record<string, string | undefined> = process.env,
): boolean {
  const raw = env.NEXT_PUBLIC_SHOW_PREVIEW_APPS;
  return raw === "1" || raw === "true" || raw === "on";
}

export const NAV_SECTIONS: NavSection[] = [
  // ── Personal Center — the user's own slice of every personal app ──────
  {
    id: "personal",
    label: "Personal Center",
    items: [
      {
        href: "/tasks",
        label: "Tasks",
        icon: "CheckSquare",
        note: "AI task manager",
        feature: "tasks",
        launch: "live",
      },
      // Deliberately UNGATED, and it must stay that way (D-PC-15).
      // `feature:people` gates the DIRECTORY — other people — and is
      // `is_default false`; your own record is not the directory. Gating this
      // hid the one surface whose entire purpose is "every person maintains
      // their own profile" from everybody who had not been granted the roster.
      {
        href: "/people/me",
        label: "My Profile",
        icon: "User",
        note: "Your skills, CV, working hours — what the assignment AI reads",
        launch: "live",
      },
      // Deliberately UNGATED — one of two panes in the sidebar that are. This
      // is the page that explains why a pane is missing, so gating it would
      // hide it from exactly the person who needs it, and "I don't have access
      // to this" would stay an unanswerable sentence. In Personal Center
      // rather than Admin because it is a fact about YOU, and because a plain
      // member must be able to reach it without an Admin heading appearing.
      {
        href: "/access",
        label: "My Access",
        icon: "ShieldCheck",
        note: "What you can reach, and why anything else is hidden",
        launch: "live",
      },
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: "LayoutDashboard",
        note: "Your day across your apps · company view today",
        feature: "dashboard",
        // Held back: the pane is the COMPANY view today; the personal one is
        // WS-15. Shipping it as "your day" would misdescribe what it shows.
        launch: "preview",
      },
      {
        href: "/email",
        label: "Email",
        icon: "Mail",
        note: "AI-powered inbox",
        feature: "email",
        launch: "preview", // WS-17 incomplete
      },
      {
        href: "/whatsapp",
        label: "WhatsApp",
        icon: "MessageSquare",
        note: "AI-powered WhatsApp inbox",
        feature: "whatsapp",
        launch: "preview", // WS-20 incomplete
      },
      {
        href: "/notes",
        label: "Notes",
        icon: "StickyNote",
        note: "AI note taker",
        feature: "notes",
        launch: "preview", // WS-19 incomplete
      },
      {
        href: "/memory",
        label: "Memories",
        icon: "Brain",
        note: "Facts · episodic · knowledge graph",
        feature: "memory",
        // Held back: an operator-grade surface. It shows the machinery, not a
        // thing a customer has a reason to open.
        launch: "preview",
      },
      {
        href: "/artifacts",
        label: "Artifacts",
        icon: "FolderOpen",
        note: "All agent files · inputs · outputs · data",
        feature: "artifacts",
        launch: "preview", // reads as a debugging surface
      },
    ],
  },

  // ── Apps — the shared surfaces (was "Centers" before D49) ─────────────
  {
    id: "apps",
    label: "Apps",
    items: [
      // ONE pane, not one per Center. A Center item was always (app + scope),
      // and forking the app per department is the bloat failure mode
      // department_centers.md §1 rule 2 says to refuse in review. `?center=`
      // still pre-filters the tree for a link that carries it; the server's
      // grants are what actually scope the data.
      {
        href: "/projects",
        label: "Projects",
        icon: "FolderKanban",
        note: "Departments, projects and team tasks",
        feature: "projects",
        launch: "live",
      },
      {
        href: "/crm",
        label: "CRM",
        icon: "KanbanSquare",
        note: "Pipeline, leads and customers",
        feature: "crm",
        launch: "preview", // WS-26 incomplete
      },
      {
        href: "/people",
        label: "People",
        icon: "Users",
        note: "Directory, skills and org chart",
        feature: "people",
        // WS-28 is partly built, but the directory is not launch-ready. Note
        // this does NOT hide /people/me, which is its own ungated live pane.
        launch: "preview",
      },
      // The six Center landing pages, kept as `preview` so they remain
      // reachable with the flag on and by URL always (D49 / launch_surface.md
      // §5). Derived from CENTERS rather than transcribed, so registering a
      // Center cannot silently drop its page — the registry stays the one
      // source of truth even though nothing navigates here today.
      ...CENTERS.map((c): NavPane => ({
        href: `/centers/${c.slug}`,
        label: c.name,
        icon: c.icon,
        note: c.tagline,
        feature: c.feature,
        launch: "preview",
      })),
    ],
  },

  // ── AI Studio — create things; each object is personally or team scoped ──
  {
    id: "ai-studio",
    label: "AI Studio",
    sub: true,
    items: [
      {
        href: "/chat",
        label: "Chat",
        icon: "MessageCircle",
        note: "AI conversations · sessions · rooms",
        feature: "chat",
        launch: "live",
      },
      {
        href: "/workflows",
        label: "Workflows",
        icon: "Workflow",
        note: "Visual automation across agents · tools · integrations",
        feature: "workflows",
        launch: "preview", // WS-11 incomplete
      },
      {
        href: "/build/apps",
        label: "App Workshop",
        icon: "PlusSquare",
        note: "User-created applications",
        feature: "build.apps",
        launch: "preview",
      },
      {
        href: "/build/agents",
        label: "Agent Workshop",
        icon: "Wrench",
        note: "MAF agents & skills",
        feature: "build.agents",
        launch: "preview",
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
        href: "/approvals",
        label: "Approvals",
        icon: "ShieldCheck",
        note: "Action Broker · outward writes awaiting review",
        feature: "approvals",
        launch: "live",
      },
      {
        // The one admin destination for the organization: members & roles,
        // seat assignments and branding as tabs (launch_surface.md §6.2).
        // `/settings/members` redirects here. British spelling is the owner's.
        href: "/settings/organization",
        label: "Organisation",
        icon: "Building2",
        note: "Members & roles · seat assignments · branding",
        adminOnly: true,
        launch: "live",
      },
      {
        // Ungated on purpose: choosing your own theme is a personal
        // preference, not an admin capability. The org-wide default on the
        // same page is authorized at the gateway.
        href: "/settings/appearance",
        label: "Appearance",
        icon: "Palette",
        note: "Themes · colour mode · density · accent",
        launch: "live",
      },
      {
        href: "/settings/models",
        label: "Models",
        icon: "Cpu",
        note: "LLMs · tiers · providers",
        feature: "models",
        launch: "preview", // an operator concern, not a customer one
      },
      {
        href: "/agents",
        label: "Agent Registry",
        icon: "Bot",
        note: "Register · manage · commits · remove",
        feature: "agents",
        launch: "preview", // operator concern
      },
      {
        href: "/integrations",
        label: "Integrations",
        icon: "Plug",
        note: "APIs · MCP servers · plugins",
        feature: "integrations",
        launch: "preview",
      },
      {
        href: "/observability",
        label: "Live Activity",
        icon: "Activity",
        note: "Agent & model activations in real time",
        feature: "observability",
        launch: "preview", // operator concern
      },
      // NO BILLING ENTRY YET — deliberately, and unchanged by D49. The billing
      // console is built and reachable by URL, but the Control Plane it reads
      // from is undeployed ("where it runs is an open owner decision",
      // work_plan.md §2 WS-31), so the page fails closed with "Billing is not
      // configured for this deployment". Promoting it into the sidebar hands
      // every customer admin a menu item that always errors, in our internal
      // environment-variable vocabulary. It is not a `preview` pane because it
      // is not a pane at all; this entry goes in when the Console has
      // somewhere to run.
    ],
  },
];

/** Flat list of all nav panes — kept for backward compatibility. */
export const PANES: NavPane[] = NAV_SECTIONS.flatMap((s) => s.items);

/** Every pane we ship today, in sidebar order. The `launch_surface.md` §2 set. */
export const LIVE_PANES: NavPane[] = PANES.filter((p) => p.launch === "live");

/** Whether this pane is offered at all, given the preview flag. */
export function isLaunched(pane: NavPane, previewVisible: boolean): boolean {
  return pane.launch === "live" || previewVisible;
}

/**
 * Drop panes the member cannot reach, and any section left empty.
 *
 * Presentation only — the gateway authorizes every request regardless of what
 * the sidebar shows. Two filters, applied in this order:
 *
 *   1. **Launch status.** A `preview` pane is not offered, whatever the member
 *      holds (`launch_surface.md` §3). The preview flag restores it.
 *   2. **Access.** The member's resolved features, or the admin flag.
 *
 * ⚠️ **`allowedFeatures === null` means "not resolved yet" and returns `[]`.**
 *
 * It used to return the FULL list, on the reasoning that the nav should not
 * "flicker from complete to filtered on first paint". That reasoning had it
 * backwards: returning everything IS the flicker — the first paint after every
 * sign-in showed the entire application and then removed most of it, for
 * exactly as long as `/api/auth/me` took to answer. That race is the reported
 * "sometimes all the apps appear, sometimes they don't"
 * (`launch_surface.md` §8.1). An unresolved viewer now gets nothing, and the
 * shell renders skeleton rows of the right shape over it, so the nav never
 * shows a link it is about to take away.
 */
export function visibleSections(
  allowedFeatures: string[] | null,
  isAdmin = false,
  previewVisible: boolean = previewAppsVisible(),
): NavSection[] {
  if (allowedFeatures === null) return [];
  const allowed = new Set(allowedFeatures);
  return NAV_SECTIONS.map((section) => ({
    ...section,
    items: section.items.filter((p) => {
      if (!isLaunched(p, previewVisible)) return false;
      if (p.adminOnly) return isAdmin;
      return !p.feature || allowed.has(p.feature);
    }),
  })).filter((section) => section.items.length > 0);
}
