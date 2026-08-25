/**
 * Tasks · the Projects LENS — `GtdItem` over `/projects/my/*`.
 *
 * Spec: `task_manager_app.md` §13 (D53) · board **WS-39 slice S3a-client** ·
 * server side: `routes/projects/personal.py`.
 *
 * ── What this module is ────────────────────────────────────────────────────
 *
 * Tasks does not own tasks any more. There is ONE store — `pm_tasks` plus the
 * per-member overlay `pm_task_personal` — and `/tasks`, `/projects` and
 * `/calendar` are three lenses on it. This file is the adapter that lets the
 * existing Tasks UI, which speaks `GtdItem` from end to end, read and write
 * that store without being rewritten: the 95 KB store above it is untouched.
 *
 * ── Why it is a separate module and not an edit to `api.ts` ────────────────
 *
 * Because both paths have to exist at once. `gtd_items` still holds every
 * task anybody has captured, and the backfill that moves them (S3b) is
 * OWNER-GATED and has not run. Re-pointing the UI today without the flag would
 * not degrade the app — it would empty it, silently and on a 200, because the
 * new store answers correctly that it holds nothing of theirs yet. So the
 * lens ships DARK (`lensEnabled()` is false unless the env says otherwise),
 * which is the expand half of R6: new readers, old tables untouched.
 *
 * ── The two traps this module is written against ───────────────────────────
 *
 * **1. A field with no home does not fail loudly — it writes a 200 and
 * disappears.** So `mapLensItem` is fenced structurally (`lens.test.ts` reads
 * `types.ts` and refuses any `GtdItem` field that is neither mapped here nor
 * named in `UNMAPPED` with a reason), and `splitPatch` THROWS on a key it
 * cannot place rather than dropping it.
 *
 * **2. The old list endpoint was unbounded; this one is capped at 100 a
 * page.** Taking the first page and calling it "my tasks" is how an app shows
 * 100 of somebody's 340 and looks perfectly healthy doing it. `fetchAll`
 * pages to exhaustion.
 */

import { projectsCall } from "@/app/projects/lib/api";

import type {
  Disposition,
  GtdItem,
  Person,
  TaskAttachment,
} from "./types";

type Raw = Record<string, unknown>;

/** `{rows, total}` — every paginated Projects list answers this shape. */
interface ListResponse {
  rows: Raw[];
  total: number;
}

// ── The flag ────────────────────────────────────────────────────────────────

/**
 * Is the Tasks app reading the one store yet?
 *
 * Default **off**, and deliberately an env var rather than a feature grant:
 * `preview`/`feature:` slugs say who may reach an app, and this says which
 * store an app reads. Revoking a grant to hide an unfinished cutover would
 * conflate the two (`launch_surface.md` §2 — "`preview` is not a permission").
 *
 * Flipping it is the owner's act, and it is not independent: it must happen
 * with or after the S3b backfill, or the first thing every member sees is an
 * empty list. Board WS-39 records the sequencing.
 */
export function lensEnabled(
  env: Record<string, string | undefined> = process.env,
): boolean {
  const raw = env.NEXT_PUBLIC_TASKS_LENS;
  return raw === "1" || raw === "true" || raw === "on";
}

// ── Mapping: the wire → GtdItem ─────────────────────────────────────────────

/**
 * `GtdItem` fields the lens deliberately does not produce, each with the
 * reason. `lens.test.ts` requires every field to be here or mapped, so this
 * list is a decision record the compiler helps keep honest — a field added to
 * `GtdItem` later cannot quietly join it.
 */
export const UNMAPPED: Readonly<Record<string, string>> = {
  provider: "D52 — there is no connector; nothing is synced from anywhere",
  accountId: "D52 — no workspace accounts",
  providerUrl: "D52 — no external task to deep-link to",
  providerStatus: "D52 — `workflowStage` is the status, and it is ours",
  syncState: "D52 — every task is local; there is nothing to be pending to",
  attachments:
    "slice 2 — `pm_task_attachments` exists and `/my/inbox` does not " +
    "project it; mapping it to [] here would read as 'no attachments'",
  origin:
    "UNDECIDED, and per-TASK rather than per-member (H-33). " +
    "`pm_tasks.source` is the nearest existing fact. Settle it before the " +
    "lens touches email-captured tasks, or their provenance is lost at the " +
    "cutover rather than at a review",
};

const text = (v: unknown): string | undefined =>
  v === null || v === undefined || v === "" ? undefined : String(v);

/**
 * Tri-state boolean. `null` (never stated) must not collapse to `false`:
 * migration 187/188 chose nullable columns precisely so "I have not decided
 * whether this is deep work" stays distinct from "it is not".
 */
const tri = (v: unknown): boolean | undefined =>
  v === null || v === undefined ? undefined : Boolean(v);

const num = (v: unknown): number | undefined =>
  v === null || v === undefined ? undefined : Number(v);

/**
 * `pm_task_assignees.assignee` is a bare email (D-PM-4), not a `{name, email}`
 * record — the directory is a different table and joining it into every list
 * read is the N+1 the projects list already refuses. So the display name is
 * the email until a caller looks the person up. Showing the address is honest;
 * showing an empty name would not be.
 */
function emailPerson(v: unknown): Person | undefined {
  const email = text(v);
  return email ? { name: email, email } : undefined;
}

/** `waiting_on` is jsonb `{name, email}` — the delegator typed both. */
function waitingPerson(v: unknown): Person | undefined {
  if (!v || typeof v !== "object") return undefined;
  const p = v as Raw;
  const name = text(p.name);
  const email = text(p.email);
  if (!name && !email) return undefined;
  return { name: name ?? email ?? "", email };
}

/**
 * One `/projects/my/*` row → the `GtdItem` the Tasks store speaks.
 *
 * The three readers (`/my/inbox`, `/my/calendar`, `/my/tasks/{id}`) return an
 * identical shape on purpose, fenced server-side by
 * `test_the_inbox_and_the_calendar_project_the_same_task_shape`. That is what
 * lets this be ONE mapper: a short reader would otherwise feed `undefined`
 * into fields the UI renders, and the surface that read from it would draw a
 * task with no stage next to one that has one.
 */
export function mapLensItem(raw: Raw): GtdItem {
  const assignees = (Array.isArray(raw.assignees) ? raw.assignees : [])
    .map(emailPerson)
    .filter(Boolean) as Person[];

  return {
    id: String(raw.id ?? ""),
    // Every task is ours now (D52). `SYNCED` described a row mirrored from a
    // connected workspace, and there are no connected workspaces.
    source: "LOCAL",
    title: String(raw.title ?? ""),
    // `pm_tasks.description` IS the notes field. One rename, and the only one
    // in this mapper — worth naming because a mapper that renames silently is
    // where the next reader stops trusting it.
    notes: text(raw.description),

    disposition: String(raw.disposition ?? "INBOX") as Disposition,
    nextAction: text(raw.next_action),
    context: text(raw.context),
    energy: (raw.energy ?? undefined) as GtdItem["energy"],
    // ⚠️ The overlay's `time_estimate_mins`, NOT `pm_tasks.estimate_mins`.
    // The task's estimate is the team's; this is mine, and they disagree
    // exactly when somebody privately thinks a job is bigger than billed.
    timeEstimateMins: num(raw.time_estimate_mins),
    isTwoMinute: Boolean(raw.is_two_minute),

    // ⚠️ `important` is the overlay's Eisenhower boolean. It is NOT
    // `pm_tasks.importance`, the shared Priority integer the Projects table
    // edits (D53.8). Reading one as the other publishes private triage.
    important: tri(raw.important),
    leveraged: tri(raw.leveraged),
    deepWork: tri(raw.deep_work),
    keptMine: tri(raw.kept_mine),

    projectId: text(raw.project_id),
    isMine: Boolean(raw.is_mine),
    waitingOn: waitingPerson(raw.waiting_on),
    delegatedAt: text(raw.delegated_at),
    expectedBy: text(raw.expected_by),
    lastNudgedAt: text(raw.last_nudged_at),
    assignee: assignees[0],
    assignees,

    workflowStage: text(raw.workflow_stage),
    sortKey: num(raw.sort_key),
    parentItemId: text(raw.parent_task_id),
    subtaskCount: raw.subtask_count == null ? 0 : Number(raw.subtask_count),
    archivedAt: text(raw.archived_at),

    dueAt: text(raw.due_at),
    isHardDate: Boolean(raw.is_hard_date),
    scheduledStart: text(raw.scheduled_start),
    scheduledEnd: text(raw.scheduled_end),
    // Unstated reads as flexible — the column default since migration 79, and
    // the reason `flexible` is nullable rather than `NOT NULL DEFAULT true`.
    flexible: raw.flexible == null ? true : Boolean(raw.flexible),
    actualStart: text(raw.actual_start),
    actualEnd: text(raw.actual_end),

    createdAt: String(raw.created_at ?? ""),
    updatedAt: String(raw.updated_at ?? ""),
    completedAt: text(raw.completed_at),
    clarifiedAt: text(raw.clarified_at),
    deferUntil: text(raw.defer_until),
  };
}

// ── Splitting a write ───────────────────────────────────────────────────────

/** Shared facts about the WORK. `PATCH /projects/tasks/{id}`. */
const TASK_KEYS: Readonly<Record<string, string>> = {
  title: "title",
  notes: "description",
  due_at: "due_at",
};

/** My practice. `PATCH /projects/tasks/{id}/personal`. */
const OVERLAY_KEYS: readonly string[] = [
  "disposition", "next_action", "context", "energy", "time_estimate_mins",
  "is_two_minute", "defer_until",
  "scheduled_start", "scheduled_end", "flexible", "is_hard_date",
  "actual_start", "actual_end",
  "important", "leveraged", "deep_work", "kept_mine", "sort_key",
  "waiting_on", "delegated_at", "expected_by", "last_nudged_at",
];

/**
 * Keys the UI still sends that the lens cannot place YET, each naming what it
 * needs. They THROW rather than being dropped.
 *
 * Dropping them is the tempting option and the wrong one: the caller gets a
 * resolved promise and a task that did not change, which is indistinguishable
 * from a save that worked. This slice is explicitly about not doing that.
 */
const NOT_YET: Readonly<Record<string, string>> = {
  workflow_stage:
    "needs a status name → `status_id` lookup against the task's project " +
    "(WS-39 S3a-client slice 2)",
  provider_status: "retired with the connector (D52) — nothing writes it",
  is_mine: "derived from `pm_task_assignees`; set assignees instead",
};

export interface SplitPatch {
  task: Record<string, unknown>;
  personal: Record<string, unknown>;
  assignees?: string[];
}

/**
 * One `GtdItem` patch → the one, two or three requests it actually is.
 *
 * A Tasks edit used to be a single `PATCH /items/{id}` because there was a
 * single row. Under one store, changing a title touches `pm_tasks` (everybody
 * assigned sees it) and changing a disposition touches `pm_task_personal`
 * (nobody else does), and conflating them is precisely the bug the overlay
 * table exists to prevent.
 */
export function splitPatch(patch: Record<string, unknown>): SplitPatch {
  const out: SplitPatch = { task: {}, personal: {} };
  for (const [key, value] of Object.entries(patch)) {
    if (value === undefined) continue;
    if (key in NOT_YET) {
      throw new Error(
        `Tasks lens: cannot write \`${key}\` — ${NOT_YET[key]}. Refusing ` +
          "rather than dropping it: a silently discarded field looks exactly " +
          "like a successful save.",
      );
    }
    if (key in TASK_KEYS) {
      out.task[TASK_KEYS[key]] = value;
    } else if (OVERLAY_KEYS.includes(key)) {
      out.personal[key] = value;
    } else if (key === "assignees") {
      out.assignees = (value as { email?: string; name: string }[])
        .map((p) => p.email ?? p.name)
        .filter(Boolean);
    } else if (key === "assignee") {
      const p = value as { email?: string; name: string };
      out.assignees = [p.email ?? p.name].filter(Boolean);
    } else if (key === "clear_assignee") {
      if (value) out.assignees = [];
    } else {
      throw new Error(
        `Tasks lens: unknown patch key \`${key}\`. Every GtdItem field has a ` +
          "`pm_*` home (task_manager_app.md §13.4a) — if this one is new, " +
          "give it one there before writing it.",
      );
    }
  }
  return out;
}

// ── Reads ───────────────────────────────────────────────────────────────────

/** The Tasks store's three list views, as `/my/inbox` query flags. */
const VIEW_FLAGS: Readonly<Record<string, string>> = {
  all: "include_deferred=true",
  done: "include_deferred=true&include_done=true",
  archive: "include_deferred=true&include_done=true&include_archived=true",
};

/** `MAX_PAGE_SIZE` in `routes/projects/core.py`. A larger ask is a 422. */
const PAGE_SIZE = 100;

/**
 * Refuse to spin forever if `total` and the rows ever disagree. 200 pages is
 * 20 000 tasks — past any real inbox, and short of a hung tab.
 */
const PAGE_LIMIT = 200;

/**
 * Every row of a paginated list, not the first hundred.
 *
 * `GET /tasks/items` was unbounded; `/projects/my/inbox` is capped, because it
 * is the same endpoint the Projects board reads. The Tasks store hydrates the
 * whole list and filters in the browser, so a lens that took page one would
 * show a member 100 of their 340 tasks — with no error, no empty state and no
 * way to tell from the UI that anything was missing.
 */
async function fetchAll(path: string, flags: string): Promise<Raw[]> {
  const rows: Raw[] = [];
  for (let page = 1; page <= PAGE_LIMIT; page += 1) {
    const res = await projectsCall<ListResponse>(
      `${path}?${flags}&page=${page}&page_size=${PAGE_SIZE}`,
    );
    rows.push(...res.rows);
    if (res.rows.length < PAGE_SIZE || rows.length >= res.total) return rows;
  }
  throw new Error(
    `Tasks lens: ${path} did not terminate after ${PAGE_LIMIT} pages — the ` +
      "server's `total` disagrees with the rows it returns.",
  );
}

/** My work, as the Tasks store wants it. `view` is one of `VIEW_FLAGS`. */
export async function lensFetchItems(view = "all"): Promise<GtdItem[]> {
  const flags = VIEW_FLAGS[view] ?? VIEW_FLAGS.all;
  return (await fetchAll("my/inbox", flags)).map(mapLensItem);
}

/**
 * One task, in the shape the list gives it.
 *
 * ⚠️ Not `GET /projects/tasks/{id}` — that answers with the task as the
 * PROJECT sees it, with no overlay at all, so a member would read their own
 * task back with their disposition, context and block missing.
 */
export async function lensGetItem(id: string): Promise<GtdItem> {
  return mapLensItem(await projectsCall<Raw>(`my/tasks/${id}`));
}

// ── Writes ──────────────────────────────────────────────────────────────────

const post = (path: string, body?: unknown) =>
  projectsCall<Raw>(path, {
    method: "POST",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

/**
 * Quick capture into my personal project.
 *
 * Two requests when there is an overlay to set, and the ORDER matters: the
 * task is created first, so a failure on the second leaves a captured thought
 * with no defer date rather than no captured thought. GTD's first discipline
 * is that capture must not be lossy.
 */
export async function lensCapture(
  title: string,
  notes?: string,
  _attachments?: TaskAttachment[],
  dates?: { deferUntil?: string; dueAt?: string; isHardDate?: boolean },
): Promise<GtdItem> {
  const created = await post("my/tasks", {
    title,
    notes: notes ?? null,
    due_at: dates?.dueAt ?? null,
  });
  const id = String(created.id ?? "");
  const overlay: Record<string, unknown> = {};
  if (dates?.deferUntil) overlay.defer_until = dates.deferUntil;
  if (dates?.isHardDate) overlay.is_hard_date = true;
  if (Object.keys(overlay).length) {
    await projectsCall<Raw>(`tasks/${id}/personal`, {
      method: "PATCH",
      body: JSON.stringify(overlay),
    });
  }
  return lensGetItem(id);
}

/**
 * A `GtdItem` edit — one to three writes, then one read back.
 *
 * The read-back is not laziness. Each write answers with its own half, and the
 * store holds whole items; stitching two partial responses in the client would
 * put a second definition of "what a task looks like" next to the one
 * `_project_task` already owns, and the two would drift.
 */
export async function lensPatchItem(
  id: string,
  patch: Record<string, unknown>,
): Promise<GtdItem> {
  const split = splitPatch(patch);

  // ⚠️ Completion is not an overlay write, and this is the one place the two
  // vocabularies genuinely collide. The Tasks store marks a task done by
  // patching `disposition: "DONE"` — which under the lens would set MY view to
  // done and leave the task open on the company board, the exact drift §13.1
  // says one store exists to prevent. §13.5 criterion 4 requires the opposite:
  // completing it in either surface completes it in both. So DONE is lifted
  // out of the overlay patch and routed through `/complete`, which moves the
  // SHARED status into the project's done lane and sets the disposition as a
  // consequence. The rest of the patch still applies.
  const completes = split.personal.disposition === "DONE";
  if (completes) delete split.personal.disposition;
  if (Object.keys(split.task).length) {
    await projectsCall<Raw>(`tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(split.task),
    });
  }
  if (Object.keys(split.personal).length) {
    await projectsCall<Raw>(`tasks/${id}/personal`, {
      method: "PATCH",
      body: JSON.stringify(split.personal),
    });
  }
  if (split.assignees) {
    await projectsCall<Raw>(`tasks/${id}/assignees`, {
      method: "PUT",
      body: JSON.stringify({ assignees: split.assignees }),
    });
  }
  // Last, so a failure here cannot leave the task closed with the edit that
  // accompanied it unsaved.
  if (completes) await post(`tasks/${id}/complete`);
  return lensGetItem(id);
}

/**
 * Tick it off — and tick it off for the project, at the same instant.
 *
 * `POST /projects/tasks/{id}/complete` moves the SHARED status into the
 * project's done lane and sets my disposition to DONE. That is the cohesion
 * one store buys, and it is a real change from the old app, where "done" was a
 * disposition on a row only I could see.
 */
export async function lensCompleteItem(id: string): Promise<GtdItem> {
  await post(`tasks/${id}/complete`);
  return lensGetItem(id);
}

/**
 * Soft-delete, with the undo intact.
 *
 * ⚠️ Deliberately NOT `DELETE /projects/tasks/{id}`, which is a HARD delete
 * that promotes the subtasks. The Tasks app's delete is reversible for an undo
 * window and `apiPurgeItem` is what finalises it — so the soft half maps onto
 * the TRASH disposition, which `/my/inbox` already filters out, and the hard
 * half onto the real DELETE. Mapping the soft delete onto the hard one would
 * make "Undo" a button that cannot work.
 *
 * TRASH is per-member, which is the right scope: trashing a task assigned to
 * me and to somebody else removes it from MY list. Their copy is their call.
 */
export async function lensTrashItem(id: string): Promise<void> {
  await projectsCall<Raw>(`tasks/${id}/personal`, {
    method: "PATCH",
    body: JSON.stringify({ disposition: "TRASH" }),
  });
}

/** Undo the soft delete — back to the inbox to be triaged again. */
export async function lensRestoreItem(id: string): Promise<GtdItem> {
  await projectsCall<Raw>(`tasks/${id}/personal`, {
    method: "PATCH",
    body: JSON.stringify({ disposition: "INBOX" }),
  });
  return lensGetItem(id);
}

/** Finalise the delete. Hard, shared, and not undoable. */
export async function lensPurgeItem(id: string): Promise<void> {
  await projectsCall<Raw>(`tasks/${id}`, { method: "DELETE" });
}

/**
 * Archive, or bring back.
 *
 * ⚠️ **A real behaviour change, and the reviewer should look at it.** The
 * projects archive refuses a task that is not CLOSED (P-3: archiving open work
 * makes it vanish while still owed). The old Tasks archive had no such guard,
 * because the row was personal. It is not personal any more — archiving hides
 * the task from the company board too — so the guard applies, and an open task
 * archived from `/tasks` now answers 422 with the category that refused it.
 */
export async function lensArchiveItem(
  id: string,
  archived: boolean,
): Promise<GtdItem> {
  await post(`tasks/${id}/${archived ? "archive" : "unarchive"}`);
  return lensGetItem(id);
}

/**
 * Hand it to somebody, and start the clock.
 *
 * Three facts in one action: they are the assignee (shared), I am waiting on
 * them (mine), and the waiting started now (mine). `delegated_at` is not
 * optional — migration 188 CHECKs that `waiting_on` has a since-when, because
 * a chase with no age cannot be scanned.
 */
export async function lensDelegateItem(
  id: string,
  body: {
    assignee: { name: string; email?: string };
    next_action?: string;
    due_at?: string;
    expected_by?: string;
  },
): Promise<GtdItem> {
  const who = body.assignee.email ?? body.assignee.name;
  await projectsCall<Raw>(`tasks/${id}/assignees`, {
    method: "PUT",
    body: JSON.stringify({ assignees: [who] }),
  });
  if (body.due_at) {
    await projectsCall<Raw>(`tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ due_at: body.due_at }),
    });
  }
  await projectsCall<Raw>(`tasks/${id}/personal`, {
    method: "PATCH",
    body: JSON.stringify({
      disposition: "WAITING",
      waiting_on: { name: body.assignee.name, email: body.assignee.email },
      delegated_at: new Date().toISOString(),
      // ⚠️ `expected_by` stays NULL unless a human actually promised a date
      // (settled 2026-08-02, §13.4). NULL means nobody promised, and the
      // overdue line falls back to the task's own `due_at`, read live. Copying
      // `due_at` in here would invent a promise and then let it go stale.
      ...(body.expected_by ? { expected_by: body.expected_by } : {}),
      ...(body.next_action ? { next_action: body.next_action } : {}),
    }),
  });
  return lensGetItem(id);
}
