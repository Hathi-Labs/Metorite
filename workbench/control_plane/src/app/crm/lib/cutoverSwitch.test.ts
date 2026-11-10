// CRM-Z10b: THE FOLD keeps the board, and the dialog runs its three calls in
// order. The fold cases live in `tests/fixtures/crm_cutover_board_order.json`.
// `tests/unit/test_crm_cutover_switch_r8.py` runs the same cases through the
// gateway SQL and checks the `after` values there. Here the Mirror board of
// `rows` must equal the Native board of `after`, lane for lane and hue for hue.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { resolveHue } from "@/lib/statusAccent";

import { CrmError } from "./api";
import { orderLanes, stageAccent, toLeadLanes } from "./board";
import { reasonWords, type CutoverPreview, type CutoverStatus, type SwitchResult } from "./cutover";
import {
  FINISH_FAILED,
  FINISH_NOT_SWITCHED,
  OTHER_RUN,
  POLL_MS,
  START_FAILED,
  SWITCH_FAILED,
  SYNC_TOO_LONG,
  SWITCH_WORD,
  canSwitch,
  closeSwitch,
  finishClosing,
  gapsOf,
  initialSwitch,
  resultLines,
  runSwitch,
  switchReducer,
  syncStoppedWords,
  type SwitchState,
} from "./cutoverSwitch";
import { placesOf } from "./pipelines";
import type { DealPipeline, PipelineLane, Status, StatusType } from "./types";

type Row = {
  id: string;
  name: string;
  type: StatusType;
  position: number;
  probability: number;
  ui_position?: number;
  ui_color?: string;
  ui_probability?: number;
  hidden?: boolean;
  source_gone?: boolean;
};
type Case = {
  name: string;
  kind: "deal" | "lead";
  pipelines: Array<{ id: string; name: string; is_default: boolean; position: number; stages: string[] }>;
  rows: Row[];
  mirror_order: string[];
  after: Record<string, { position: number; color: string; probability: number }>;
  other_order: number;
};

const CASES = (
  JSON.parse(
    readFileSync(
      fileURLToPath(new URL("../../../../../../tests/fixtures/crm_cutover_board_order.json", import.meta.url)),
      "utf8",
    ),
  ) as { cases: Case[] }
).cases;

/** `core.ADMIN_ORDER_TAIL`: where a row with no `ui_position` sorts once an admin ordered a kind. */
const ADMIN_ORDER_TAIL = 1_000_000;

/** A row as `GET /crm/pipeline` sends it in Mirror mode (`EFFECTIVE_STATUS_COLUMNS`). */
function mirrorStatus(row: Row, adminOrder: boolean): Status {
  const effective =
    row.ui_position ?? (adminOrder ? ADMIN_ORDER_TAIL + row.position : row.position);
  return {
    id: row.id,
    name: row.name,
    type: row.type,
    is_default: false,
    // The sync writes no colour, so every stored colour of a mirror is gray.
    color: row.ui_color ?? "gray",
    position: effective,
    source_position: row.position,
    probability: row.ui_probability ?? row.probability,
    ui_position: row.ui_position ?? null,
    ui_color: row.ui_color ?? null,
    ui_probability: row.ui_probability ?? null,
    hidden: !!row.hidden,
    source_gone_at: row.source_gone ? "2026-10-01T00:00:00Z" : null,
    admin_order: adminOrder,
  };
}

/** The same row after the switch: the folded values, and no overlay. */
function nativeStatus(row: Row, after: Case["after"][string]): Status {
  return {
    id: row.id,
    name: row.name,
    type: row.type,
    is_default: false,
    color: after.color,
    position: after.position,
    source_position: after.position,
    probability: after.probability,
    ui_position: null,
    ui_color: null,
    ui_probability: null,
    hidden: !!row.hidden,
    source_gone_at: row.source_gone ? "2026-10-01T00:00:00Z" : null,
    admin_order: false,
  };
}

function lane(status: Status): PipelineLane {
  return { status, rows: [], count: 1, amount: 0, weighted: 0 };
}

function pipelinesOf(c: Case): DealPipeline[] {
  return c.pipelines.map((p) => ({
    id: p.id,
    name: p.name,
    is_default: p.is_default,
    position: p.position,
    stages: p.stages.map((id, place) => ({ id, name: id, position: place, type: "open" })),
  }));
}

/** The board of one kind, left to right, as the page draws it in *mode*. */
function board(c: Case, statuses: Status[], mode: "mirror" | "native"): string[] {
  if (c.kind === "lead") {
    // The Leads board: `toLeadLanes`, with one lead in each lane so that a
    // retired lane draws too.
    const pages = Object.fromEntries(statuses.map((s) => [s.id, { rows: [], total: 1 }]));
    return toLeadLanes(statuses, pages).map((l) => l.status.id);
  }
  // The Deals board with every deal: the places of the default pipeline.
  const places = placesOf(pipelinesOf(c), null);
  return orderLanes(statuses.map(lane), mode, places).map((l) => l.status.id);
}

describe("CRM-Z10b THE FOLD: the board looks the same after the switch", () => {
  it("has the five cases of the audit", () => {
    expect(CASES.map((c) => c.name)).toEqual([
      "a saved order with one NULL row",
      "no saved order, two pipelines, the default pipeline differs from the picklist",
      "a won stage in the middle",
      "a hidden row and a row that Zoho no longer lists",
      "lead statuses with a saved order",
    ]);
  });

  for (const c of CASES) {
    describe(c.name, () => {
      const adminOrder = c.rows.some((r) => r.ui_position != null);
      const before = c.rows.map((r) => mirrorStatus(r, adminOrder));
      const after = c.rows.map((r) => nativeStatus(r, c.after[r.id]));

      it("draws the Mirror board in the fixture's order", () => {
        expect(board(c, before, "mirror")).toEqual(c.mirror_order);
      });

      it("draws the Native board in the same order", () => {
        expect(board(c, after, "native")).toEqual(c.mirror_order);
        expect(board(c, after, "native")).toEqual(board(c, before, "mirror"));
      });

      it("gives each column the same hue before and after", () => {
        for (const row of c.rows) {
          const mirror = before.find((s) => s.id === row.id)!;
          const native = after.find((s) => s.id === row.id)!;
          expect(stageAccent(native, "native"), row.name).toEqual(stageAccent(mirror, "mirror"));
          // The stored colour is a hue name the shared vocabulary reads.
          expect(resolveHue({ color: c.after[row.id].color }), row.name).toBe(c.after[row.id].color);
        }
      });

      it("keeps the win chance that Mirror showed", () => {
        for (const row of c.rows) {
          expect(c.after[row.id].probability).toBe(row.ui_probability ?? row.probability);
        }
      });
    });
  }

  it("would break the board if a NULL ui_position folded into the picklist", () => {
    // The trap of the spec text: case (b) in picklist order is not the board.
    const c = CASES[1];
    const picklist = [...c.rows].sort((a, b) => a.position - b.position).map((r) => r.id);
    expect(picklist).not.toEqual(c.mirror_order);
  });
});

// ── The dialog's steps and calls ────────────────────────────────────────────

function preview(over: Partial<CutoverPreview> = {}): CutoverPreview {
  return {
    mode: "mirror",
    sync: { last_good_at: "2026-10-28T01:00:00Z", age_seconds: 60, running: false },
    records: [],
    other: [],
    unmatched_owners: [],
    unmatched_owner_names: 0,
    labels_kept: [],
    not_complete: ["Products"],
    stays_in_zoho: ["Attachments"],
    incomplete: [{ key: "module:Products", label: "Products", reason: "held" }],
    can_switch: true,
    pipelines_in_another_order: 0,
    ...over,
  };
}

const RESULT: SwitchResult = { status: "switched", revoked: "confirmed", zoho_connection: "disconnected" };

/** A gateway refusal, as `call()` throws it. */
function refusal(code: string, message: string, extra: Record<string, unknown> = {}): CrmError {
  return new CrmError(message, 409, code, undefined, { code, message, ...extra });
}

function status(
  state: CutoverStatus["state"], error: string | null = null, runId = "r1",
): CutoverStatus {
  return {
    state, run_id: runId, started_at: null, finished_at: null, error,
    age_seconds: null, zoho_connection: "connected",
  };
}

describe("CRM-Z10b the switch dialog", () => {
  it("adds the Leave out step only when a module is not complete", () => {
    expect(initialSwitch(preview()).steps).toEqual(["intro", "leave", "confirm"]);
    expect(initialSwitch(preview({ incomplete: [] })).steps).toEqual(["intro", "confirm"]);
  });

  it("switches only on the exact word", () => {
    let s: SwitchState = initialSwitch(preview({ incomplete: [] }));
    s = switchReducer(s, { type: "next" });
    expect(s.step).toBe("confirm");
    for (const word of ["switch", " SWITCH", "SWITCH ", "SWITCHED", ""]) {
      expect(canSwitch(switchReducer(s, { type: "type", text: word })), word).toBe(false);
    }
    const typed = switchReducer(s, { type: "type", text: SWITCH_WORD });
    expect(canSwitch(typed)).toBe(true);
    const busy = switchReducer(typed, { type: "submit" });
    expect(busy.step).toBe("progress");
    expect(canSwitch(busy)).toBe(false);
    // A second submit does nothing (§11 M3).
    expect(switchReducer(busy, { type: "submit" })).toBe(busy);
  });

  it("keeps the word and the ticks after a failure", () => {
    let s = initialSwitch(preview());
    s = switchReducer(s, { type: "tick", key: "module:Products", on: true });
    s = switchReducer(switchReducer(s, { type: "next" }), { type: "next" });
    s = switchReducer(s, { type: "type", text: SWITCH_WORD });
    s = switchReducer(switchReducer(s, { type: "submit" }), { type: "failed", message: "No." });
    expect(s).toMatchObject({ step: "confirm", typed: SWITCH_WORD, error: "No.", busy: false });
    expect(s.leaveOut).toEqual(["module:Products"]);
  });

  it("polls the status every 3 seconds, then switches with the ticked modules and the run", async () => {
    const reads = [status("running"), status("running"), status("ready")];
    const start = vi.fn(async () => ({ run_id: "r1" }));
    const read = vi.fn(async () => reads.shift()!);
    const commit = vi.fn(async () => RESULT);
    const wait = vi.fn(async (_ms: number) => undefined);
    const phases: string[] = [];
    const out = await runSwitch(["module:Products"], {
      start, status: read, commit, wait, onPhase: (p) => phases.push(p),
    });
    expect(out).toEqual({ result: RESULT });
    const body = { confirm: SWITCH_WORD, leave_out: ["module:Products"] };
    expect(start).toHaveBeenCalledWith(body);
    expect(read).toHaveBeenCalledTimes(3);
    expect(wait.mock.calls.map(([ms]) => ms)).toEqual([POLL_MS, POLL_MS, POLL_MS]);
    expect(POLL_MS).toBe(3000);
    // P0 (review): phase 2 gets the run that this start began.
    expect(commit).toHaveBeenCalledWith({ ...body, run_id: "r1" });
    expect(phases).toEqual(["sync", "switch"]);
  });

  it("always waits for its own run, and stops on a failed one", async () => {
    const commit = vi.fn(async () => RESULT);
    const read = vi.fn(async () => status("ready", null, "r2"));
    const own = await runSwitch([], {
      start: async () => ({ run_id: "r2" }),
      status: read, commit, wait: async () => undefined,
    });
    expect(own).toEqual({ result: RESULT });
    expect(read).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenLastCalledWith({ confirm: SWITCH_WORD, leave_out: [], run_id: "r2" });
    const stopped = await runSwitch([], {
      start: async () => ({ run_id: "r1" }),
      status: async () => status("failed", "budget"),
      commit, wait: async () => undefined,
    });
    expect(stopped).toEqual({ error: syncStoppedWords("budget") });
    expect(commit).toHaveBeenCalledTimes(1);
    const refused = await runSwitch([], {
      start: async () => { throw refusal("needs_reconnect", "Zoho asks you to connect again."); },
      status: async () => status("ready"), commit, wait: async () => undefined,
    });
    expect(refused).toEqual({ error: "Zoho asks you to connect again." });
    const lost = await runSwitch([], {
      start: async () => { throw new TypeError("Failed to fetch"); },
      status: async () => status("ready"), commit, wait: async () => undefined,
    });
    expect(lost).toEqual({ error: START_FAILED });
    // Review P3: after a lost 202, the words never say that it did not start.
    expect(START_FAILED).toBe(
      "Metorite did not get an answer. The final sync may run now. Wait a minute, then try again.",
    );
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("keeps the run of a slow sync, and the next press waits for it (review P3)", async () => {
    const commit = vi.fn(async () => RESULT);
    const start = vi.fn(async () => ({ run_id: "r7" }));
    const slow = await runSwitch([], {
      start, status: async () => status("running", null, "r7"), commit,
      wait: async () => undefined,
    });
    expect(slow).toEqual({ error: SYNC_TOO_LONG, runId: "r7" });
    expect(commit).not.toHaveBeenCalled();
    // The reducer keeps the run for the next press.
    let s = initialSwitch(preview({ incomplete: [] }));
    s = switchReducer(switchReducer(s, { type: "next" }), { type: "type", text: SWITCH_WORD });
    s = switchReducer(switchReducer(s, { type: "submit" }), {
      type: "failed", message: SYNC_TOO_LONG, runId: "r7",
    });
    expect(s.runId).toBe("r7");
    // The next press reads that run first, and starts no new sync.
    const reads = [status("running", null, "r7"), status("running", null, "r7"),
      status("ready", null, "r7")];
    const again = await runSwitch([], {
      start, status: async () => reads.shift()!, commit, wait: async () => undefined,
    }, s.runId);
    expect(again).toEqual({ result: RESULT });
    expect(start).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ confirm: SWITCH_WORD, leave_out: [], run_id: "r7" });
    // A failure with no run clears it.
    expect(switchReducer(s, { type: "failed", message: "No." }).runId).toBeNull();
  });

  it("keeps the final run after a 409 with modules, and the next press switches with it (P3-c)", async () => {
    const start = vi.fn(async () => ({ run_id: "r5" }));
    const tick = refusal("module_incomplete", "Tick them.", {
      modules: [{ key: "module:Quotes", label: "Quotes", reason: "not_read" }],
    });
    const refused = vi.fn(async () => { throw tick; });
    const first = await runSwitch([], {
      start, status: async () => status("ready", null, "r5"), commit: refused,
      wait: async () => undefined,
    });
    expect(first).toMatchObject({ runId: "r5", gaps: [{ key: "module:Quotes" }] });
    // The reducer keeps the run with the ticks.
    let s = initialSwitch(preview({ incomplete: [] }));
    s = switchReducer(switchReducer(s, { type: "next" }), { type: "type", text: SWITCH_WORD });
    s = switchReducer(switchReducer(s, { type: "submit" }), {
      type: "failed", message: "Tick them.", gaps: "gaps" in first ? first.gaps : [], runId: "r5",
    });
    expect(s.runId).toBe("r5");
    // The admin ticks Quotes and presses again: the run is ready, so the
    // switch goes at once with it, and no new run starts.
    const commit = vi.fn(async () => RESULT);
    const read = vi.fn(async () => status("ready", null, "r5"));
    const second = await runSwitch(["module:Quotes"], {
      start, status: read, commit, wait: async () => undefined,
    }, s.runId);
    expect(second).toEqual({ result: RESULT });
    expect(start).toHaveBeenCalledTimes(1);
    expect(read).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({
      confirm: SWITCH_WORD, leave_out: ["module:Quotes"], run_id: "r5",
    });
    // The run is no longer fresh (the status says idle): a new run starts.
    const reads = [status("idle", null, "r5"), status("ready", null, "r6")];
    start.mockImplementation(async () => ({ run_id: "r6" }));
    const third = await runSwitch(["module:Quotes"], {
      start, status: async () => reads.shift()!, commit, wait: async () => undefined,
    }, "r5");
    expect(third).toEqual({ result: RESULT });
    expect(start).toHaveBeenCalledTimes(2);
    expect(commit).toHaveBeenLastCalledWith({
      confirm: SWITCH_WORD, leave_out: ["module:Quotes"], run_id: "r6",
    });
    // A 409 that names no module keeps no run.
    const stale = await runSwitch([], {
      start, status: async () => status("ready", null, "r6"),
      commit: async () => { throw refusal("final_sync_missing", "Start the switch again."); },
      wait: async () => undefined,
    }, "r6");
    expect(stale).toEqual({ error: "Start the switch again." });
  });

  it("starts a new sync when the kept run is gone, and stops when another run takes over", async () => {
    const commit = vi.fn(async () => RESULT);
    const start = vi.fn(async () => ({ run_id: "r9" }));
    // The kept run failed meanwhile: this press starts its own run.
    const reads = [status("failed", "budget", "r7"), status("ready", null, "r9")];
    const fresh = await runSwitch([], {
      start, status: async () => reads.shift()!, commit, wait: async () => undefined,
    }, "r7");
    expect(fresh).toEqual({ result: RESULT });
    expect(start).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenLastCalledWith({ confirm: SWITCH_WORD, leave_out: [], run_id: "r9" });
    // Another run is the newest while the dialog waits: no switch.
    const other = await runSwitch([], {
      start, status: async () => status("running", null, "r10"), commit,
      wait: async () => undefined,
    });
    expect(other).toEqual({ error: OTHER_RUN });
    expect(commit).toHaveBeenCalledTimes(1);
  });

  it("finishes a switch whose answer got lost (review P2)", async () => {
    // The first press: the switch committed, but its answer did not arrive.
    const commitLost = vi.fn(async () => { throw new TypeError("Failed to fetch"); });
    const first = await runSwitch([], {
      start: async () => ({ run_id: "r1" }),
      status: async () => status("ready"), commit: commitLost, wait: async () => undefined,
    });
    expect(first).toEqual({ error: SWITCH_FAILED });
    expect(SWITCH_FAILED).not.toMatch(/Nothing changed/);
    // The press again: the start says not_mirror, the status says done, and
    // the switch goes again. The gateway answers already_done and closes Zoho.
    const again: SwitchResult = { status: "already_done", revoked: "confirmed", zoho_connection: "disconnected" };
    const commit = vi.fn(async () => again);
    const read = vi.fn(async () => status("done"));
    const second = await runSwitch([], {
      start: async () => { throw refusal("not_mirror", "The switch is for a CRM that mirrors Zoho."); },
      status: read, commit, wait: async () => undefined,
    });
    expect(second).toEqual({ result: again });
    expect(read).toHaveBeenCalledTimes(1);
    expect(commit).toHaveBeenCalledWith({ confirm: SWITCH_WORD, leave_out: [] });
    // A start that says not_mirror for an org that did not switch: no switch.
    const notDone = vi.fn(async () => RESULT);
    const other = await runSwitch([], {
      start: async () => { throw refusal("not_mirror", "The switch is for a CRM that mirrors Zoho."); },
      status: async () => status("idle"), commit: notDone, wait: async () => undefined,
    });
    expect(other).toEqual({ error: "The switch is for a CRM that mirrors Zoho." });
    expect(notDone).not.toHaveBeenCalled();
  });

  it("reloads the mode on close after a switch call that failed", () => {
    const reload = vi.fn();
    const close = vi.fn();
    let s = initialSwitch(preview({ incomplete: [] }));
    s = switchReducer(switchReducer(s, { type: "next" }), { type: "type", text: SWITCH_WORD });
    s = switchReducer(s, { type: "submit" });
    // A failure in the sync: the switch never went, so no reload.
    const synced = switchReducer(s, { type: "failed", message: "No." });
    closeSwitch(synced, { reload, close });
    expect(reload).not.toHaveBeenCalled();
    // A failure after the switch went: the mode may have changed.
    s = switchReducer(switchReducer(s, { type: "phase", phase: "switch" }), {
      type: "failed", message: SWITCH_FAILED,
    });
    expect(s.committed).toBe(true);
    closeSwitch(s, { reload, close });
    expect(reload).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("finishes closing Zoho only when the status says the switch is done (F6)", async () => {
    const commit = vi.fn(async () => RESULT);
    const stale = await finishClosing({ status: async () => status("ready"), commit });
    expect(stale).toEqual({ error: FINISH_NOT_SWITCHED });
    expect(commit).not.toHaveBeenCalled();
    const done = await finishClosing({ status: async () => status("done"), commit });
    expect(done).toEqual({ lines: resultLines(RESULT).slice(1) });
    expect(commit).toHaveBeenCalledWith({ confirm: SWITCH_WORD, leave_out: [] });
    const failed = await finishClosing({
      status: async () => status("done"),
      commit: async () => ({ ...RESULT, revoked: null, zoho_connection: "connected" }),
    });
    expect(failed).toEqual({ error: FINISH_FAILED });
  });

  it("reads the modules of a 409 module_incomplete, and nothing else", () => {
    const modules = [
      { key: "module:Quotes", label: "Quotes", reason: "short" },
      { key: "tasks", label: "Tasks", reason: "something new" },
      { key: "module:Products", label: "Products", reason: "not_read" },
      { key: "deal_contacts", label: "Contacts linked to deals", reason: "roles_waiting", count: 3 },
      { label: "No key" },
    ];
    const rows = gapsOf(refusal("module_incomplete", "Tick them.", { modules }));
    expect(rows).toEqual([
      { key: "module:Quotes", label: "Quotes", reason: "short" },
      { key: "tasks", label: "Tasks", reason: "failed" },
      // Review P1: a module that the final sync did not read keeps its reason.
      { key: "module:Products", label: "Products", reason: "not_read" },
      // Round 4 (P2-3): the deals that wait for their contacts, with a count.
      { key: "deal_contacts", label: "Contacts linked to deals", reason: "roles_waiting", count: 3 },
    ]);
    expect(reasonWords(rows[3])).toBe(
      "3 deals still wait for their contacts from Zoho. Wait for the next syncs to read them, " +
        "or leave them out. A deal you leave out keeps no linked contacts from Zoho",
    );
    expect(reasonWords({ ...rows[3], count: 1 })).toMatch(/^1 deal still waits for its contacts/);
    // Round 6: a walk that read fewer records than Zoho holds has its own words.
    const [short] = gapsOf(refusal("module_incomplete", "Tick them.", {
      modules: [{ key: "organizations", label: "Companies", reason: "walk_short" }],
    }));
    expect(short.reason).toBe("walk_short");
    expect(reasonWords(short)).toBe(
      "Zoho sent fewer records than it holds, so Metorite cannot confirm it read them all",
    );
    expect(gapsOf(refusal("run_open", "A sync runs now.", { modules }))).toEqual([]);
    expect(gapsOf(new Error("plain"))).toEqual([]);
  });

  it("says what Zoho said to the revoke", () => {
    expect(resultLines(RESULT)[1]).toMatch(/Zoho confirmed/);
    expect(resultLines({ ...RESULT, revoked: "not_confirmed" })[1]).toMatch(
      /did not confirm.*remove Metorite from the connected apps/,
    );
    expect(resultLines({ ...RESULT, revoked: null, zoho_connection: "connected" })[1]).toMatch(
      /Finish closing Zoho/,
    );
  });

  it("reloads the mode when the admin closes it after a switch, and not before", () => {
    const reload = vi.fn();
    const close = vi.fn();
    const open = initialSwitch(preview());
    closeSwitch(open, { reload, close });
    expect(reload).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledTimes(1);
    const done = switchReducer(open, { type: "done", result: RESULT });
    closeSwitch(done, { reload, close });
    expect(reload).toHaveBeenCalledTimes(1);
    const busy: SwitchState = { ...open, busy: true };
    closeSwitch(busy, { reload, close });
    expect(close).toHaveBeenCalledTimes(2);
  });
});
