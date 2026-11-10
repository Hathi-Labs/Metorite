/**
 * The links one chat message's tools printed, and how a link in the answer
 * resolves against them (KB-4b, `project-docs/specs/knowledge_base.md` §8.3).
 *
 * `search_company` prints each hit as `- «Title» (hint, date) <href>`. The
 * model then writes `[text](<href>)`. The chat draws that link as a chip only
 * when the SAME message's tool results printed the same href. So the model
 * cannot chip a path it never read. The label comes from the tool line and
 * never from the model's link text.
 *
 * Pure and framework-free, so the node-env vitest holds it.
 */

import { inAppPathname } from "@/lib/inAppLink";
import { PANES } from "@/lib/nav";

/** One printed href: its tool title when one parsed, and how many differ. */
export interface LinkChipEntry {
  /** The first title printed on the same line as the href. */
  title?: string;
  /** How many distinct titles carry this href in this message. */
  titles: number;
}

export type LinkChipIndex = Map<string, LinkChipEntry>;

export interface LinkChip {
  /** The app's name: "Projects", "Email", "WhatsApp". */
  app: string;
  /** A `PANES` icon name. */
  icon: string;
  label: string;
}

/** The part of a tool event the index reads. `ToolEvent` satisfies it. */
export interface LinkEventLike {
  result?: string;
  status?: string;
  subAgentTools?: Array<{ result?: string; status?: string }>;
}

function safeDecode(text: string): string {
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

/** One key for a link: the decoded path, then the decoded query. */
export function linkKey(href: string | undefined): string | null {
  const path = inAppPathname(href);
  if (path === null || !href) return null;
  const q = href.indexOf("?");
  if (q < 0) return path;
  const query = href.slice(q + 1).split("#")[0];
  return query ? `${path}?${safeDecode(query)}` : path;
}

/** The app a path belongs to: the pane whose href is its first segment. */
function paneOf(path: string) {
  const first = "/" + (path.split("/")[1] ?? "");
  return PANES.find((p) => p.href === first) ?? null;
}

const TITLE = /«([^»]+)»/;

function scan(text: string, seen: Map<string, Set<string>>, firstTitle: Map<string, string>): void {
  for (const line of text.split("\n")) {
    const title = TITLE.exec(line)?.[1].trim();
    for (const token of line.split(/\s+/)) {
      if (!token.startsWith("/")) continue;
      const key = linkKey(token);
      if (key === null) continue;
      const titles = seen.get(key) ?? new Set<string>();
      if (title) {
        titles.add(title);
        if (!firstTitle.has(key)) firstTitle.set(key, title);
      }
      seen.set(key, titles);
    }
  }
}

/**
 * Every in-app href that a finished tool of this message printed. A running
 * or failed tool adds none. Sub-agent tools count too: an orchestrator turn
 * that delegates keeps the search in `subAgentTools`.
 */
export function buildLinkChipIndex(events: LinkEventLike[] | undefined): LinkChipIndex {
  const seen = new Map<string, Set<string>>();
  const firstTitle = new Map<string, string>();
  const take = (e: { result?: string; status?: string }) => {
    if (e.status && e.status !== "done") return;
    if (typeof e.result !== "string") return;
    scan(e.result, seen, firstTitle);
  };
  for (const e of events ?? []) {
    take(e);
    for (const s of e.subAgentTools ?? []) take(s);
  }
  const index: LinkChipIndex = new Map();
  for (const [key, titles] of seen) {
    index.set(key, { title: firstTitle.get(key), titles: titles.size });
  }
  return index;
}

/** "My WhatsApp" is the pane, and "WhatsApp" is the app. */
function appName(label: string): string {
  return label.replace(/^My\s+/, "");
}

/**
 * The chip for a link the model wrote, or `null` when no tool printed it or
 * no pane owns it. With more than one title on the href, the label is the
 * app name. Else it is the tool's title, then the link text, then the app.
 */
export function chipForHref(
  index: LinkChipIndex | undefined,
  href: string | undefined,
  linkText?: string,
): LinkChip | null {
  if (!index || index.size === 0) return null;
  const key = linkKey(href);
  if (key === null) return null;
  const entry = index.get(key);
  if (!entry) return null;
  const pane = paneOf(key.split("?")[0]);
  if (!pane) return null;
  const app = appName(pane.label);
  const text = linkText?.trim();
  const label = entry.titles > 1 ? app : entry.title || text || app;
  return { app, icon: pane.icon, label };
}
