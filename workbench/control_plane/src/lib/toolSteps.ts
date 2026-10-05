/**
 * toolSteps — one readable line for each step an agent takes.
 *
 * The chat's working trail (`ThinkingContainer`) draws one row per tool call.
 * A row used to read "Edited Create Task" or "Used Task Detail": the tool's
 * code name, title-cased, behind a verb guessed from a regex. The owner asked
 * for the trail the way a person would say it (2026-10-05): "Read file
 * outputs/…", "Ran a script in the sandbox", "Asked email-assistant",
 * "Created a task", "Wrote report.md".
 *
 * This module is that vocabulary, and the ONLY place a tool name becomes a
 * sentence. `ThinkingContainer` is its one renderer, and every chat surface
 * draws through it: `/chat`, the Projects rail, the Tasks rail and the email
 * rail. So the words are the same everywhere, with no second copy to drift.
 *
 * It only RE-WORDS what the stream already carries. The arguments it reads
 * (a path, a command, an agent name, a query) are the same ones the trail
 * showed before, from the same `ToolEvent`. There is no new data path.
 *
 * Fence: `toolSteps.test.ts`.
 */

import type { ToolEvent } from "@/components/MarkdownMessage";

/** What a step does. Drives the icon on the trail's axis. */
export type StepKind = "read" | "edit" | "search" | "run" | "delegate" | "think" | "other";

/** Where a step is. `error` on the wire is `failed` on screen. */
export type StepStatus = "running" | "done" | "failed";

export interface ToolStep {
  kind: StepKind;
  status: StepStatus;
  /** The sentence: "Ran a script in the sandbox", "Created a task". */
  label: string;
  /**
   * What the step acted on, when it is short enough to read on one line: a
   * file path, a command's first line, a query. Absent when the label
   * already says it ("Asked email-assistant", "Wrote report.md").
   */
  target?: string;
}

/** The longest target drawn on the one-line row. The detail shows the rest. */
export const TARGET_CAP = 120;

/** The most output a step's detail draws. The stream itself caps a result at
 *  2,000 characters, but streamed partial output accumulates without a cap. */
export const OUTPUT_CAP = 4000;

type Verb = readonly [running: string, done: string];

/** One verb, two tenses. The trail shows the first while the step runs. */
const V = {
  read: ["Reading", "Read"],
  wrote: ["Writing", "Wrote"],
  edit: ["Editing", "Edited"],
  ran: ["Running", "Ran"],
  ask: ["Asking", "Asked"],
  search: ["Searching", "Searched"],
  create: ["Creating", "Created"],
  update: ["Updating", "Updated"],
  delete: ["Deleting", "Deleted"],
  archive: ["Archiving", "Archived"],
  restore: ["Restoring", "Restored"],
  move: ["Moving", "Moved"],
  merge: ["Merging", "Merged"],
  link: ["Linking", "Linked"],
  unlink: ["Unlinking", "Unlinked"],
  assign: ["Assigning", "Assigned"],
  complete: ["Completing", "Completed"],
  defer: ["Deferring", "Deferred"],
  watch: ["Watching", "Watched"],
  comment: ["Commenting on", "Commented on"],
  list: ["Listing", "Listed"],
  draw: ["Drawing", "Drew"],
  save: ["Saving", "Saved"],
  set: ["Setting", "Set"],
  add: ["Adding", "Added"],
  check: ["Checking", "Checked"],
  plan: ["Planning", "Planned"],
  capture: ["Capturing", "Captured"],
  triage: ["Triaging", "Triaged"],
  revert: ["Reverting", "Reverted"],
  open: ["Opening", "Opened"],
  find: ["Looking for", "Looked for"],
  rank: ["Ranking", "Ranked"],
  build: ["Building", "Built"],
  mark: ["Marking", "Marked"],
  recall: ["Recalling", "Recalled"],
  remember: ["Remembering", "Remembered"],
  use: ["Using", "Used"],
} as const satisfies Record<string, Verb>;

/**
 * Steps whose words cannot be derived from the name: a noun-only name
 * (`task_detail`), a noun-first name (`report_save`), or a name whose object
 * reads badly as written (`people_for`). Keys are the BARE tool name.
 *
 * Unknown names fall through to {@link fromVerbFirstName}, and then to the
 * generic "Used <name>" row, so a new tool still draws — only less well.
 */
const NAMED: Record<string, { kind: StepKind; verb: Verb; object: string }> = {
  // ── Platform tools (every agent) ─────────────────────────────────────────
  manage_todo_list: { kind: "think", verb: V.update, object: "the plan" },
  save_memory: { kind: "edit", verb: V.save, object: "a memory" },
  remember: { kind: "edit", verb: V.remember, object: "a fact" },
  recall_timeline: { kind: "search", verb: V.recall, object: "earlier notes" },
  ask_questions: { kind: "other", verb: V.ask, object: "you a question" },
  ask_user: { kind: "other", verb: V.ask, object: "you a question" },
  request_confirmation: { kind: "other", verb: V.ask, object: "you to confirm" },
  emit_generative_ui: { kind: "other", verb: V.draw, object: "a card" },
  request_network_access: { kind: "other", verb: V.ask, object: "for network access" },
  web_search: { kind: "search", verb: V.search, object: "the web" },
  // ── Projects (skill-projects): the reads ─────────────────────────────────
  projects_tree: { kind: "read", verb: V.read, object: "the project tree" },
  project_summary: { kind: "read", verb: V.read, object: "a project summary" },
  task_detail: { kind: "read", verb: V.read, object: "a task" },
  my_work: { kind: "read", verb: V.read, object: "your work" },
  my_task: { kind: "read", verb: V.read, object: "your task" },
  my_contexts: { kind: "read", verb: V.read, object: "your contexts" },
  my_led_projects: { kind: "read", verb: V.read, object: "the projects you lead" },
  people_for: { kind: "search", verb: V.find, object: "who could take it" },
  vocabulary: { kind: "read", verb: V.read, object: "the project's vocabulary" },
  calendar: { kind: "read", verb: V.read, object: "the calendar" },
  intake_queue: { kind: "read", verb: V.read, object: "the intake queue" },
  notifications: { kind: "read", verb: V.read, object: "notifications" },
  watchers: { kind: "read", verb: V.read, object: "the watchers" },
  project_access: { kind: "read", verb: V.read, object: "who may see a project" },
  project_views: { kind: "read", verb: V.read, object: "the saved views" },
  recurrence: { kind: "read", verb: V.read, object: "a repeat rule" },
  team_capacity: { kind: "read", verb: V.read, object: "the team's capacity" },
  fit_for_task: { kind: "search", verb: V.rank, object: "who fits a task" },
  rebalance: { kind: "search", verb: V.find, object: "a fairer split" },
  task_dataset: { kind: "search", verb: V.build, object: "a table of tasks" },
  status_report: { kind: "edit", verb: V.wrote, object: "a status report" },
  open_in_app: { kind: "other", verb: V.open, object: "it in the app" },
  analytics_stuck: { kind: "search", verb: V.check, object: "what is stuck" },
  analytics_load: { kind: "search", verb: V.check, object: "who holds the work" },
  analytics_throughput: { kind: "search", verb: V.check, object: "the throughput" },
  analytics_finished: { kind: "search", verb: V.check, object: "what finished" },
  analytics_outlook: { kind: "search", verb: V.check, object: "the outlook" },
  report_list: { kind: "read", verb: V.list, object: "the saved reports" },
  report_render: { kind: "read", verb: V.read, object: "a saved report" },
  report_save: { kind: "edit", verb: V.save, object: "a report" },
  report_delete: { kind: "edit", verb: V.delete, object: "a report" },
  propose_plan: { kind: "think", verb: V.plan, object: "a project" },
  mark_notifications_read: { kind: "edit", verb: V.mark, object: "notifications read" },
  set_my_overlay: { kind: "edit", verb: V.update, object: "your view of a task" },
  set_status_set: { kind: "edit", verb: V.update, object: "the statuses" },
  bulk_update: { kind: "edit", verb: V.update, object: "tasks in bulk" },
  revert_activity: { kind: "edit", verb: V.revert, object: "a change" },
  unarchive_task: { kind: "edit", verb: V.restore, object: "a task" },
  unarchive_project: { kind: "edit", verb: V.restore, object: "a project" },
  edit_comment: { kind: "edit", verb: V.edit, object: "a comment" },
  comment: { kind: "edit", verb: V.comment, object: "a task" },
  assign: { kind: "edit", verb: V.assign, object: "a task" },
  complete: { kind: "edit", verb: V.complete, object: "a task" },
  defer: { kind: "edit", verb: V.defer, object: "a task" },
  watch: { kind: "edit", verb: V.watch, object: "a task" },
};

/** First word of a verb-first name → its verb and its step kind. */
const VERB_FIRST: Record<string, { kind: StepKind; verb: Verb }> = {
  create: { kind: "edit", verb: V.create },
  add: { kind: "edit", verb: V.add },
  update: { kind: "edit", verb: V.update },
  edit: { kind: "edit", verb: V.edit },
  delete: { kind: "edit", verb: V.delete },
  archive: { kind: "edit", verb: V.archive },
  move: { kind: "edit", verb: V.move },
  merge: { kind: "edit", verb: V.merge },
  link: { kind: "edit", verb: V.link },
  unlink: { kind: "edit", verb: V.unlink },
  set: { kind: "edit", verb: V.set },
  save: { kind: "edit", verb: V.save },
  capture: { kind: "edit", verb: V.capture },
  triage: { kind: "edit", verb: V.triage },
  render: { kind: "other", verb: V.draw },
  list: { kind: "read", verb: V.list },
  find: { kind: "search", verb: V.search },
  get: { kind: "read", verb: V.read },
};

/** "a task", "an intake item", "tasks" — the article a plain noun needs. */
function withArticle(noun: string): string {
  if (/s$/.test(noun)) return noun;
  return /^[aeiou]/i.test(noun) ? `an ${noun}` : `a ${noun}`;
}

/** `create_personal_task` → "Created a personal task". */
function fromVerbFirstName(bare: string): { kind: StepKind; verb: Verb; object: string } | null {
  const [first, ...rest] = bare.split("_");
  const known = VERB_FIRST[first];
  if (!known || rest.length === 0) return null;
  const noun = rest.join(" ");
  // `render_*` draws a thing; `find_*` searches a set, so no article there.
  const object = first === "find" || first === "list" ? noun : withArticle(noun);
  return { ...known, object };
}

/** The bare tool name. A runtime may prefix it (`skill_projects.create_task`,
 *  `projects__create_task`); the trail keys on what the tool IS. */
export function bareToolName(name: string): string {
  const lower = name.trim().toLowerCase();
  const dotted = lower.split(/[.:/]/).pop() ?? lower;
  return dotted.replace(/^.*__/, "");
}

/** `outputs/reports/q3/report.md` → `…/q3/report.md`. */
export function shortPath(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  const short = parts.slice(-2).join("/");
  return short.length < path.length ? `…/${short}` : short;
}

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v.trim() : null;
}

/** The command a shell step ran, as one string. */
export function commandOf(args: Record<string, unknown> | undefined, name: string): string {
  const a = args ?? {};
  const direct = str(a.command) ?? str(a.cmd) ?? str(a.fullCommandText)
    ?? str(a.full_command_text) ?? str(a.script) ?? str(a.code);
  if (direct) return direct;
  const argv = a.argv ?? a.args ?? a.arguments;
  if (Array.isArray(argv) && argv.length > 0) return argv.join(" ");
  const first = Object.values(a).find((v) => typeof v === "string" && v.length > 0);
  if (typeof first === "string") return first.slice(0, TARGET_CAP);
  return name.replace(/_/g, " ");
}

function pathOf(args: Record<string, unknown> | undefined): string | null {
  const a = args ?? {};
  return str(a.path) ?? str(a.filePath) ?? str(a.file) ?? str(a.file_path)
    ?? str(a.filename) ?? str(a.target) ?? str(a.destination);
}

function oneLine(text: string): string {
  const first = text.split(/\r?\n/).find((l) => l.trim()) ?? text;
  const line = first.trim();
  return line.length > TARGET_CAP ? `${line.slice(0, TARGET_CAP - 1)}…` : line;
}

/** Is this step a shell or script run? The detail then draws a terminal. */
export function isRunStep(name: string): boolean {
  const bare = bareToolName(name);
  return /^(run_command|run_script|run_skill_script|bash|shell|exec|execute|terminal|run_in_terminal|powershell)/.test(bare)
    || /(^|_)(bash|shell|terminal|command)(_|$)/.test(bare);
}

/** Is this step a hand-off to another agent? */
export function isDelegateStep(name: string): boolean {
  const bare = bareToolName(name);
  return bare === "call_agent" || /delegate|spawn_agent|ask_agent/.test(bare);
}

function statusOf(event: Pick<ToolEvent, "status">): StepStatus {
  if (event.status === "error") return "failed";
  return event.status === "running" ? "running" : "done";
}

function phrase(verb: Verb, status: StepStatus, object: string): string {
  const v = status === "running" ? verb[0] : verb[1];
  return object ? `${v} ${object}` : v;
}

/**
 * One step, in words. Pure: the same event always gives the same line.
 *
 * The order is a ladder from most specific to least: the shell, a hand-off,
 * file reads and writes, then a name this module knows, then a verb-first
 * name, then a regex guess at the kind, and last "Used <name>".
 */
export function describeToolStep(
  event: Pick<ToolEvent, "name" | "args" | "status" | "subAgentName">,
): ToolStep {
  const status = statusOf(event);
  const bare = bareToolName(event.name);
  const args = event.args ?? {};

  // ── A script or a command in the sandbox ──────────────────────────────────
  if (isRunStep(event.name)) {
    const skill = /skill_script/.test(bare);
    const object = skill ? "a skill script" : "a script in the sandbox";
    return {
      kind: "run",
      status,
      label: phrase(V.ran, status, object),
      target: oneLine(commandOf(args, event.name)),
    };
  }

  // ── A hand-off to another agent ───────────────────────────────────────────
  if (isDelegateStep(event.name)) {
    const who = str(event.subAgentName) ?? str(args.agent_name) ?? str(args.agent)
      ?? str(args.name) ?? "another agent";
    return { kind: "delegate", status, label: phrase(V.ask, status, who) };
  }

  // ── Files ─────────────────────────────────────────────────────────────────
  const path = pathOf(args);
  if (/^(write_artifact|write_file|create_file|save_file)$/.test(bare)) {
    return {
      kind: "edit",
      status,
      label: phrase(V.wrote, status, path ? baseName(path) : "a file"),
      ...(path && path.includes("/") ? { target: shortPath(path) } : {}),
    };
  }
  if (/^(edit_file|replace_string_in_file|replace_in_file|apply_patch|patch_file|str_replace|insert_edit_into_file)$/.test(bare)) {
    return {
      kind: "edit",
      status,
      label: phrase(V.edit, status, path ? baseName(path) : "a file"),
      ...(path && path.includes("/") ? { target: shortPath(path) } : {}),
    };
  }
  if (/^(read_file|read_attachment|view_file|get_file|open_file|cat)$/.test(bare)) {
    const noun = bare === "read_attachment" ? "attachment" : "file";
    return {
      kind: "read",
      status,
      label: phrase(V.read, status, path ? `${noun} ${shortPath(path)}` : withArticle(noun)),
    };
  }
  if (/^(list_dir|list_directory|list_files|ls)$/.test(bare)) {
    return { kind: "read", status, label: phrase(V.list, status, path ? `files in ${shortPath(path)}` : "files") };
  }
  if (/^(fetch_page|fetch_url|fetch|web_fetch)$/.test(bare)) {
    const url = str(args.url);
    let host = "";
    try { host = url ? new URL(url).host : ""; } catch { host = ""; }
    return { kind: "read", status, label: phrase(V.read, status, host ? `a page on ${host}` : "a web page") };
  }

  // ── A name this module knows, then a verb-first name ──────────────────────
  const named = NAMED[bare] ?? fromVerbFirstName(bare);
  if (named) {
    const q = str(args.query) ?? str(args.q) ?? str(args.search);
    return {
      kind: named.kind,
      status,
      label: phrase(named.verb, status, named.object),
      ...(q ? { target: oneLine(q) } : {}),
    };
  }

  // ── A search with a query ─────────────────────────────────────────────────
  const q = str(args.query) ?? str(args.q) ?? str(args.pattern) ?? str(args.search);
  if (/search|grep|find|query|lookup|retrieve/.test(bare)) {
    return { kind: "search", status, label: phrase(V.search, status, q ? `for ${oneLine(q)}` : "") };
  }

  // ── Last: the name, readable ──────────────────────────────────────────────
  const readable = bare.replace(/_/g, " ");
  return {
    kind: "other",
    status,
    label: phrase(V.use, status, readable),
    ...(path ? { target: shortPath(path) } : {}),
  };
}

/** Output for a step's detail, cut to {@link OUTPUT_CAP}. `cut` is how many
 *  characters were left out, so the detail can say so. */
export function capOutput(text: string, cap = OUTPUT_CAP): { text: string; cut: number } {
  if (text.length <= cap) return { text, cut: 0 };
  return { text: text.slice(0, cap), cut: text.length - cap };
}
