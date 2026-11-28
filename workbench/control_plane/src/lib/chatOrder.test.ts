/**
 * The one client order of a chat (task 4146, owner report 2026-11-28).
 *
 * On production, the reply "I found it. Akram's email…" drew ABOVE the
 * prompt that it answers. The saved rows were in the right order. The
 * browser flipped them. Each block below names a merge that could do it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import { hydrateRows, mergePolledRows, mergeServerRows, placeReplayRows } from "@/lib/chatOrder";
import { planReplay, rejectFor } from "@/lib/runReplay";

const T = 1_795_816_137_033; // 2026-11-28 05:48:57.033, the prompt's stamp

const msg = (id: string, role: ChatMessage["role"], content = id, extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id, role, content, timestamp: T, ...extra,
});

const ids = (ms: readonly ChatMessage[]) => ms.map((m) => m.id);

// The two rows of the incident, with their real ids. A random id sorts the
// reply ("5…") before the prompt ("k…").
const PROMPT = msg("kjcbswcqf", "user", "There should be an email that Akram sent me", { timestamp: T });
const REPLY = msg("5bk4rcdmn", "assistant", "I found it. Akram's email", { timestamp: T + 1 });
const H1 = msg("h1", "user", "Earlier question", { timestamp: T - 60_000 });
const H2 = msg("h2", "assistant", "Earlier answer", { timestamp: T - 59_000 });

describe("the recovery poll places a row by the server's order", () => {
  it("the server returns [prompt, reply] while the chat holds [reply]: the prompt goes first", () => {
    expect(ids(mergePolledRows([REPLY], [PROMPT, REPLY]))).toEqual([PROMPT.id, REPLY.id]);
    expect(ids(mergePolledRows([H1, H2, REPLY], [H1, H2, PROMPT, REPLY])))
      .toEqual([H1.id, H2.id, PROMPT.id, REPLY.id]);
  });

  it("a new reply whose words extend an older reply never takes the older reply's place", () => {
    // The old superset rule matched ANY assistant row, so "Done. More."
    // replaced "Done." two turns up, and drew above its own prompt.
    const p1 = msg("p1", "user", "First");
    const r1 = msg("r1", "assistant", "Done.");
    const p2 = msg("p2", "user", "Second");
    const r2 = msg("r2", "assistant", "Done. More.");
    expect(ids(mergePolledRows([p1, r1], [p1, r1, p2, r2]))).toEqual(["p1", "r1", "p2", "r2"]);
  });

  it("a server reply that extends a partial the server does not know takes that partial's place", () => {
    const partial = msg("local-nanoid", "assistant", "I found", { streaming: true });
    const full = msg("assistant-t1", "assistant", "I found it.");
    const out = mergePolledRows([H1, partial], [H1, full]);
    expect(ids(out)).toEqual(["h1", "assistant-t1"]);
    expect(out[1].streaming).toBe(false);
  });

  it("keeps the 'only grows' rule for a row of the same id", () => {
    const local = msg("a1", "assistant", "A longer local answer", {
      authorKind: "agent", authorEmail: "orchestrator", toolEvents: [{ id: "t1" } as never],
    });
    const behind = msg("a1", "assistant", "A short", { toolEvents: [] });
    // Nothing grew: the same array comes back, so the poll writes nothing.
    const same = [H1, local];
    expect(mergePolledRows(same, [H1, behind])).toBe(same);
    // Authorship arriving alone is news, and the text never shrinks.
    const named = mergePolledRows([msg("a2", "assistant", "Hello there")], [
      msg("a2", "assistant", "Hello", { authorKind: "agent", authorEmail: "orchestrator" }),
    ]);
    expect(named[0].content).toBe("Hello there");
    expect(named[0].authorKind).toBe("agent");
  });

  it("a row the chat shows in the wrong order takes the server's order", () => {
    // An old cache, saved while the bug was live, holds [reply, prompt].
    expect(ids(mergePolledRows([H1, REPLY, PROMPT], [H1, PROMPT, REPLY])))
      .toEqual([H1.id, PROMPT.id, REPLY.id]);
  });

  it("a tab that missed more rows than the window holds keeps its old thread on top", () => {
    const m = ["m1", "m2", "m3"].map((id) => msg(id, "user"));
    const n = ["n1", "n2"].map((id) => msg(id, "user"));
    expect(ids(mergePolledRows(m, n))).toEqual(["m1", "m2", "m3", "n1", "n2"]);
  });

  it("a saved reply above the window is never taken for a partial", () => {
    const old = msg("old", "assistant", "Done.");
    const w1 = msg("w1", "user", "Again");
    const n = msg("n", "assistant", "Done. More.");
    expect(ids(mergePolledRows([old, w1], [w1, n]))).toEqual(["old", "w1", "n"]);
  });

  it("a steer that the server saved before the reply's first words draws above the reply after the run", () => {
    // A behaviour change of task 4146, on purpose: the poll now draws what
    // a reload draws.
    const p = msg("p", "user", "Find the mail");
    const r = msg("r", "assistant", "I found it.");
    const s = msg("s", "user", "Only from Akram", { customEvents: [{ name: "steered", value: {} }] });
    expect(ids(mergePolledRows([p, r, s], [p, s, r]))).toEqual(["p", "s", "r"]);
  });

  it("rows older than the poll's window keep the top", () => {
    const old1 = msg("old1", "user");
    const old2 = msg("old2", "assistant");
    const w1 = msg("w1", "user");
    const w2 = msg("w2", "assistant");
    const n0 = msg("n0", "user");
    const n = msg("n", "user");
    expect(ids(mergePolledRows([old1, old2, w1, w2], [w1, w2, n]))).toEqual(["old1", "old2", "w1", "w2", "n"]);
    expect(ids(mergePolledRows([old1, old2, w1, w2], [n0, w1, w2]))).toEqual(["old1", "old2", "n0", "w1", "w2"]);
  });
});

describe("an id tie-break never puts a reply before its prompt", () => {
  it("rows of the same millisecond keep the server's order, whatever their ids", () => {
    const p = { ...PROMPT, timestamp: T };
    const r = { ...REPLY, timestamp: T };
    expect(ids(mergePolledRows([], [p, r]))).toEqual([p.id, r.id]);
    expect(ids(mergePolledRows([H1, r], [H1, p, r]))).toEqual([H1.id, p.id, r.id]);
    expect(ids(mergeServerRows([r], [p, r]))).toEqual([p.id, r.id]);
    expect(ids(hydrateRows([r], [p, r], null))).toEqual([p.id, r.id]);
  });
});

describe("the reattach replay places the rows it adds by the server's order", () => {
  it("the server returns [prompt, reply] while the chat holds [reply]: the prompt goes first", () => {
    const saved = [PROMPT, REPLY];
    const shown = [REPLY];
    const plan = planReplay(saved, shown, rejectFor(shown, null, false));
    expect(ids(placeReplayRows(shown, saved, plan))).toEqual([PROMPT.id, REPLY.id]);
    const shown2 = [H1, H2, REPLY];
    const saved2 = [H1, H2, PROMPT, REPLY];
    const plan2 = planReplay(saved2, shown2, rejectFor(shown2, null, false));
    expect(ids(placeReplayRows(shown2, saved2, plan2))).toEqual([H1.id, H2.id, PROMPT.id, REPLY.id]);
  });

  it("a run started elsewhere draws its prompt, then its own reply row", () => {
    const shown = [H1, H2];
    const u2 = msg("u2", "user", "From the phone", { timestamp: T - 120_000 }); // a slow phone clock
    const r2 = msg("r2", "assistant", "So far");
    const saved = [H1, H2, u2, r2];
    const plan = planReplay(saved, shown, rejectFor(shown, H2.id, false));
    const out = placeReplayRows(shown, saved, plan);
    expect(ids(out)).toEqual(["h1", "h2", "u2", "r2"]);
    expect(out[3].streaming).toBe(true);
  });
});

describe("the history load on open keeps the turn in flight (the incident)", () => {
  it("a load that read before the prompt was saved, then the poll, ends as [prompt, reply]", () => {
    // The send drew [prompt, reply]. The load started before it, and its
    // snapshot holds only the older rows. The run is live.
    const live = { ...REPLY, content: "I found", streaming: true };
    const afterSend = [H1, H2, PROMPT, live];
    const hydrated = hydrateRows(afterSend, [H1, H2], live.id);
    expect(ids(hydrated)).toEqual([H1.id, H2.id, PROMPT.id, REPLY.id]);
    expect(hydrated[3]).toBe(live); // the stream's own row, not a copy
    // The run ends. The poll reads the saved rows.
    const polled = mergePolledRows(
      hydrated.map((m) => (m.id === REPLY.id ? { ...m, streaming: false } : m)),
      [H1, H2, PROMPT, REPLY],
    );
    expect(ids(polled)).toEqual([H1.id, H2.id, PROMPT.id, REPLY.id]);
  });

  it("a row of another member that the load brings goes before the turn in flight", () => {
    const other = msg("x1", "user", "Another member's turn");
    const live = { ...REPLY, streaming: true };
    expect(ids(hydrateRows([H1, PROMPT, live], [H1, other], live.id)))
      .toEqual([H1.id, "x1", PROMPT.id, REPLY.id]);
  });

  it("with no row in common, the saved rows go first and the turn in flight last", () => {
    const live = { ...REPLY, streaming: true };
    expect(ids(hydrateRows([PROMPT, live], [H1, H2], live.id))).toEqual([H1.id, H2.id, PROMPT.id, REPLY.id]);
  });

  it("a settled thread still takes the server's rows, as before", () => {
    const stale = msg("gone", "assistant", "A row an edit on another device removed");
    const c3 = msg("c3", "user");
    expect(ids(hydrateRows([H1, H2, stale], [H1, H2, c3], null))).toEqual([H1.id, H2.id, "c3"]);
    // A server row of the same id wins, except the live reply.
    const richer = { ...H2, content: "Earlier answer, with the fold's cards" };
    expect(hydrateRows([H1, H2], [H1, richer], null)[1]).toBe(richer);
  });

  it("a send that the server has not answered yet stays", () => {
    const waiting = msg("w1", "user", "Still going out", { awaitingServer: true });
    expect(ids(hydrateRows([H1, waiting], [H1, H2], null))).toEqual([H1.id, H2.id, "w1"]);
  });
});

describe("local rows that the server does not know keep their place at the end", () => {
  it("a new server row goes before the optimistic turn", () => {
    const p = msg("p", "user", "Mine", { awaitingServer: true });
    const r = msg("r", "assistant", "", { streaming: true });
    const x = msg("x", "user", "A colleague's turn");
    expect(ids(mergePolledRows([H1, p, r], [H1, x]))).toEqual([H1.id, "x", "p", "r"]);
  });
});

describe("every merge of server rows goes through this seam", () => {
  const read = (...p: string[]) => readFileSync(join(__dirname, "..", ...p), "utf8");

  it("the poll, the replay and the history load call it, and append no server row themselves", () => {
    const hook = read("hooks", "useAgentChat.ts");
    expect(hook).toMatch(/mergePolledRows\(cur\.messages, fresh\)/);
    expect(hook).toMatch(/placeReplayRows\(prev\.messages, saved, /);
    expect(hook).not.toMatch(/merged\.push\(rm\)/);
    expect(hook).not.toMatch(/\.\.\.add\.missing/);
    const chat = read("components", "AgentChat.tsx");
    expect(chat).toMatch(/hydrateRows\(local, remote as ChatMessage\[\], liveId\)/);
    expect(chat).not.toMatch(/withLive\(/);
  });
});
