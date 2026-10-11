// ── The rail of views (spec: integrations_console.md §7.2, IN-2) ───────────
//
// Connections is the console. API keys, Plugins and Skills are the old tabs,
// moved with no behaviour change: their fate is the owner's question Q2
// (IN-6). The Email tab is gone. A mailbox is a row in Connections, and the
// Email app connects and manages it.

export type ViewId = "connections" | "keys" | "plugins" | "skills";

export interface RailView {
  id: ViewId;
  label: string;
  icon: string;
  /** The scope line beside the name. */
  line: string;
}

export const INTEGRATION_VIEWS: RailView[] = [
  { id: "connections", label: "Connections", icon: "Plug", line: "Every tool your company has connected" },
  { id: "keys", label: "API keys", icon: "KeyRound", line: "Service keys for your company" },
  { id: "plugins", label: "Plugins", icon: "Puzzle", line: "Tool plugins for agents" },
  { id: "skills", label: "Skills", icon: "Sparkles", line: "Agent skill families and token costs" },
];

/** `?view=` to a view. An unknown value opens Connections. */
export function parseView(value: string | null | undefined): ViewId {
  return INTEGRATION_VIEWS.find((v) => v.id === value)?.id ?? "connections";
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
