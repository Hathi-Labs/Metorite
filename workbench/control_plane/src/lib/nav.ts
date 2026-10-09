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
/**
 * Routes that render WITHOUT the application chrome — no sidebar, no docks,
 * no mobile bottom nav (CP-2c onboarding UX, owner directive 2026-08-24: the
 * signup form was rendering inside the app shell, sidebar and all, which
 * reads as "you are already in the product" to someone who has not joined
 * it). Prefix-matched, so `/signin/code` rides on `/signin`.
 *
 * Deliberately NOT the same thing as "public": `/signup` requires a session
 * (its page redirects without one). Chrome is a statement about belonging to
 * an organization's workspace; onboarding is the state of not yet belonging.
 *
 * `/oauth/approved` (WS-17 EM-T3c) is the landing page of an IT admin who
 * approved Metorite in Microsoft. That admin has no session and may never
 * join, so the page carries no chrome. It is public in `proxy.ts` as well.
 */
export const CHROMELESS_ROUTES: readonly string[] = ["/signin", "/signup", "/oauth/approved"];

export function isChromeless(pathname: string): boolean {
  return CHROMELESS_ROUTES.some(
    (r) => pathname === r || pathname.startsWith(`${r}/`),
  );
}

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

  // ── The shell manifest (D89, `navigation_shell.md` §5.1, NS-2) ──────────
  // The shell reads these fields. An app never edits the sidebar, the
  // launcher or the account menu by hand. `nav.test.ts` fails on a live pane
  // with no `team` or no `blurb`.

  /** The group the pane belongs to, in the sidebar and in All apps. */
  team: PaneTeam;
  /**
   * What the app is for, in one sentence of job words, 60 characters at
   * most. The launcher prints it under the name, and the command bar prints
   * it beside "Open …". Required for a live pane. `note` is the older line,
   * written for operators, and the home grid still reads it.
   */
  blurb?: string;
  /**
   * Where the pane's door is, with the shell nav on (`shellNavOn`).
   * `sidebar`, the default, puts it in its team's group. `account` puts it in
   * the account menu at the sidebar's foot, for a page about the member or
   * the organization rather than a place to work.
   */
  door?: "sidebar" | "account";
  /**
   * A preference about the member, not an app: My Profile, Appearance. It
   * opens from the account menu and never shows in All apps.
   */
  setting?: true;
};

/**
 * A pane's team. `personal` is the member's own work, `across` is shared
 * with their teams, `studio` makes things with AI, and `admin` runs the
 * organization. A Center's slug may also stand here (§5.1).
 */
export type PaneTeam = "personal" | "across" | "studio" | "admin" | (string & {});

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
  env?: Record<string, string | undefined>,
): boolean {
  // ⚠️ The LITERAL member expression is the only form Next inlines into the
  // browser bundle — see `src/lib/publicFlags.test.ts`.
  // A defaulted `env = process.env` reads the `{}` polyfill in a browser, so
  // the flag is permanently false and every test still passes, because each
  // test hands the function an env object of its own.
  const raw = env
    ? env.NEXT_PUBLIC_SHOW_PREVIEW_APPS
    : process.env.NEXT_PUBLIC_SHOW_PREVIEW_APPS;
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
        team: "personal",
        blurb: "Your to-do list, and the work given to you",
        label: "My Tasks",
        icon: "CheckSquare",
        note: "Your tasks, and your view of the company's",
        feature: "tasks",
        launch: "live",
      },
      // Calendar — D54 (2026-08-24, board WS-39 S2). Lifted out of `/tasks`,
      // where it had been a view mode rather than a destination.
      //
      // ⚠️ Gated on `feature:tasks`, NOT a new `feature:calendar`, and that is
      // deliberate. A new slug is a grant nobody holds: minting one would ship
      // this app DARK to every existing member, and un-darkening it is an
      // owner-gated role write (`work_plan.md` §6, the WS-24 (d) class) on top
      // of a migration that has to reach a box first (H-1). Riding the grant
      // that already covers this surface keeps reachability exactly as it is
      // today — the calendar was inside Tasks, so everyone with Tasks had it.
      //
      // `live`, not `preview`: the surface ships today inside a live app, so
      // holding it back would WITHDRAW a capability customers already have.
      // That is why `launch_surface.md` §2's live set is nine and not eight.
      {
        href: "/calendar",
        team: "personal",
        blurb: "Plan your day and block time to focus",
        label: "Calendar",
        icon: "Calendar",
        note: "When your work happens — timeboxing, day plan, focus",
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
        team: "personal",
        blurb: "Your skills, CV and working hours",
        door: "account",
        setting: true,
        label: "My Profile",
        icon: "User",
        note: "Your skills, CV, working hours — what the assignment AI reads",
        launch: "live",
      },
      // ⚠️ "My Access" is NOT a pane any more (owner directive, 2026-10-05:
      // "remove my access from the sidebar and fold it into the People's
      // app"). It is the "My access" tab of the People app, at
      // `/people/access`, beside My profile, and it is still UNGATED
      // (`lib/access.ts` ALWAYS_ALLOWED). Do not put it back here. The People
      // layout shows the two personal tabs even to a member without
      // `feature:people`, so that member still has a door to it.
      {
        href: "/dashboard",
        team: "personal",
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
        team: "personal",
        blurb: "Read, sort and answer your email, with AI help",
        // "My Email" since 2026-10-08 (owner): the member's own mailbox,
        // apart from any shared team inbox a later app may add.
        label: "My Email",
        icon: "Mail",
        note: "AI-powered inbox",
        feature: "email",
        // `live` since 2026-10-02, by owner decision of 2026-10-01 (H-21,
        // WS-17 EM-T3b). A member connects Microsoft 365 from inside the app,
        // with no setup step. `launch_surface.md` §2 moved in the same change.
        launch: "live",
      },
      {
        href: "/whatsapp",
        team: "personal",
        blurb: "Your WhatsApp Business chats, with AI help",
        // "My WhatsApp", and `live`, since 2026-10-08 by owner decision (WS-20
        // WA-C6). A member connects a WhatsApp Business account through Meta's
        // Embedded Signup, the one official way in. `launch_surface.md` §2
        // moved in the same change.
        label: "My WhatsApp",
        icon: "MessageSquare",
        note: "AI-powered WhatsApp inbox",
        feature: "whatsapp",
        launch: "live",
      },
      {
        href: "/notes",
        team: "personal",
        label: "Notes",
        icon: "StickyNote",
        note: "AI note taker",
        feature: "notes",
        launch: "preview", // WS-19 incomplete
      },
      {
        href: "/memory",
        team: "personal",
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
        team: "personal",
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
        team: "across",
        blurb: "Plan and track your teams' projects and tasks",
        label: "Projects",
        icon: "FolderKanban",
        note: "Departments, projects and team tasks",
        feature: "projects",
        launch: "live",
      },
      {
        href: "/crm",
        team: "across",
        label: "CRM",
        icon: "KanbanSquare",
        note: "Pipeline, leads and customers",
        feature: "crm",
        launch: "preview", // WS-26 incomplete
      },
      {
        href: "/people",
        team: "across",
        blurb: "Find a colleague, their skills and who is out",
        label: "People",
        icon: "Users",
        note: "Directory, skills and org chart",
        feature: "people",
        // `live` since 2026-09-20 — owner decision, taking the live set from
        // NINE to TEN. Held back until now because the directory had never
        // loaded (PR #306: the BFF proxy was a REQUIRED catch-all, so the
        // bare path 404'd for its whole life) and because no member ever got
        // a `people` row, so it rendered empty even once it did load.
        // Both are fixed, and the roster sync (H-124) seeds it.
        //
        // ⚠️ `feature:people` is `is_default false`, so promoting the pane
        // does NOT make it visible to anybody who has not been granted the
        // slug — the owner's `*` matches, an ordinary member's grants do not.
        // Launch status answers *are we offering it*; the grant answers *may
        // this member reach it*, and they stay two questions.
        launch: "live",
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
        team: "across",
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
        team: "studio",
        blurb: "Ask the AI assistant, or have it do the work",
        label: "Chat",
        icon: "MessageCircle",
        note: "AI conversations · sessions · rooms",
        feature: "chat",
        launch: "live",
      },
      {
        href: "/workflows",
        team: "studio",
        label: "Workflows",
        icon: "Workflow",
        note: "Visual automation across agents · tools · integrations",
        feature: "workflows",
        launch: "preview", // WS-11 incomplete
      },
      {
        href: "/build/apps",
        team: "studio",
        label: "App Workshop",
        icon: "PlusSquare",
        note: "User-created applications",
        feature: "build.apps",
        launch: "preview",
      },
      {
        href: "/build/agents",
        team: "studio",
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
        team: "admin",
        blurb: "Check what the AI will send before it goes out",
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
        team: "admin",
        blurb: "Members, roles, seats and your brand",
        door: "account",
        label: "Organisation",
        icon: "Building2",
        note: "Members & roles · seat assignments · branding",
        adminOnly: true,
        launch: "live",
      },
      {
        // Ungated on purpose: how the product looks to you is a personal
        // preference, not an admin capability. The org-wide default on the
        // same page is authorized at the gateway.
        href: "/settings/appearance",
        team: "personal",
        blurb: "Light or dark, spacing and accent colour",
        door: "account",
        setting: true,
        label: "Appearance",
        icon: "Palette",
        // "Themes ·" led this line until 2026-08-31. The theming engine is
        // retired — there is one look, and these three adjust it.
        note: "Colour mode · density · accent",
        launch: "live",
      },
      // ⚠️ NO MODELS ENTRY, and no route behind it either. CP-5 deleted
      // `settings/models/page.tsx` on 2026-08-28 (D32.7, §5.1): model
      // operations moved to the OPERATOR console, which is where the
      // keys and the rate card already lived. A customer never sees a
      // model, so there is nothing here to gate — `preview` was hiding
      // a surface that should not exist in this product at all.
      {
        href: "/agents",
        team: "admin",
        label: "Agent Registry",
        icon: "Bot",
        note: "Register · manage · commits · remove",
        feature: "agents",
        launch: "preview", // operator concern
      },
      {
        href: "/integrations",
        team: "admin",
        label: "Integrations",
        icon: "Plug",
        note: "APIs · MCP servers · plugins",
        feature: "integrations",
        launch: "preview",
      },
      {
        href: "/observability",
        team: "admin",
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
