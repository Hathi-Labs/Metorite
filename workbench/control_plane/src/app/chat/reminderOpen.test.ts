/**
 * WS-66 RM-4 — a tap on a reminder link opens the one assistant
 * (`calendar_events_reminders.md` §5.7, D114).
 *
 * The spec names this fence `reminderOpen.test.tsx`. The vitest config runs
 * `src/**\/*.test.ts` only, so the file is `.ts` and draws with
 * `createElement`, as `genUITemplates.test.ts` does.
 *
 * What this holds:
 *
 * - `/chat?reminder=<id>` and `/chat?do=new-chat&reminder=<id>` both open a
 *   FRESH chat that carries the reminder, and the page reads no id that is
 *   not a UUID.
 * - The store keeps the reminder until the dock takes it, once.
 * - The open sends the reminder id alone. The server builds every word.
 * - The dock asks once for each chat, holds the chat as for the brief, and
 *   keeps the brief out of a reminder's chat.
 * - The card draws the five chips. Done calls `/resolve`, and its Undo calls
 *   `/reopen` and gives the chips back. Not yet calls `/reschedule` with the
 *   smart slot and says the new time. Later and Make it a task send the
 *   member's words to the model, and no chip calls `/promote`.
 * - With no `onAction` the card draws read-only, with no chips.
 *
 * The server half is `tests/unit/test_reminder_context.py` and
 * `tests/unit/test_reminders_r8.py`.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { TEMPLATE_REGISTRY } from "@/components/genUITemplates";
import {
  BRIEF_HOLD_MS,
  __resetAssistantForTests,
  assistantState,
  closeAssistant,
  consumeReminder,
  openAssistant,
} from "@/lib/shell/assistant";
import {
  LATER_TURN,
  REMINDER_CHIPS,
  REMINDER_HOLD_MS,
  isOpenFailure,
  noteReminderReceipt,
  openFailureWords,
  reminderFromReceipt,
  TASK_TURN,
  __resetReminderOpenForTests,
  actOfWords,
  actRequest,
  actResultWords,
  actTurn,
  chipsLabel,
  claimReminderOpen,
  closedWords,
  knownReminder,
  movedLine,
  noteReminder,
  opensReminder,
  reminderActs,
  reminderFactsShown,
  reminderOpenPath,
  reminderParam,
  requestReminderOpen,
  runReminderAct,
  type ReminderRow,
} from "@/lib/shell/reminderOpen";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const RID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const ROW: ReminderRow = {
  id: RID, title: "Get a haircut", kind: "action", state: "due", resolution: null,
  fire_at: "2026-10-29T09:30:00+00:00", tz: "Asia/Kolkata",
};

afterEach(() => {
  __resetAssistantForTests();
  __resetReminderOpenForTests();
});

/** A fetch that records each call and answers from `answers`, in order. */
function fakeFetch(...answers: Array<[number, unknown]>) {
  const calls: Array<{ url: string; method?: string; body: unknown }> = [];
  const impl = (async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const [status, body] = answers.shift() ?? [500, null];
    return new Response(body === undefined || status === 204 ? null : JSON.stringify(body), { status });
  }) as typeof fetch;
  return { calls, impl };
}

// ── The link ────────────────────────────────────────────────────────────────

describe("the link opens a fresh chat with the reminder", () => {
  it("reads a UUID and nothing else", () => {
    expect(reminderParam(new URLSearchParams(`reminder=${RID.toUpperCase()}`))).toBe(RID);
    expect(reminderParam(new URLSearchParams(`do=new-chat&reminder=${RID}`))).toBe(RID);
    for (const bad of ["", "abc", "../x", `${RID}x`, "javascript:alert(1)"]) {
      expect(reminderParam(new URLSearchParams(`reminder=${encodeURIComponent(bad)}`))).toBeUndefined();
    }
    expect(reminderParam(new URLSearchParams("do=new-chat"))).toBeUndefined();
  });

  it("the page opens a fresh chat for a reminder, with or without do=new-chat", () => {
    const page = read("app/chat/page.tsx");
    expect(page).toContain("const reminder = reminderParam(params);");
    expect(page).toContain('fresh: job === "new-chat" || Boolean(reminder),');
    expect(page).toContain("reminder,\n    });");
    // A reminder never opens the chat that was open last.
    expect(page).toContain("fields.session && !reminder ? fields.session : undefined");
  });

  it("the store keeps the reminder until the dock takes it, once", () => {
    const before = assistantState().freshSeq;
    openAssistant({ mode: "full", fresh: true, reminder: ` ${RID} ` });
    expect(assistantState().reminder).toBe(RID);
    expect(assistantState().freshSeq).toBe(before + 1);
    // Another open that names no reminder keeps the one that waits.
    openAssistant({});
    expect(assistantState().reminder).toBe(RID);
    consumeReminder();
    expect(assistantState().reminder).toBeNull();
    // A close drops a reminder that no chat took.
    openAssistant({ fresh: true, reminder: RID });
    closeAssistant();
    expect(assistantState().reminder).toBeNull();
  });
});

// ── The open ────────────────────────────────────────────────────────────────

describe("the open sends the id alone, and the server builds every word", () => {
  const answer = {
    posted: true, message_id: `reminder-${RID}`, preview: "Reminder · Get a haircut · 15:00 today. Did you do it?",
    reminder: ROW, context: { label: "Reminder · Get a haircut", facts: [`reminder_id: ${RID}`, "Title: «Get a haircut»", 7] },
  };

  it("reads a 200, and nothing else", async () => {
    const { calls, impl } = fakeFetch([200, answer]);
    const got = await requestReminderOpen("c1", RID, impl);
    expect(calls).toEqual([{ url: reminderOpenPath("c1"), method: "POST", body: { reminder_id: RID } }]);
    expect(got).toEqual({
      posted: true, preview: answer.preview, reminder: ROW,
      context: { label: "Reminder · Get a haircut", facts: [`reminder_id: ${RID}`, "Title: «Get a haircut»"] },
    });
    expect(reminderOpenPath("a/b")).toBe("/api/chat/sessions/a%2Fb/reminder-open");
    // A 204 is a shared room: nothing is wrong, and nothing shows.
    expect(await requestReminderOpen("c1", RID, fakeFetch([204, null]).impl)).toBeNull();
  });

  it("a failed open says so, and the chat does not stay blank", async () => {
    const down = { failed: true, gone: false } as const;
    expect(await requestReminderOpen("c1", RID, fakeFetch([503, { error: "down" }]).impl)).toEqual(down);
    expect(await requestReminderOpen("c1", RID, fakeFetch([200, { posted: true }]).impl)).toEqual(down);
    expect(await requestReminderOpen("c1", RID, (async () => { throw new Error("offline"); }) as typeof fetch)).toEqual(down);
    expect(isOpenFailure(down)).toBe(true);
    expect(isOpenFailure(null)).toBe(false);
    expect(openFailureWords(down)).toBe("Metorite could not open this reminder. Tap the link again.");
    // The dock puts the words in its one notice, and keeps nothing else of the open.
    const dock = read("lib/shell/AssistantDock.tsx");
    expect(dock).toContain("setNotice(openFailureWords(opened));");
    expect(dock.indexOf("isOpenFailure(opened)")).toBeLessThan(dock.indexOf("noteReminder(opened.reminder"));
  });

  it("a 404 open says the reminder is not the member's, or is gone", async () => {
    const got = await requestReminderOpen("c1", RID, fakeFetch([404, { detail: "Reminder not found" }]).impl);
    expect(got).toEqual({ failed: true, gone: true });
    expect(isOpenFailure(got) ? openFailureWords(got) : "").toBe("This reminder is not yours, or it no longer exists.");
  });

  it("a late open still shows the card: the hold lasts as long as the BFF waits", () => {
    expect(REMINDER_HOLD_MS).toBeGreaterThan(15_000);
    expect(REMINDER_HOLD_MS).toBeGreaterThan(BRIEF_HOLD_MS);
    const route = read("app/api/chat/sessions/[sessionId]/reminder-open/route.ts");
    expect(route).toContain("AbortSignal.timeout(15_000)");
    const dock = read("lib/shell/AssistantDock.tsx");
    const open = dock.indexOf("requestReminderOpen(");
    // The reminder's own timer is the long one, and the brief keeps the short one.
    expect(dock.lastIndexOf("setTimeout(release, REMINDER_HOLD_MS);", open)).toBeGreaterThan(-1);
    expect(dock.slice(open - 400, open)).not.toContain("BRIEF_HOLD_MS");
    expect(dock.slice(open)).toContain("setTimeout(release, BRIEF_HOLD_MS);");
  });

  it("the BFF route sends the gateway the id alone", () => {
    const route = read("app/api/chat/sessions/[sessionId]/reminder-open/route.ts");
    expect(route).toContain("body: JSON.stringify({ reminder_id: reminderId }),");
    expect(route).toContain("/reminder-open`");
    expect(route).not.toMatch(/\/api\/agent\//);
  });

  it("asks once for each chat, and a reminder's chat gets no brief", () => {
    expect(opensReminder("c1")).toBe(false);
    expect(claimReminderOpen("c1", RID)).toBe(true);
    expect(claimReminderOpen("c1", RID)).toBe(false);
    expect(opensReminder("c1")).toBe(true);
    expect(claimReminderOpen("", RID)).toBe(false);
    const dock = read("lib/shell/AssistantDock.tsx");
    // The open waits for the fresh chat that its link asked for.
    expect(dock).toContain("if (handledFresh.current !== st.freshSeq) return;");
    expect(dock).toContain("if (!claimReminderOpen(id, reminderId)) return;");
    expect(dock.match(/requestReminderOpen\(/g)?.length).toBe(1);
    // It holds the chat as the brief does, and comes before the brief.
    const open = dock.indexOf("requestReminderOpen(");
    expect(dock.lastIndexOf("setBriefHold(id);", open)).toBeGreaterThan(-1);
    expect(open).toBeLessThan(dock.indexOf("requestBrief("));
    expect(dock).toContain("if (st.reminder || opensReminder(activeIdForBrief)) return;");
    // The context block follows the chat, and a chip's close withdraws it.
    expect(dock).toContain("return publishAssistantFacts(reminderChat.facts);");
  });

  it("the context block leaves when a chip closes the reminder, and comes back on Undo", () => {
    expect(reminderFactsShown(null)).toBe(true);
    noteReminder({ ...ROW, kind: "notice", state: "done", resolution: "done" }, "open");
    expect(reminderFactsShown(knownReminder(RID))).toBe(true);
    noteReminder({ ...ROW, state: "done", resolution: "done" }, "chip");
    expect(reminderFactsShown(knownReminder(RID))).toBe(false);
    noteReminder(ROW, "chip");
    expect(reminderFactsShown(knownReminder(RID))).toBe(true);
  });
});

// ── The chips ───────────────────────────────────────────────────────────────

describe("the chips", () => {
  const draw = (data: Record<string, unknown>, onAction?: (m: string) => void) =>
    renderToStaticMarkup(createElement(() => TEMPLATE_REGISTRY.reminderCard(data, onAction ? { onAction } : undefined)));
  const card = { reminderId: RID, title: "Get a haircut", when: "15:00 today", zone: "Asia/Kolkata", state: "due", kind: "action" };

  it("an open reminder draws the five chips, in the order of §5.7", () => {
    const html = draw(card, () => {});
    expect(html).toContain("Get a haircut");
    expect(html).toContain("15:00 today");
    const pills = [...html.matchAll(/<button[^>]*>([^<]+)<\/button>/g)].map((m) => m[1]);
    expect(pills).toEqual(["Done", "Not yet", "Later", "Drop", "Make it a task"]);
  });

  it("with no onAction the card draws read-only", () => {
    const html = draw(card);
    expect(html).toContain("Get a haircut");
    expect(html).not.toContain("<button");
  });

  it("a closed reminder draws how it closed, and no chips", () => {
    const html = draw({ ...card, state: "done", resolution: "promoted" }, () => {});
    expect(html).toContain("Made into a task.");
    expect(html).not.toContain("<button");
    expect(closedWords("dropped", "dropped")).toBe("Dropped.");
    expect(closedWords("due", null)).toBe("");
  });

  it("Done calls /resolve, and its Undo calls /reopen and gives the chips back", async () => {
    const { calls, impl } = fakeFetch(
      [200, { ...ROW, state: "done", resolution: "done" }],
      [200, ROW],
    );
    expect(reminderActs(ROW)).toEqual(["done", "not_yet", "later", "drop", "task"]);
    const done = await runReminderAct(RID, "done", impl);
    expect(done.ok).toBe(true);
    expect(calls[0]).toEqual({ url: `/api/projects/my/reminders/${RID}/resolve`, method: "POST", body: { action: "done" } });
    const after = knownReminder(RID);
    expect(after?.via).toBe("chip");
    expect(reminderActs({ state: after!.row.state, resolution: after!.row.resolution, via: after!.via })).toEqual(["undo"]);
    // The card reads the store (`useKnownReminder`), so the Undo stays after
    // a remount. A server render reads no store, so the rule is held here.
    expect(chipsLabel(["undo"])).toBe("Changed your mind?");
    expect(chipsLabel(["done", "not_yet", "later", "drop", "task"])).toBe("Your answer");
    const template = read("components/genUITemplates.tsx");
    expect(template).toContain("const known = useKnownReminder(id);");
    expect(template).toContain("reminderActs({ state, resolution, via: known?.via })");

    const undo = await runReminderAct(RID, "undo", impl);
    expect(undo.ok).toBe(true);
    expect(calls[1]).toEqual({ url: `/api/projects/my/reminders/${RID}/reopen`, method: "POST", body: {} });
    expect(knownReminder(RID)?.row.state).toBe("due");
    expect(reminderActs({ ...knownReminder(RID)!.row, via: "chip" })).toHaveLength(5);
    if (done.ok) expect(actResultWords("done", done.row)).toBe("Done. The reminder is closed.");
  });

  it("Drop calls /resolve with drop, and its Undo too", () => {
    expect(actRequest(RID, "drop")).toEqual({ url: `/api/projects/my/reminders/${RID}/resolve`, body: { action: "drop" } });
    expect(reminderActs({ state: "dropped", resolution: "dropped", via: "chip" })).toEqual(["undo"]);
    // A close that no chip of this page made offers no Undo.
    expect(reminderActs({ state: "done", resolution: "done", via: "open" })).toEqual([]);
    expect(reminderActs({ state: "done", resolution: "promoted", via: "chip" })).toEqual([]);
  });

  it("Not yet moves the reminder to the smart slot and says the new time", async () => {
    const { calls, impl } = fakeFetch([200, { ...ROW, state: "scheduled", fire_at: "2026-10-31T04:30:00+00:00" }]);
    const got = await runReminderAct(RID, "not_yet", impl);
    expect(calls[0].url).toBe(`/api/projects/my/reminders/${RID}/reschedule`);
    expect(calls[0].body).toEqual({ fire_at: "smart" });
    expect(got.ok).toBe(true);
    const now = new Date("2026-10-29T09:35:00Z");
    if (got.ok) expect(actResultWords("not_yet", got.row, "Asia/Kolkata", now)).toBe("Moved to Sat 10:00.");
    expect(movedLine("2026-10-29T11:00:00Z", "Asia/Kolkata", now)).toBe("Moved to 16:30 today.");
    expect(movedLine("2026-10-30T04:30:00Z", "Asia/Kolkata", now)).toBe("Moved to 10:00 tomorrow.");
    expect(movedLine("2026-11-12T04:30:00Z", "Asia/Kolkata", now)).toBe("Moved to Thu 12 Nov 10:00.");
    expect(movedLine(null, "Asia/Kolkata", now)).toBe("Moved.");
  });

  it("Later and Make it a task send the member's words, and no chip calls /promote", () => {
    expect(actTurn("later")).toBe(LATER_TURN);
    expect(actTurn("task")).toBe(TASK_TURN);
    expect(actRequest(RID, "later")).toBeNull();
    expect(actRequest(RID, "task")).toBeNull();
    for (const act of Object.keys(REMINDER_CHIPS) as Array<keyof typeof REMINDER_CHIPS>) {
      expect(actRequest(RID, act)?.url ?? "").not.toContain("/promote");
      expect(actOfWords(REMINDER_CHIPS[act])).toBe(act);
    }
    expect(actOfWords("Something else")).toBeNull();
  });

  it("a receipt of a tool that closed the reminder moves the store row, and the card has no chips", () => {
    // The text of `writes.py::_reminder_receipt`, after `resolve_reminder`.
    const receipt = [
      "Done: «Get a haircut» at 15:00 today (Asia/Kolkata).",
      "  state done · kind action · private, yours. A reminder, not a task.",
      `  reminder_id: ${RID}`,
    ].join("\n");
    noteReminder(ROW, "open");
    expect(reminderFromReceipt("resolve_reminder", receipt)).toEqual({ id: RID, state: "done", resolution: "done" });
    expect(reminderFromReceipt("resolve_reminder", receipt.replace("state done", "state dropped"))?.resolution).toBe("dropped");
    expect(reminderFromReceipt("reopen_reminder", receipt.replace("state done", "state due"))).toEqual({ id: RID, state: "due", resolution: null });
    expect(reminderFromReceipt("list_reminders", receipt)).toBeNull();
    expect(reminderFromReceipt("resolve_reminder", "answer is done or drop.")).toBeNull();
    // The promote prints no state line. It ends the reminder as promoted.
    const promoted = `Made a private task from «Get a haircut».\n  full_id: ${RID}\n  reminder_id: ${RID}`;
    expect(reminderFromReceipt("promote_reminder", promoted)).toEqual({ id: RID, state: "done", resolution: "promoted" });

    expect(noteReminderReceipt("resolve_reminder", receipt)).toBe(true);
    const known = knownReminder(RID)!;
    expect(known.row.state).toBe("done");
    expect(known.row.title).toBe("Get a haircut");
    expect(reminderActs({ state: known.row.state, resolution: known.row.resolution, via: known.via })).toEqual([]);
    expect(reminderFactsShown(known)).toBe(false);
    // The card draws from the store, and the receipt card writes to it.
    expect(read("components/genUITemplates.tsx")).toContain("reminderActs({ state, resolution, via: known?.via })");
    const cards = read("components/projects/ProjectToolCards.tsx");
    expect(cards).toContain("noteReminderReceipt(e.name, result);");
  });

  it("a 409 that says the reminder is closed hides the chips", async () => {
    noteReminder(ROW, "open");
    const closed = await runReminderAct(RID, "drop", fakeFetch([409, { detail: "This reminder is closed (done). It cannot change." }]).impl);
    expect(closed.ok).toBe(false);
    const known = knownReminder(RID)!;
    expect(known.row.state).toBe("done");
    expect(reminderActs({ state: known.row.state, resolution: known.row.resolution, via: known.via })).toEqual([]);
    // Another 409 (not a closed one) leaves the row as it was.
    noteReminder(ROW, "open");
    await runReminderAct(RID, "undo", fakeFetch([409, { detail: "Only a reminder that is done or dropped can reopen. This one is due" }]).impl);
    expect(knownReminder(RID)?.row.state).toBe("due");
  });

  it("a refusal says what happened, never a raw status", async () => {
    const closed = await runReminderAct(RID, "done", fakeFetch([409, { detail: "This reminder is closed (done). It cannot change." }]).impl);
    expect(closed).toEqual({ ok: false, words: "This reminder is closed (done). It cannot change." });
    const gone = await runReminderAct(RID, "drop", fakeFetch([404, { detail: "Reminder not found" }]).impl);
    expect(gone).toEqual({ ok: false, words: "Metorite could not find this reminder. It may be gone." });
    const down = await runReminderAct(RID, "done", fakeFetch([502, { error: "upstream" }]).impl);
    expect(down.ok ? "" : down.words).toBe("Metorite could not save your answer. Try again.");
    const bad = await runReminderAct("not-a-uuid", "done", fakeFetch().impl);
    expect(bad.ok).toBe(false);
    // Only the closed 409 changed the store, and it kept the state it named.
    expect(knownReminder(RID)?.row.state).toBe("done");
  });
});
