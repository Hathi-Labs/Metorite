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
 *
 * ── S6a: the CRUD tail (my_tasks_cutover.md §5 S6a, 2026-09-23) ───────────
 *
 * Group C of `api.ts` follows the spine through here. Four decisions the spec
 * left open, each recorded once:
 *
 * * **An attachment needs a task.** `pm_task_attachments` hangs off a task,
 *   so a file picked BEFORE capture cannot be uploaded yet. `lensStageAttachment`
 *   returns a descriptor holding the `File` (and an object URL for the chip),
 *   and `lensCapture` creates the task FIRST and then uploads each held file —
 *   the smallest change that keeps the composer untouched. A pasted LINK has
 *   no home in that table; it is appended to the notes, which is where "for
 *   more context later" lives under one store.
 * * **Subtask rows are project-shaped.** `GET /projects/tasks?parent_task_id=`
 *   carries no overlay, so `mapLensItem` cannot state a disposition for them.
 *   The checklist reads `disposition === "DONE"`, so DONE is derived from
 *   `completed_at` — the one fact the shared row does hold — and nothing else
 *   is invented.
 * * **Bulk DONE is N completions.** `POST /projects/tasks/bulk` refuses
 *   `disposition: "DONE"` by design (§13.5a decision 1); the lens completes
 *   each task through `/complete` and reads the selection back.
 * * **`workflow_stage` is a NAME, resolved against the task's project**
 *   (§4.6). `splitPatch` no longer throws on it: it lands in `stage`, and
 *   `lensPatchItem` resolves it to a `status_id` through `lensStatuses`. A
 *   name that matches nothing throws with the valid names listed.
 */

import { projectsCall } from "@/app/projects/lib/api";

import type { OrganizeBody, ProviderTaskDetail, StatusCatalog } from "./api";
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
  env?: Record<string, string | undefined>,
): boolean {
  // ⚠️ The LITERAL member expression is the only form Next inlines into the
  // browser bundle. `env.NEXT_PUBLIC_X` off a defaulted `env = process.env`
  // is NOT inlined: in a browser `process.env` is the `{}` polyfill, so the
  // flag reads undefined and the lens stays off whatever the box is set to.
  //
  // This one is the D53 cutover switch, so the failure is worse than a
  // hidden pane. Flip it on the box, watch nothing happen, and the obvious
  // reading is "the S3b backfill did not work" rather than "the flag was
  // never readable". Measured 2026-09-23: neither flag was set on the box,
  // so nothing was broken yet — which is why this is fixed BEFORE the flip.
  //
  // Tests pass an env object; the app passes nothing and hits the literal.
  // `src/lib/publicFlags.test.ts` holds every flag reader to this shape.
  const raw = env ? env.NEXT_PUBLIC_TASKS_LENS : process.env.NEXT_PUBLIC_TASKS_LENS;
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
    "`/my/inbox` does not project `pm_task_attachments`, and mapping it to " +
    "[] here would read as 'no attachments'. The detail panel reads them " +
    "through `lensItemDetail` (S6a) — per task, when opened, not per row",
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
    projectName: text(raw.project_name),
    isTriaged: tri(raw.is_triaged),
    assignedBy: text(raw.assigned_by),
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
  provider_status: "retired with the connector (D52) — nothing writes it",
  is_mine: "derived from `pm_task_assignees`; set assignees instead",
};

export interface SplitPatch {
  task: Record<string, unknown>;
  personal: Record<string, unknown>;
  assignees?: string[];
  /**
   * A status NAME for the task's project (S6a, §4.6). Not a task field: the
   * write is `status_id`, and the id is only knowable once the project is —
   * `lensPatchItem` resolves it through `lensSetStage`.
   */
  stage?: string;
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
        `My Tasks lens: cannot write \`${key}\` — ${NOT_YET[key]}. Refusing ` +
          "rather than dropping it: a silently discarded field looks exactly " +
          "like a successful save.",
      );
    }
    if (key in TASK_KEYS) {
      out.task[TASK_KEYS[key]] = value;
    } else if (OVERLAY_KEYS.includes(key)) {
      out.personal[key] = value;
    } else if (key === "workflow_stage") {
      out.stage = String(value);
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
        `My Tasks lens: unknown patch key \`${key}\`. Every GtdItem field has a ` +
          "`pm_*` home (task_manager_app.md §13.4a) — if this one is new, " +
          "give it one there before writing it.",
      );
    }
  }
  return out;
}

// ── Routes ──────────────────────────────────────────────────────────────────

/**
 * Every `my/*` door the lens opens, spelled the way the gateway serves it.
 *
 * `{task_id}` is the ROUTE's own placeholder, kept verbatim so that
 * `tests/unit/test_client_route_contract.py` — which reads these literals off
 * this file and compares them with the mounted routes — sees the whole path,
 * `organize` included. A door renamed on one side fails there.
 */
const MY_ROUTES: Readonly<Record<string, string>> = {
  inbox: "my/inbox",
  capture: "my/tasks",
  batch: "my/tasks/batch",
  task: "my/tasks/{task_id}",
  organize: "my/tasks/{task_id}/organize",
  project: "my/project",
  // S6b — a member's own categories (`routes/projects/personal.py`, PR #391).
  areas: "my/areas",
  area: "my/areas/{area_id}",
  // S6e — the projects I lead (`routes/projects/personal.py`).
  led: "my/led",
};

const at = (template: string, id: string): string =>
  template.replace("{task_id}", id).replace("{area_id}", id);

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
    `My Tasks lens: ${path} did not terminate after ${PAGE_LIMIT} pages — the ` +
      "server's `total` disagrees with the rows it returns.",
  );
}

/** My work, as the Tasks store wants it. `view` is one of `VIEW_FLAGS`. */
export async function lensFetchItems(view = "all"): Promise<GtdItem[]> {
  const flags = VIEW_FLAGS[view] ?? VIEW_FLAGS.all;
  return (await fetchAll(MY_ROUTES.inbox, flags)).map(mapLensItem);
}

/**
 * One task, in the shape the list gives it.
 *
 * ⚠️ Not `GET /projects/tasks/{id}` — that answers with the task as the
 * PROJECT sees it, with no overlay at all, so a member would read their own
 * task back with their disposition, context and block missing.
 */
export async function lensGetItem(id: string): Promise<GtdItem> {
  return mapLensItem(await projectsCall<Raw>(at(MY_ROUTES.task, id)));
}

// ── Continuity with Projects (S6e, my_tasks_cutover.md §4.8) ────────────────

/**
 * The rows I have never looked at — assigned to me on a board, with no
 * overlay row of mine. `GET /projects/my/inbox?untriaged=true`, the same
 * door as the list with one flag, so there is no second membership query.
 * Each row carries `assignedBy`.
 */
export async function lensFetchUntriaged(): Promise<GtdItem[]> {
  return (await fetchAll(MY_ROUTES.inbox, "untriaged=true")).map(mapLensItem);
}

/** A project I lead, as `/projects/my/led` answers it. */
export interface LensLedProject {
  id: string;
  name: string;
  taskPrefix?: string;
  /** Everybody's open work on it — not archived, not in a closed lane. */
  openTasks: number;
  /** MY open tasks in it, in the inbox's shape (overlay included). */
  myTasks: GtdItem[];
}

/**
 * The projects where I am the lead. A project with no task assigned to me
 * is invisible through the membership fragment and lists here anyway —
 * leading it is the fact (§4.8 point 1).
 */
export async function lensFetchLed(): Promise<LensLedProject[]> {
  const res = await projectsCall<ListResponse>(MY_ROUTES.led);
  return rowsOf(res).map((r) => ({
    id: String(r.id ?? ""),
    name: String(r.name ?? ""),
    taskPrefix: text(r.task_prefix),
    openTasks: num(r.open_tasks) ?? 0,
    myTasks: (Array.isArray(r.my_tasks) ? (r.my_tasks as Raw[]) : []).map(
      mapLensItem,
    ),
  }));
}

/**
 * The VIEWER's own overlay on one task, for the Projects task panel's
 * disposition chip (§4.8 point 4) — or null when they hold none, which the
 * gateway answers as a 404 because the task is not "theirs" through the
 * fragment. Nothing about anybody else's overlay: the route resolves the
 * caller from the session and has no `?member=`.
 */
export async function lensMyOverlay(
  id: string,
): Promise<Pick<GtdItem, "disposition" | "context" | "isTriaged"> | null> {
  try {
    const item = await lensGetItem(id);
    if (!item.isTriaged && !item.context) return null;
    return { disposition: item.disposition, context: item.context, isTriaged: item.isTriaged };
  } catch {
    return null;
  }
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
  attachments?: TaskAttachment[],
  dates?: { deferUntil?: string; dueAt?: string; isHardDate?: boolean },
): Promise<GtdItem> {
  // A pasted link has no row of its own under one store (`pm_task_attachments`
  // is the FILE registry), so it rides in the notes — which is where "keep it
  // for context later" lives. Files wait for the task to exist, below.
  const links = (attachments ?? []).filter((a) => a.kind === "link" && !a.file);
  const body = [
    notes,
    links.length ? links.map((l) => `- ${l.name}: ${l.url}`).join("\n") : "",
  ]
    .filter(Boolean)
    .join("\n\n");
  const created = await post(MY_ROUTES.capture, {
    title,
    notes: body || null,
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
  // AFTER the task exists, and after the overlay: a failed upload leaves a
  // captured thought without its file, never a file without its thought.
  for (const a of attachments ?? []) {
    if (a.file) await lensUploadAttachment(id, a.file);
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
  if (split.stage !== undefined) {
    // §4.6. The NAME is resolved against the task's OWN project, which only
    // the task knows — one read, then the resolved `status_id` write.
    const current = await lensGetItem(id);
    if (!current.projectId) {
      throw new Error("Tasks lens: cannot set a stage on a task with no project.");
    }
    await lensSetStage(id, current.projectId, split.stage);
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

// ── The day planner ──────────────────────────────────────────────────
//
// `/calendar` reads its tasks from the shared task store, so the grid, the
// unscheduled rail and every schedule edit followed the lens the moment slice 1
// landed — with one exception, and it was the dangerous one. "Plan my day" is a
// SERVER-side computation over whichever store the endpoint reads, and it read
// the retiring one. Under the flag the UI would have shown `pm_*` tasks while
// the planner ranked and packed `gtd_items`, and the plan would have come back
// empty. A 200, no error, nothing in a log.
//
// The four below are the fix, and they are all PROPOSALS — none writes. The
// client applies an accepted plan through `apiPatchItem`, which slice 1 already
// routed, which is why there is no `apply` here to port.
//
// ⚠️ `apiAgentPlanToday` is deliberately NOT in this list. The agent surface
// has no browser and so no flag to read, and giving it a server-side one would
// mean two flags that must agree. Slice 3 (H-33). Until then an agent asked to
// plan a day on a lens deployment plans the wrong store.

/** The planner proposals, one store. Same request shape, different route. */
const PLANNER: Readonly<Record<string, string>> = {
  plan: "my/calendar/plan",
  replan: "my/calendar/replan",
  rollover: "my/calendar/rollover",
};

// ── Promotion: the personal lens reaching into the company board ────────────
//
// WS-39 S3a-client slice 5a. Everything migration 192 and the D62 guards built
// is unreachable until the Tasks app can call `move` at all — this is that
// call, plus the two reads a destination picker needs.

/**
 * The projects a task can be PROMOTED into.
 *
 * `GET /projects/nodes` and not the Tasks app's old `/projects`: the old one
 * listed `gtd_projects`, a per-user local tree. This lists the company's, which
 * is what "move it to a project" means.
 *
 * ⚠️ It returns TEAM projects only, and gets that for free rather than by
 * filtering here — `tree.py` selects `AND personal_owner IS NULL`, so since
 * migration 191 a member's whole private tree (inbox and every Area) is already
 * absent. That matters for this picker specifically: offering somebody their own
 * Areas as a promote destination would be offering a move that `D62`'s guard
 * then refuses, which is the worst kind of control — one that looks available
 * and is not.
 */
export async function lensFetchProjects(): Promise<Raw[]> {
  const res = await projectsCall<ListResponse | Raw[]>("nodes");
  return Array.isArray(res) ? res : (res.rows ?? []);
}

/**
 * The status names a project offers, for the destination's lane picker.
 *
 * ⚠️ Keyed on the PROJECT, where the old endpoint keyed on the ITEM
 * (`/items/{id}/stage-options`). That is not a translation, it is the fix for
 * something the old shape could not express: statuses are per-ROOT
 * (`load_default_status` selects `WHERE project_id = :root`), so "what stages
 * can this task be in" has no answer until you know where the task is GOING.
 * Asked of the item, the question can only describe where it already is — which
 * is precisely the wrong answer inside a move dialog.
 */
export async function lensStageOptions(projectId: string): Promise<string[]> {
  return (await lensStatuses(projectId)).map((r) => r.name);
}

/** A project's lanes, id and name, in board order. The one read behind both
 *  `lensStageOptions` (names, for a picker) and `lensSetStage` (the id, for
 *  the write). */
export async function lensStatuses(
  projectId: string,
): Promise<{ id: string; name: string }[]> {
  const res = await projectsCall<ListResponse | Raw[]>(
    `nodes/${projectId}/statuses`,
  );
  const rows = Array.isArray(res) ? res : (res.rows ?? []);
  return rows
    .map((r) => ({ id: String((r as Raw).id ?? ""), name: String((r as Raw).name ?? "") }))
    .filter((r) => r.name);
}

/**
 * Put a task in the lane called `name` in `projectId` (§4.6, closes H-62 (1)).
 *
 * The name is matched exactly first, then case-insensitively — "done" typed
 * for a lane called "Done" is the same intent, and two lanes differing only
 * in case is a board nobody should have. A name that matches nothing THROWS
 * with the valid names listed rather than landing the task in a default lane,
 * which is how a "Blocked" task used to arrive as "To do".
 */
export async function lensSetStage(
  taskId: string,
  projectId: string,
  name: string,
): Promise<void> {
  const rows = await lensStatuses(projectId);
  const wanted = name.trim();
  const hit =
    rows.find((r) => r.name === wanted) ??
    rows.find((r) => r.name.toLowerCase() === wanted.toLowerCase());
  if (!hit?.id) {
    throw new Error(
      `Tasks lens: no status named "${wanted}" in this project. Valid names: ` +
        `${rows.map((r) => r.name).join(", ") || "(none)"}.`,
    );
  }
  await projectsCall<Raw>(`tasks/${taskId}`, {
    method: "PATCH",
    body: JSON.stringify({ status_id: hit.id }),
  });
}

export interface LensMoveRequest {
  /** Destination project. Omit to leave the task where it is. */
  projectId?: string;
  /** Values for the destination's REQUIRED custom fields (migration 192). */
  customFields?: Record<string, unknown>;
  /** Assignees to set atomically with the move. `undefined` leaves them alone. */
  assignees?: string[];
}

/**
 * Move a task into a project — optionally answering its required fields and
 * assigning it, in ONE request.
 *
 * ⚠️ One request is the whole point, and it is a server property this function
 * exists to USE rather than a convenience it invents. Two calls (move, then
 * assign) can fail between them and leave a task promoted onto a team board,
 * visible to everyone, owned by nobody — a state the client cannot repair
 * because it cannot know what it was mid-way through. `move_task` does both in
 * one transaction; splitting them here would throw that away.
 *
 * ⚠️ Two refusals travel back as 422 and BOTH are actionable, so callers should
 * surface the detail rather than a generic failure:
 *   * `required_fields_missing` carries the field DEFINITIONS, so the dialog can
 *     render them as inputs in place;
 *   * the D62 guards refuse a move into somebody's personal tree, and an
 *     assignment to a colleague while the task is still in your own.
 */
export async function lensMoveTask(
  taskId: string,
  req: LensMoveRequest,
): Promise<Raw> {
  return post(`tasks/${taskId}/move`, {
    ...(req.projectId ? { project_id: req.projectId } : {}),
    ...(req.customFields ? { custom_fields: req.customFields } : {}),
    // `undefined` and `[]` are DIFFERENT here: undefined leaves the assignees
    // untouched, an empty array clears them. Collapsing the two would make
    // "promote without touching who owns it" impossible to express.
    ...(req.assignees === undefined ? {} : { assignees: req.assignees }),
  });
}

export async function lensPlan(
  kind: "plan" | "replan" | "rollover",
  req: unknown,
): Promise<Raw> {
  return projectsCall<Raw>(PLANNER[kind], {
    method: "POST",
    body: JSON.stringify(req),
  });
}

/** How long my work actually takes against what I planned. */
export async function lensEstimateStats(): Promise<Raw> {
  return projectsCall<Raw>("my/calendar/estimate-stats");
}

// ── The CRUD tail (S6a) ──────────────────────────────────────────────────────
//
// Group C of `api.ts` (my_tasks_cutover.md §3.1). The header records the four
// decisions; the functions below are each one door onto a route the Projects
// app already serves, plus the three the gateway grew for this slice
// (`my/tasks/batch`, `my/tasks/{id}/organize`, `tasks/bulk` action `personal`).

/** Rows the paginated Projects reads answer with, or a bare array. */
const rowsOf = (res: ListResponse | Raw[]): Raw[] =>
  Array.isArray(res) ? res : (res.rows ?? []);

/** `attachments.descriptor` → the chip the Tasks UI draws. */
function mapAttachment(raw: Raw): TaskAttachment {
  return {
    kind: raw.kind === "image" ? "image" : "file",
    name: String(raw.name ?? "attachment"),
    url: String(raw.url ?? ""),
    attachmentId: text(raw.attachment_id),
    mime: text(raw.mime),
    size: num(raw.size),
  };
}

/**
 * A child row as the checklist wants it. Project-shaped (no overlay), so the
 * only disposition it can honestly state is DONE, read off `completed_at`.
 */
function mapSubtask(raw: Raw): GtdItem {
  const item = mapLensItem(raw);
  if (raw.completed_at) item.disposition = "DONE";
  return item;
}

/**
 * Comments, attachments and subtasks for one task — the detail panel's read.
 * Three routes, one answer, fetched together because the panel draws all
 * three sections at once and a serial fetch would draw them one by one.
 */
export async function lensItemDetail(id: string): Promise<ProviderTaskDetail> {
  const [timeline, attachments, children] = await Promise.all([
    projectsCall<ListResponse>(
      `tasks/${id}/timeline?kind=comments&page_size=${PAGE_SIZE}`,
    ),
    projectsCall<ListResponse>(`tasks/${id}/attachments`),
    projectsCall<ListResponse>(`tasks?parent_task_id=${id}&page_size=${PAGE_SIZE}`),
  ]);
  return {
    // The timeline answers newest first; a thread reads oldest first.
    comments: rowsOf(timeline)
      .map((c) => ({
        id: String(c.id ?? ""),
        author: String(c.created_by ?? "Someone"),
        text: String(c.body ?? ""),
        createdAtMs: c.created_at ? Date.parse(String(c.created_at)) : undefined,
      }))
      .reverse(),
    attachments: rowsOf(attachments).map(mapAttachment),
    subtasks: rowsOf(children).map((s) => ({
      providerTaskId: String(s.id ?? ""),
      title: String(s.title ?? "Untitled"),
      statusType: s.completed_at ? "done" : undefined,
      assignees: (Array.isArray(s.assignees) ? s.assignees : [])
        .map(emailPerson)
        .filter(Boolean) as Person[],
    })),
  };
}

/** Many thoughts, ONE transaction — the multi-line capture box. */
export async function lensCaptureBatch(titles: string[]): Promise<GtdItem[]> {
  const res = (await post(MY_ROUTES.batch, {
    items: titles.map((title) => ({ title })),
  })) as unknown as ListResponse;
  return rowsOf(res).map(mapLensItem);
}

/** The selection, read back after a bulk write. */
const readBack = (ids: string[]): Promise<GtdItem[]> =>
  Promise.all(ids.map(lensGetItem));

/**
 * Some of a selection went through and some did not. Carries the rows that
 * DID, so a caller can show them rather than pretend nothing happened.
 */
export class LensPartialFailure extends Error {
  constructor(
    readonly items: GtdItem[],
    readonly failed: number,
    readonly total: number,
    cause?: unknown,
  ) {
    super(
      `${failed} of ${total} could not be completed` +
        (cause instanceof Error && cause.message ? `: ${cause.message}` : ""),
    );
    this.name = "LensPartialFailure";
  }
}

/**
 * One disposition onto a selection — MY overlay on each task.
 *
 * ⚠️ DONE is not an overlay write (§13.5a decision 1), and the bulk route
 * refuses it by name. Completion goes through `/complete`, per task, so the
 * board moves with the list.
 */
export async function lensBulkDispose(
  ids: string[],
  disposition: Disposition,
): Promise<GtdItem[]> {
  if (!ids.length) return [];
  if (disposition === "DONE") {
    // Settled, not raced: `Promise.all` would report the first refusal and
    // hide that the other forty completed. The ones that did are read back
    // and handed to the caller ON the error, so the list can show them.
    const settled = await Promise.allSettled(
      ids.map((id) => post(`tasks/${id}/complete`)),
    );
    const done = ids.filter((_, i) => settled[i].status === "fulfilled");
    const failed = ids.length - done.length;
    const items = await readBack(done);
    if (failed) {
      const first = settled.find((s) => s.status === "rejected") as
        | PromiseRejectedResult
        | undefined;
      throw new LensPartialFailure(items, failed, ids.length, first?.reason);
    }
    return items;
  } else {
    await post("tasks/bulk", {
      task_ids: ids,
      action: "personal",
      personal: { disposition },
    });
  }
  return readBack(ids);
}

/** Archive or restore a selection. The P-3 note on `lensArchiveItem` applies. */
export async function lensBulkArchive(
  ids: string[],
  archived: boolean,
): Promise<GtdItem[]> {
  if (!ids.length) return [];
  await post("tasks/bulk", {
    task_ids: ids,
    action: archived ? "archive" : "unarchive",
  });
  return readBack(ids);
}

/**
 * One clarify decision, one request, one transaction (S6a done-when 4).
 *
 * `account_id` is dropped: it named a connected workspace and there are none
 * (D52). `status` is a lane NAME for the destination and is honoured the §4.6
 * way — resolved against the task's project AFTER the move, so a decision
 * that promotes and names a lane lands in that lane.
 */
export async function lensOrganize(
  id: string,
  body: OrganizeBody,
): Promise<GtdItem> {
  const { account_id: _account, status, ...decision } = body;
  void _account;
  const raw = await post(at(MY_ROUTES.organize, id), decision);
  if (status) {
    const projectId = text(raw.project_id);
    if (!projectId) {
      throw new Error("Tasks lens: cannot set a stage on a task with no project.");
    }
    await lensSetStage(id, projectId, status);
    return lensGetItem(id);
  }
  return mapLensItem(raw);
}

/** A task's children, in board order. See `mapSubtask` for what they carry. */
export async function lensListSubtasks(id: string): Promise<GtdItem[]> {
  const res = await projectsCall<ListResponse>(
    `tasks?parent_task_id=${id}&sort=created_at&direction=asc&page_size=${PAGE_SIZE}`,
  );
  return rowsOf(res).map(mapSubtask);
}

let whoAmI: Promise<string> | undefined;

/**
 * My own address, from the session — needed once, to self-assign a subtask
 * (`POST /projects/tasks` assigns nobody). Memoised: it cannot change within
 * a page, and it is the same door `resolveAccess` opens.
 */
export function lensWhoAmI(): Promise<string> {
  whoAmI ??= (async () => {
    const res = await fetch("/api/auth/me", { cache: "no-store" });
    if (!res.ok) throw new Error(`Tasks lens: who am I? (${res.status})`);
    const email = text(((await res.json()) as Raw).email);
    if (!email) throw new Error("Tasks lens: the session has no email.");
    return email;
  })();
  // A failed lookup must not be cached as the answer.
  whoAmI.catch(() => {
    whoAmI = undefined;
  });
  return whoAmI;
}

/**
 * Add steps under a task: each an ordinary task in the parent's project,
 * assigned to me, created in the order given so the list reads as typed.
 */
export async function lensAddSubtasks(
  id: string,
  titles: string[],
): Promise<GtdItem[]> {
  const parent = await projectsCall<Raw>(`tasks/${id}`);
  const me = await lensWhoAmI();
  for (const title of titles) {
    const child = await post("tasks", {
      project_id: parent.project_id,
      parent_task_id: id,
      title,
    });
    await projectsCall<Raw>(`tasks/${String(child.id)}/assignees`, {
      method: "PUT",
      body: JSON.stringify({ assignees: [me] }),
    });
  }
  return lensListSubtasks(id);
}

/** Fold `id` into `targetId`. The path names the SURVIVOR (merge.py). */
export async function lensMergeInto(
  id: string,
  targetId: string,
): Promise<GtdItem> {
  await post(`tasks/${targetId}/merge`, { sources: [id] });
  return lensGetItem(targetId);
}

/** File `id` as a step of `parentId`. Answers with the PARENT, as before. */
export async function lensFileUnder(
  id: string,
  parentId: string,
): Promise<GtdItem> {
  await post(`tasks/${id}/move`, { parent_task_id: parentId });
  return lensGetItem(parentId);
}

/**
 * Hold a picked file until its task exists. The object URL is what the chip
 * previews; the `File` is what `lensCapture` uploads.
 */
export function lensStageAttachment(file: File): TaskAttachment {
  const url =
    typeof URL !== "undefined" && "createObjectURL" in URL
      ? URL.createObjectURL(file)
      : "";
  return {
    kind: file.type.startsWith("image/") ? "image" : "file",
    name: file.name,
    url,
    mime: file.type || undefined,
    size: file.size,
    file,
  };
}

/** Multipart, like `attachmentsApi.upload` in the Projects app. */
export async function lensUploadAttachment(
  taskId: string,
  file: File,
): Promise<TaskAttachment> {
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await fetch(`/api/projects/tasks/${taskId}/attachments`, {
    method: "POST",
    body,
  });
  const raw = (await res.json().catch(() => ({}))) as Raw;
  if (!res.ok) {
    throw new Error(text(raw.detail) ?? `Upload failed (${res.status})`);
  }
  return mapAttachment(raw);
}

/**
 * My personal root (`GET /projects/my/project`), or `null` for a member who
 * has never captured — a 404 there is an answer, not a fault. ONE read
 * behind the status catalogue and the store's `personalRootId` (S6b repair),
 * so "which project is my root" has one door.
 */
export async function lensFetchMyRoot(): Promise<{ id: string; name: string } | null> {
  try {
    const root = await projectsCall<Raw>(MY_ROUTES.project);
    return { id: String(root.id ?? ""), name: String(root.name ?? "") };
  } catch (err) {
    if ((err as { status?: number }).status === 404) return null;
    throw err;
  }
}

/**
 * The settings modal's status catalogue: the lanes of my personal root.
 *
 * Under one store there is no upstream vocabulary to map — a lane IS the
 * stage — so every entry maps to itself and nothing is unmapped. A member
 * who has never captured has no root yet, and that is an empty catalogue,
 * not an error.
 */
export async function lensStatusCatalog(): Promise<StatusCatalog> {
  const root = await lensFetchMyRoot();
  if (!root) return { stages: [], entries: [], unmapped: 0 };
  const names = (await lensStatuses(root.id)).map((r) => r.name);
  return {
    stages: names,
    entries: names.map((name) => ({ status: name, stage: name, mapped: true })),
    unmapped: 0,
  };
}

// ── Areas (S6b) ─────────────────────────────────────────────────────────────
//
// An Area is a child of my personal root that carries `personal_owner`
// (migration 191). It is the whole of a member's own structure under one
// store: FLAT, by decision D65 — no space above it and no folder inside it.
// The Space→Folder→Project tree the old Tasks app drew (group D of
// `my_tasks_cutover.md` §3.1) retires under the flag, and these four doors
// are what replaces it. The gateway half is `routes/projects/personal.py`
// (PR #391): list with open counts, mint, rename, and a delete that ARCHIVES
// when tasks remain and says which it did.

/** One of my categories, as `/my/areas` answers it. */
export interface LensArea {
  id: string;
  name: string;
  /** Archived by a delete that found tasks inside. Listed only on request. */
  archived: boolean;
  /** Live tasks inside — `0` on a row a write answered with, which carries none. */
  openTasks: number;
}

/** What `DELETE /my/areas/{id}` did. The two are different promises. */
export interface LensAreaRemoval {
  id: string;
  outcome: "deleted" | "archived";
  /** How many tasks the Area held — the reason it was archived, when it was. */
  tasks: number;
}

function mapArea(raw: Raw): LensArea {
  return {
    id: String(raw.id ?? ""),
    name: String(raw.name ?? ""),
    archived: Boolean(raw.archived),
    openTasks: raw.open_tasks == null ? 0 : Number(raw.open_tasks),
  };
}

/** My Areas, live ones only, in name order — the sidebar's and the picker's read. */
export async function lensFetchAreas(): Promise<LensArea[]> {
  const res = await projectsCall<ListResponse>(MY_ROUTES.areas);
  return rowsOf(res).map(mapArea);
}

/**
 * Mint one. A name I already have live answers 409, and that surfaces as the
 * thrown error's message — the panel and the sidebar show it rather than a
 * generic "could not create".
 */
export async function lensCreateArea(name: string): Promise<LensArea> {
  return mapArea(await post(MY_ROUTES.areas, { name }));
}

/** Rename one. The name is the only field an Area has. */
export async function lensRenameArea(id: string, name: string): Promise<LensArea> {
  return mapArea(
    await projectsCall<Raw>(at(MY_ROUTES.area, id), {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  );
}

/**
 * Remove one, without removing what is in it.
 *
 * The server decides between a hard delete (empty) and an archive (holds
 * tasks), and answers with which. The caller must SAY which — "archived, 3
 * tasks kept" and "deleted" are different things to have done to somebody's
 * list, and a toast that says "Deleted" over an archive is a lie.
 */
export async function lensDeleteArea(id: string): Promise<LensAreaRemoval> {
  const raw = await projectsCall<Raw>(at(MY_ROUTES.area, id), {
    method: "DELETE",
  });
  return {
    id: String(raw.id ?? id),
    outcome: raw.outcome === "archived" ? "archived" : "deleted",
    tasks: raw.tasks == null ? 0 : Number(raw.tasks),
  };
}
