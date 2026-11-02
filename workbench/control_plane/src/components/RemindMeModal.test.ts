/**
 * WS-66 RX-6 — "Remind me" on a task, and the Tasks quick capture that sets a
 * real reminder.
 *
 * Mutations caught: the form sends no `subject_kind: "task"`; a time at or
 * before now is accepted; a bad date passes; the create call uses a second
 * client; the reminder route's 404 reads as "could not find this reminder";
 * the Remind kind of the quick capture goes back to `defer_until`; the quick
 * capture imports from the Calendar app (D54); the task body loses its act.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { PAST_FIELD_WORDS } from "@/lib/reminderClock";
import { REMINDERS_OFF_WORDS, createReminder } from "@/lib/reminderWrite";

import { checkRemindMe } from "./RemindMeModal";

const SRC = fileURLToPath(new URL("..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const TASK = "1f8fad5b-d9cb-469f-a165-70867728950e";
const ZONE = "Asia/Kolkata";
const NOW = Date.parse("2026-11-02T04:30:00Z"); // 10:00 IST

describe("checkRemindMe", () => {
  it("takes a title and a time after now, and gives the ISO time with the offset", () => {
    const ok = checkRemindMe({ title: " Call Raj ", date: "2026-11-02", time: "17:00", zone: ZONE, nowMs: NOW });
    expect(ok.title).toBe("Call Raj");
    expect(ok.when?.iso).toBe("2026-11-02T17:00:00+05:30");
    expect(ok.titleError).toBeUndefined();
    expect(ok.whenError).toBeUndefined();
  });

  it("refuses an empty title, a long title, a time at or before now and a bad date, each under its field", () => {
    const base = { title: "x", date: "2026-11-02", time: "17:00", zone: ZONE, nowMs: NOW };
    expect(checkRemindMe({ ...base, title: "  " }).titleError).toBe("Add a title for the reminder.");
    expect(checkRemindMe({ ...base, title: "x".repeat(201) }).titleError).toBe("Keep the title to 200 characters.");
    expect(checkRemindMe({ ...base, time: "10:00" }).whenError).toBe(PAST_FIELD_WORDS);
    expect(checkRemindMe({ ...base, date: "2026-11-01" }).whenError).toBe(PAST_FIELD_WORDS);
    expect(checkRemindMe({ ...base, date: "2026-02-30" }).whenError).toBe("This date or time is not valid.");
    expect(checkRemindMe({ ...base, time: "" }).whenError).toBe("Pick a date and a time.");
    expect(checkRemindMe({ ...base, time: "10:00" }).when).toBeUndefined();
  });
});

describe("createReminder with a task", () => {
  const fake = (status: number, body: unknown) => {
    const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
    const impl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      return new Response(JSON.stringify(body), { status });
    }) as typeof fetch;
    return { calls, impl };
  };

  it("posts the task as the subject of the reminder", async () => {
    const { calls, impl } = fake(201, {});
    const got = await createReminder(
      { title: "Call Raj", kind: "action", fireAt: "2026-11-02T17:00:00+05:30", tz: ZONE, subject: { kind: "task", id: TASK } },
      impl,
    );
    expect(got).toEqual({ ok: true });
    expect(calls[0].url).toBe("/api/projects/my/reminders");
    expect(calls[0].body).toEqual({
      title: "Call Raj",
      kind: "action",
      fire_at: "2026-11-02T17:00:00+05:30",
      tz: ZONE,
      subject_kind: "task",
      subject_id: TASK,
    });
  });

  it("sends no subject for a plain reminder", async () => {
    const { calls, impl } = fake(201, {});
    await createReminder({ title: "x", kind: "notice", fireAt: "2026-11-02T17:00:00+05:30", tz: ZONE }, impl);
    expect(calls[0].body).not.toHaveProperty("subject_kind");
  });

  it("a 404 or a 403 says that reminders are not available, and a 422 says the server's words are not shown", async () => {
    for (const status of [404, 403]) {
      expect(await createReminder({ title: "x", kind: "action", fireAt: "a", tz: ZONE }, fake(status, { detail: "Not Found" }).impl)).toEqual({
        ok: false,
        words: REMINDERS_OFF_WORDS,
      });
    }
    const other = await createReminder({ title: "x", kind: "action", fireAt: "a", tz: ZONE }, fake(500, {}).impl);
    expect(other).toEqual({ ok: false, words: "Metorite could not save the reminder. Try again." });
  });
});

describe("the wiring", () => {
  it("the task body has a Remind me act that opens the form for this task", () => {
    const body = read("app/projects/components/TaskBody.tsx");
    expect(body).toContain('import { RemindMeModal } from "@/components/RemindMeModal";');
    expect(body).toContain("Remind me");
    expect(body).toContain("<RemindMeModal taskId={task.id} taskTitle={task.title}");
  });

  it("the form sets a reminder linked to the task through the one create client", () => {
    const modal = read("components/RemindMeModal.tsx");
    expect(modal).toContain('subject: { kind: "task", id: taskId }');
    expect(modal).toContain("createReminder({");
    expect(modal).not.toContain("fetch(");
    // Tasks may not import from the Calendar app (D54).
    expect(modal).not.toMatch(/from "@\/app\/calendar/);
  });

  it("the quick capture makes a real reminder for Remind, and no longer defers", () => {
    const capture = read("app/tasks/components/QuickCapture.tsx");
    expect(capture).toContain("createReminder({ title: t, kind: \"action\"");
    expect(capture).toContain('if (dateIso && dateKind === "remind") {');
    expect(capture).not.toContain("deferUntil");
    expect(capture).toContain("makes no task");
    expect(capture).not.toMatch(/from "@\/app\/calendar/);
    expect(capture).not.toMatch(/from "\.\.\/\.\.\/calendar/);
  });

  it("the command bar's capture job no longer takes 'remind'", () => {
    const jobs = read("lib/shell/jobs.generated.ts");
    const capture = jobs.slice(jobs.indexOf('id: "capture"'), jobs.indexOf('id: "compose"'));
    expect(capture).not.toMatch(/"remind"|"reminder"/);
    expect(jobs).toContain('id: "set-reminder"');
    expect(jobs).toContain('href: "/calendar?do=new-reminder"');
  });
});
