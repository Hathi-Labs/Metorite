// ── The rail of views (spec: integrations_console.md §7.2, IN-2) ───────────
//
// Connections is the console. API keys, Plugins and Skills are the old tabs,
// moved with no behaviour change: their fate is the owner's question Q2
// (IN-6). The Email tab is gone. A mailbox is a row in Connections, and the
// Email app connects and manages it.
//
// AI agents (WS-55 EA-4b, D-EA-9) is the one view every member sees. The app
// is ungated since 2026-10-14, so each view gates itself: the four org views
// need `feature:integrations`, and a member without it sees AI agents only
// (`external_agent_access.md` §5.4).

export type ViewId = "connections" | "keys" | "plugins" | "skills" | "agents";

export interface RailView {
  id: ViewId;
  label: string;
  icon: string;
  /** The scope line beside the name. */
  line: string;
  /** True when the view needs `feature:integrations`. */
  org: boolean;
}

export const INTEGRATION_VIEWS: RailView[] = [
  { id: "connections", label: "Connections", icon: "Plug", line: "Every tool your company has connected", org: true },
  { id: "keys", label: "API keys", icon: "KeyRound", line: "Service keys for your company", org: true },
  { id: "plugins", label: "Plugins", icon: "Puzzle", line: "Tool plugins for agents", org: true },
  { id: "skills", label: "Skills", icon: "Sparkles", line: "Agent skill families and token costs", org: true },
  { id: "agents", label: "AI agents", icon: "Bot", line: "Outside AI agents that act as you", org: false },
];

/** The views a member may open. Without `feature:integrations`, AI agents only. */
export function visibleViews(hasFeature: boolean): RailView[] {
  return INTEGRATION_VIEWS.filter((v) => hasFeature || !v.org);
}

/** The view a member opens on when `?view=` names none, or one they lack. */
export function homeView(hasFeature: boolean): ViewId {
  return hasFeature ? "connections" : "agents";
}

/**
 * `?view=` to a view the member may open. An unknown value, or a view the
 * member lacks, opens their home view. `hasFeature` defaults to true, so a
 * caller that does not know the access yet keeps the old answer.
 */
export function parseView(value: string | null | undefined, hasFeature: boolean = true): ViewId {
  return visibleViews(hasFeature).find((v) => v.id === value)?.id ?? homeView(hasFeature);
}

export function viewHref(view: ViewId, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams(extra);
  if (view !== "connections") q.set("view", view);
  const s = q.toString();
  return s ? `/integrations?${s}` : "/integrations";
}

export function viewLine(view: ViewId): string {
  return INTEGRATION_VIEWS.find((v) => v.id === view)!.line;
}
