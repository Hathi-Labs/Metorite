"use client";

/**
 * EntityPill — a task, a project, a person, a status or a tag, named inline.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §15 (WS-27bm S9). Owner
 * request, 2026-09-24: the chat's names should be pills "rather than just
 * text".
 *
 *     <EntityPill kind="task" number="#5" label="Notification engine"
 *                 href="/projects?task=…" dot={accent} statusName="To do" />
 *     <EntityPill kind="project" label="Projects/Tasks App" href="/projects?project=…" />
 *     <EntityPill kind="person" label="Vijay" email="vijay@x.io" />
 *
 * Built on `Badge`'s shape and tones (`BADGE_BASE`, `badgeTone`), so a pill
 * and a badge are one look. Three rules:
 *
 * 1. **An icon carries the kind.** A hue is never the only signal: a task
 *    wears ListChecks, a space Layers, a folder Folder, a project
 *    FolderKanban, a person their initials, an agent Bot, a tag Tag. A status
 *    is the shared `StatusChip`, dot and name.
 * 2. **Only an in-app path links**, and it is a real anchor (`ControlLink`):
 *    a plain click navigates in this tab with `router.push`, and a modified
 *    click opens a tab as a link does everywhere else. A pill with no `href`
 *    is a plain chip and does not click.
 * 3. **A long name truncates**, and the whole name is the tooltip.
 */

import { useRouter } from "next/navigation";

import { ControlLink } from "@/components/ControlLink";
import Icon from "@/components/Icon";
import { StatusChip } from "@/components/StatusChip";
import { PersonAvatar } from "@/components/room/Identity";
import { BADGE_BASE, BADGE_SHAPE, badgeTone } from "@/components/ui/Badge";
import { categoricalAccent } from "@/lib/categorical";
import type { StatusAccent } from "@/lib/statusAccent";

export type EntityKind =
  | "task"
  | "space"
  | "folder"
  | "project"
  | "subproject"
  | "person"
  | "agent"
  | "status"
  | "tag"
  | "unknown";

/** The icon each kind wears. Exported for its test. */
export const ENTITY_ICONS: Record<EntityKind, string | null> = {
  task: "ListChecks",
  space: "Layers",
  folder: "Folder",
  project: "FolderKanban",
  subproject: "FolderKanban",
  person: null, // the initials are the icon
  agent: "Bot",
  status: null, // StatusChip draws its dot
  tag: "Tag",
  unknown: null,
};

/** The word a screen reader hears for each kind. */
const KIND_WORD: Record<EntityKind, string> = {
  task: "task",
  space: "space",
  folder: "folder",
  project: "project",
  subproject: "subproject",
  person: "person",
  agent: "agent",
  status: "status",
  tag: "tag",
  unknown: "",
};

/** A base no real page has, so the check below needs no `window`. */
const IN_APP_BASE = "https://in-app.invalid";

/**
 * True for a path inside this app: `/projects?task=…`. Never `//host`, which
 * a browser reads as another site, and never `/api/…`, which is a file or a
 * JSON answer and not a page. Exported for its test.
 *
 * The prefix test alone is not enough (S9 fix round 1). A URL parser strips a
 * tab or a newline and reads `\` as `/`, so `/\evil.com` and `/<tab>/evil.com`
 * both open another site. So the path must also resolve, against a fixed base,
 * to that same base: the check the browser itself will make, run here first.
 */
export function isInAppPath(href: string | undefined): boolean {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return false;
  let url: URL;
  try {
    url = new URL(href, IN_APP_BASE);
  } catch {
    return false;
  }
  if (url.origin !== IN_APP_BASE) return false;
  return !url.pathname.startsWith("/api/") && url.pathname !== "/api";
}

/**
 * The accessible name of a pill that links. It carries the status too: the
 * name replaces the anchor's content for a screen reader, so the dot's
 * hidden words would be lost. Exported for its test.
 */
export function pillLinkName(
  kind: EntityKind,
  label: string,
  number?: string,
  statusName?: string,
): string {
  const what = [KIND_WORD[kind], number, label].filter(Boolean).join(" ");
  return `Open ${what} in Projects${statusName ? `, status ${statusName}` : ""}`;
}

export interface EntityPillProps {
  kind: EntityKind;
  label: string;
  /** A task's `#n`, drawn muted before the title. */
  number?: string;
  /** An in-app path. Anything else draws no link. */
  href?: string;
  /** A task's status: the dot beside the title. */
  dot?: StatusAccent;
  statusName?: string;
  /** A status pill's accent. */
  accent?: StatusAccent;
  /** A person's address: the seed of their initials, and the tooltip. */
  email?: string;
}

/**
 * `px-1`, not Badge's `px-1.5` (S9 visual review). A pill sits inside running
 * text, so its padding reads as a space: "Hathi Labs , the task". The DOM
 * holds no space there (`markdownPills.test.ts`), and the plugin keeps the
 * punctuation after a pill on the same line as the pill.
 */
const SHAPE = "max-w-64 px-1 py-px text-xs leading-snug align-middle font-normal";

/**
 * A linked pill's ink (S9 visual review). The label is foreground ink, so a
 * row of links does not turn the paragraph blue under a saturated accent.
 * The icon and the `#n` keep the primary colour, and with the tint they mark
 * the pill as a link. A hover makes the tint stronger and underlines the
 * label. Exported for its test.
 */
export const LINKED_TONE = "group bg-primary/10 text-foreground hover:bg-primary/20";
export const LINKED_ACCENT = "text-primary";
export const LINKED_LABEL = "group-hover:underline underline-offset-2";

function PillBody({
  kind,
  label,
  number,
  dot,
  statusName,
  email,
  linked = false,
}: EntityPillProps & { linked?: boolean }) {
  const icon = ENTITY_ICONS[kind];
  return (
    <>
      {kind === "person" && (
        <PersonAvatar email={email || label} displayName={label} size={14} title="" />
      )}
      {icon && (
        <Icon name={icon} size={12} className={`shrink-0 ${linked ? LINKED_ACCENT : ""}`} aria-hidden />
      )}
      {number && (
        <span className={`shrink-0 ${linked ? LINKED_ACCENT : "text-muted-foreground"}`}>{number}</span>
      )}
      <span className={`min-w-0 truncate ${linked ? LINKED_LABEL : ""}`}>{label}</span>
      {dot && (
        <>
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot.dot}`} aria-hidden />
          {statusName && <span className="sr-only">, status {statusName}</span>}
        </>
      )}
    </>
  );
}

function tooltip({ kind, label, number, statusName, email }: EntityPillProps): string {
  const head = [number, label].filter(Boolean).join(" ");
  if (kind === "person" && email && email !== label) return `${label} · ${email}`;
  return statusName ? `${head} · ${statusName}` : head;
}

function toneOf(props: EntityPillProps, linked: boolean): string {
  if (props.kind === "tag") return `border ${categoricalAccent(props.label).chip}`;
  return linked ? LINKED_TONE : badgeTone("neutral");
}

/** The linked pill. Its own component, so only a link asks for the router. */
function LinkedPill(props: EntityPillProps & { href: string }) {
  const router = useRouter();
  return (
    <ControlLink
      href={props.href}
      onActivate={() => router.push(props.href)}
      aria-label={pillLinkName(props.kind, props.label, props.number, props.statusName)}
      title={tooltip(props)}
      className={`${BADGE_BASE} ${toneOf(props, true)} ${SHAPE} no-underline`}
    >
      <PillBody {...props} linked />
    </ControlLink>
  );
}

export default function EntityPill(props: EntityPillProps) {
  if (props.kind === "status" && props.accent) {
    return <StatusChip accent={props.accent} label={props.label} className="align-middle" />;
  }
  if (props.href && isInAppPath(props.href)) return <LinkedPill {...props} href={props.href} />;
  return (
    // `BADGE_SHAPE`, not `BADGE_BASE`: a chip that does not click must not
    // wear the hover layer that says it does.
    <span title={tooltip(props)} className={`${BADGE_SHAPE} ${toneOf(props, false)} ${SHAPE}`}>
      <PillBody {...props} />
    </span>
  );
}
