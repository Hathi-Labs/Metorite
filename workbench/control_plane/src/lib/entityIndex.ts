/**
 * The entities one chat message's tools returned, and how a name in the
 * answer resolves against them.
 *
 * Spec: `project-docs/specs/projects_ai_chat.md` §15 (WS-27bm S9).
 *
 * The Projects assistant writes names in «guillemets», the way its tools
 * print them. `remarkEntityPills.ts` turns each one into a pill, and this
 * module decides what the pill IS: a task that opens, a project that opens, a
 * person, a status, a tag, or a plain neutral chip.
 *
 * ⚠️ **A pill links only on exactly one match.** The index is built from the
 * SAME message's tool results and from nothing else, so the model cannot link
 * a name to a row it never read. Two tasks with one title, or a task number
 * that two roots share (`#n` is unique per root only), resolve to a neutral
 * chip that does not click. A wrong link is worse than no link.
 *
 * Pure and framework-free, so the node-env vitest can hold it.
 */

import { classify } from "@/app/projects/lib/assignees";
import { personLabel } from "@/app/projects/lib/grouping";
import { STATUS_CATEGORIES } from "@/lib/statusCategory";
import {
  parseProjectRows,
  parseTaskRows,
  type ProjectLevel,
} from "@/lib/projectToolRows";

// ── The index ────────────────────────────────────────────────────────────────

export interface TaskEntity {
  id: string;
  number: string;
  title: string;
  status?: string;
  category?: string;
}

export interface ProjectEntity {
  id: string;
  name: string;
  level: ProjectLevel;
}

export interface EntityIndex {
  tasks: TaskEntity[];
  projects: ProjectEntity[];
  /** Lowercased email → the display name a tool printed, or "". */
  people: Map<string, string>;
  /** Lowercased status name → the name as printed and its category. */
  statuses: Map<string, { name: string; category?: string }>;
  /** Lowercased tag name → the name as printed. */
  tags: Map<string, string>;
}

/** The part of a tool event the index reads. `ToolEvent` satisfies it. */
export interface ToolResultLike {
  result?: string;
  status?: string;
}

export const EMPTY_INDEX: EntityIndex = {
  tasks: [],
  projects: [],
  people: new Map(),
  statuses: new Map(),
  tags: new Map(),
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const FULL_ID = /^\s*full_id:\s*([0-9a-f-]{36})\s*$/i;
const CATEGORIES: ReadonlySet<string> = new Set(STATUS_CATEGORIES);

/** One key for a name: case and runs of space do not make two names. */
export function nameKey(text: string): string {
  return text.trim().replace(/\s+/g, " ").toLowerCase();
}

function isEmail(text: string): boolean {
  return classify(text) === "person";
}

/** `status «In progress» · in_progress · …` → the name and the category. */
function statusOf(meta: string): { status?: string; category?: string } {
  const m = meta.match(/(?:^|·\s*)status\s+«([^»]*)»(?:\s*·\s*([a-z_]+))?/);
  if (!m) return {};
  const category = m[2] && CATEGORIES.has(m[2]) ? m[2] : undefined;
  return { status: m[1] || undefined, category };
}

function addPerson(index: EntityIndex, email: string, name = ""): void {
  const key = email.trim().toLowerCase();
  if (!isEmail(key)) return;
  // A "name" that is itself an address is no name: a capacity row prints the
  // address twice when the directory does not know the person.
  const label = isEmail(name.trim()) ? "" : name.trim();
  const known = index.people.get(key);
  if (!known) index.people.set(key, label);
}

function addTask(index: EntityIndex, task: TaskEntity): void {
  if (!UUID.test(task.id)) return;
  const existing = index.tasks.find((t) => t.id === task.id);
  if (existing) {
    existing.status ??= task.status;
    existing.category ??= task.category;
    return;
  }
  index.tasks.push(task);
}

/** Read one tool result into the index. */
function readResult(index: EntityIndex, result: string): void {
  // Task rows: `- #<n> «title» · facts` + `full_id:`.
  for (const row of parseTaskRows(result)) {
    const { status, category } = statusOf(row.meta);
    addTask(index, { id: row.id, number: row.number, title: row.title, status, category });
    if (status) {
      const key = nameKey(status);
      if (!index.statuses.has(key)) index.statuses.set(key, { name: status, category });
    }
    for (const m of row.meta.matchAll(/«([^»]*)»/g)) {
      if (isEmail(m[1])) addPerson(index, m[1]);
    }
  }

  // Project rows: `- «name» [level]` + `full_id:`.
  for (const row of parseProjectRows(result)) {
    if (!UUID.test(row.id)) continue;
    if (index.projects.some((p) => p.id === row.id)) continue;
    index.projects.push({ id: row.id, name: row.name, level: row.level });
  }

  const lines = result.split("\n");
  let section = "";
  for (let k = 0; k < lines.length; k++) {
    const line = lines[k];

    // A receipt or a detail: `Task #5 «Title»`, `Commented on #7 «x» (…).`,
    // with the id on the next line. Exactly one task reference on the line,
    // or the id could belong to either.
    const next = lines[k + 1]?.match(FULL_ID);
    if (next) {
      const refs = [...line.matchAll(/(#\d+)\s+«([^»]*)»/g)];
      if (refs.length === 1 && !/^\s*-\s*#/.test(line)) {
        addTask(index, { id: next[1], number: refs[0][1], title: refs[0][2] || "(untitled)" });
      }
    }

    // `vocabulary` prints its words in sections.
    const header = line.match(/^(Statuses|Types|Tags|Custom fields) \(\d+\):/);
    if (header) {
      section = header[1];
      continue;
    }
    if (!line.startsWith("- ")) {
      if (!line.startsWith(" ")) section = "";
    } else if (section === "Statuses") {
      const s = line.match(/^- «([^»]+)» \[([a-z_]+)\]/);
      if (s) {
        const category = CATEGORIES.has(s[2]) ? s[2] : undefined;
        index.statuses.set(nameKey(s[1]), { name: s[1], category });
      }
    } else if (section === "Tags") {
      const g = line.match(/^- «([^»]+)»/);
      if (g) index.tags.set(nameKey(g[1]), g[1]);
    }

    // A name for an address, in the three shapes the reads print (S9 round 2):
    // - `- «Name» · assignee «email»`: `people_for`, `team_capacity`,
    //   `rebalance` and `fit_for_task`, at any indent;
    // - `- «email» · «Name»`: `people_for` with `emails=`;
    // - `«Name» («email»)`: the holders of an at-risk task in capacity.
    const picker = line.match(/^\s*- «([^»]+)» · assignee «([^»]+)»/);
    if (picker) addPerson(index, picker[2], picker[1]);
    for (const m of line.matchAll(/«([^»]+)» \(«([^»]+)»\)/g)) addPerson(index, m[2], m[1]);
    const named = line.match(/^- «([^»]+)» · «([^»]+)»\s*$/);
    if (named && isEmail(named[1]) && !isEmail(named[2])) addPerson(index, named[1], named[2]);

    // A project's lead, and any other fenced address.
    for (const m of line.matchAll(/«([^»]*)»/g)) {
      if (isEmail(m[1])) addPerson(index, m[1]);
    }
  }
}

/**
 * Build the index from one message's tool events. Only a finished tool
 * counts: a running one has no result yet, and a failed one printed an error.
 */
export function buildEntityIndex(events: readonly ToolResultLike[] | undefined): EntityIndex {
  const index: EntityIndex = {
    tasks: [],
    projects: [],
    people: new Map(),
    statuses: new Map(),
    tags: new Map(),
  };
  for (const e of events ?? []) {
    if (e.status && e.status !== "done") continue;
    if (typeof e.result !== "string" || !e.result) continue;
    readResult(index, e.result);
  }
  return index;
}

// ── Resolution ───────────────────────────────────────────────────────────────

export type Resolution =
  | {
      kind: "task";
      number: string;
      title: string;
      /** Present only on exactly one match. */
      href?: string;
      status?: string;
      category?: string;
    }
  | { kind: "project"; name: string; level: ProjectLevel; href: string }
  | { kind: "person"; label: string; email?: string; agent: boolean }
  | { kind: "status"; name: string; category?: string }
  | { kind: "tag"; name: string }
  | { kind: "unknown"; text: string; ambiguous: boolean };

export function taskHref(id: string): string {
  return `/projects?task=${encodeURIComponent(id)}`;
}

export function projectHref(id: string): string {
  return `/projects?project=${encodeURIComponent(id)}`;
}

function distinct<T>(items: T[], key: (item: T) => string): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const k = key(item);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

/**
 * What a pill is. `number` is the `#n` the answer wrote before the name, when
 * it wrote one: then the pill is a task, and it links only when exactly one
 * task has that number AND that title.
 */
export function resolveEntity(index: EntityIndex, text: string, number?: string): Resolution {
  const shown = text.trim().replace(/\s+/g, " ");
  const key = nameKey(shown);

  if (number) {
    const hits = distinct(
      index.tasks.filter((t) => t.number === number && nameKey(t.title) === key),
      (t) => t.id,
    );
    if (hits.length === 1) {
      const t = hits[0];
      return {
        kind: "task",
        number,
        title: shown,
        href: taskHref(t.id),
        status: t.status,
        category: t.category,
      };
    }
    return { kind: "task", number, title: shown };
  }

  const who = classify(shown);
  if (who === "person") {
    const email = shown.toLowerCase();
    const name = index.people.get(email);
    return { kind: "person", label: name || personLabel(email), email, agent: false };
  }
  if (who === "agent") {
    return { kind: "person", label: personLabel(shown.toLowerCase()), agent: true };
  }

  const tasks = distinct(index.tasks.filter((t) => nameKey(t.title) === key), (t) => t.id);
  const projects = distinct(index.projects.filter((p) => nameKey(p.name) === key), (p) => p.id);
  const people = [...index.people.entries()].filter(([, name]) => name && nameKey(name) === key);
  const status = index.statuses.get(key);
  const tag = index.tags.get(key);
  const total = tasks.length + projects.length + people.length + (status ? 1 : 0) + (tag ? 1 : 0);

  if (total !== 1) return { kind: "unknown", text: shown, ambiguous: total > 1 };
  if (tasks.length === 1) {
    const t = tasks[0];
    return {
      kind: "task",
      number: t.number,
      title: shown,
      href: taskHref(t.id),
      status: t.status,
      category: t.category,
    };
  }
  if (projects.length === 1) {
    const p = projects[0];
    return { kind: "project", name: shown, level: p.level, href: projectHref(p.id) };
  }
  if (people.length === 1) {
    return { kind: "person", label: people[0][1], email: people[0][0], agent: false };
  }
  if (status) return { kind: "status", name: status.name, category: status.category };
  return { kind: "tag", name: tag ?? shown };
}
