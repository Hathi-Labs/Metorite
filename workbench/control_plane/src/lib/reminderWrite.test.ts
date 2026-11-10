// The create route answers `already: true` for a repeat of an open reminder
// (RX-7, item 3). The three clients that set a reminder read it, and say that
// the reminder already exists, with its time. They never say "set".
//
// Mutation: make `write` drop `already` from the result, and the first test
// fails. Make `createdWords` ignore `already`, and the second test fails.
import { describe, expect, it } from "vitest";

import { createReminder, createdWords } from "./reminderWrite";

const reply = (status: number, body: unknown): typeof fetch =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

const ask = { title: "Call Sam", kind: "action" as const, fireAt: "2026-11-03T09:00:00+00:00", tz: "UTC" };

describe("createReminder reads `already`", () => {
  it("passes a repeat on, with the time of the twin", async () => {
    const got = await createReminder(ask, reply(200, { id: "r1", already: true, fire_at: "2026-11-03T09:00:00+00:00" }));
    expect(got).toEqual({ ok: true, already: true, fireAt: "2026-11-03T09:00:00+00:00" });
  });

  it("answers a new reminder with no `already`", async () => {
    const got = await createReminder(ask, reply(201, { id: "r2", fire_at: ask.fireAt }));
    expect(got).toEqual({ ok: true });
  });

  it("still reads a refusal", async () => {
    const got = await createReminder(ask, reply(500, { detail: "no" }));
    expect(got.ok).toBe(false);
  });
});

describe("createdWords", () => {
  const when = { ms: Date.parse("2026-11-03T09:00:00+00:00") };

  it("says the reminder already exists, with its time, for a repeat", () => {
    const words = createdWords({ already: true, fireAt: "2026-11-04T10:30:00+00:00" }, when, "UTC");
    expect(words).toMatch(/^This reminder already exists, for /);
    expect(words).toMatch(/10:30/);
    expect(words).not.toMatch(/set for/i);
  });

  it("says `set for` for a new reminder", () => {
    expect(createdWords({}, when, "UTC")).toMatch(/^Reminder set for .*9:00/);
  });
});
