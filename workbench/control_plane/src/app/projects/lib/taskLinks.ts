/**
 * The task card's Links section (WS-57 TR-2): how one reference draws.
 *
 * Pure, so `taskLinks.test.ts` pins it without a DOM. The component is
 * `components/TaskLinks.tsx`.
 *
 * A row reads `[icon] title  label  [state]`. The icon says what the link is
 * and, for a pull request or an issue, where it stands, as GitHub draws it:
 * a merged pull request wears the merge icon. The colour never carries that
 * alone, because the state chip says it in words.
 */

import type { BadgeTone } from "@/components/ui/Badge";

import type { TaskRefRow } from "./api";

type Drawn = Pick<TaskRefRow, "kind" | "provider" | "state" | "title" | "label" | "url">;

/** The icon for one reference. Every name is a Lucide icon (`isKnownIcon`). */
export function refIcon(ref: Pick<Drawn, "kind" | "provider" | "state">): string {
  switch (ref.kind) {
    case "pull_request":
      if (ref.state === "merged") return "GitMerge";
      if (ref.state === "closed") return "GitPullRequestClosed";
      if (ref.state === "draft") return "GitPullRequestDraft";
      return "GitPullRequest";
    case "issue":
      return ref.state === "closed" ? "CircleCheck" : "CircleDot";
    case "commit":
      return "GitCommitHorizontal";
    case "branch":
      return "GitBranch";
    case "comment":
      return "MessageSquare";
    case "doc":
      if (ref.provider === "figma") return "PenTool";
      if (ref.provider === "notion") return "BookOpen";
      return "FileText";
    default:
      return "Link";
  }
}

/** The state chip: its words and its tone. Null when there is no state. */
export function refState(state: string | null): { label: string; tone: BadgeTone } | null {
  switch (state) {
    case "open":
      return { label: "Open", tone: "primary" };
    case "draft":
      return { label: "Draft", tone: "neutral" };
    // Merged is the done colour, and closed is the muted one (spec §6 TR-2).
    case "merged":
      return { label: "Merged", tone: "success" };
    case "closed":
      return { label: "Closed", tone: "neutral" };
    default:
      return null;
  }
}

/**
 * The row's two names. The title is the main one when GitHub gave us one or
 * the member typed one; the label (`widgets#12`, `Google Doc`) then sits
 * beside it, muted. With no title, the label is the main name.
 */
export function refNames(ref: Pick<Drawn, "title" | "label">): { main: string; aside: string | null } {
  const title = ref.title?.trim();
  if (title && title !== ref.label) return { main: title, aside: ref.label };
  return { main: ref.label, aside: null };
}

/**
 * Is this text one link to add at once? A paste of a single http(s) URL adds
 * it with no Enter. Anything else stays in the field for the member to edit.
 */
export function pastedLink(text: string): string | null {
  const value = text.trim();
  if (!value || /\s/.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? value : null;
  } catch {
    return null;
  }
}
