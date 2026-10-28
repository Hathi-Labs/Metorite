// CRM-Z10b: THE FOLD keeps the board, and the dialog runs its three calls in
// order. The fold cases live in `tests/fixtures/crm_cutover_board_order.json`.
// `tests/unit/test_crm_cutover_switch_r8.py` runs the same cases through the
// gateway SQL and checks the `after` values there. Here the Mirror board of
// `rows` must equal the Native board of `after`, lane for lane and hue for hue.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { resolveHue } from "@/lib/statusAccent";

import { orderLanes, stageAccent, toLeadLanes } from "./board";
import type { CutoverPreview, CutoverStatus, SwitchResult } from "./cutover";
import {
  POLL_MS,
  SWITCH_WORD,
  canSwitch,
  closeSwitch,
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

function status(state: CutoverStatus["state"], error: string | null = null): CutoverStatus {
  return {
    state, run_id: "r1", started_at: null, finished_at: null, error,
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

  it("polls the status every 3 seconds, then switches with the ticked modules", async () => {
    const reads = [status("running"), status("running"), status("ready")];
    const start = vi.fn(async () => ({ run_id: "r1", already_fresh: false }));
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
    expect(commit).toHaveBeenCalledWith(body);
    expect(phases).toEqual(["sync", "switch"]);
  });

  it("switches at once after a fresh sync, and stops on a failed one", async () => {
    const commit = vi.fn(async () => RESULT);
    const fresh = await runSwitch([], {
      start: async () => ({ run_id: "r0", already_fresh: true }),
      status: async () => { throw new Error("no read"); },
      commit, wait: async () => undefined,
    });
    expect(fresh).toEqual({ result: RESULT });
    const stopped = await runSwitch([], {
      start: async () => ({ run_id: "r1", already_fresh: false }),
      status: async () => status("failed", "budget"),
      commit, wait: async () => undefined,
    });
    expect(stopped).toEqual({ error: syncStoppedWords("budget") });
    expect(commit).toHaveBeenCalledTimes(1);
    const refused = await runSwitch([], {
      start: async () => { throw new Error("Zoho asks you to connect again."); },
      status: async () => status("ready"), commit, wait: async () => undefined,
    });
    expect(refused).toEqual({ error: "Zoho asks you to connect again." });
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
