/**
 * WS-66 RX-2 — the receipt of a reminder write draws as the reminder.
 *
 * Mutations caught: the card is a plain result row again ("A reminder, not a
 * task" in a grey line); the Bell, the title, the exact time or the relative
 * time is missing; the linked task shows with no task; one of the three acts
 * (Undo, Change time, See in Calendar) is missing; Undo shows when the receipt
 * names none; a refusal or a cancel draws the card; the machine lines show to
 * the member; See in Calendar opens something other than
 * `/calendar?reminder=<id>`; the entry point stops routing the three tools.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));

import { readReminderReceipt } from "@/lib/reminderReceipt";
import type { ToolEvent } from "@/components/MarkdownMessage";

import ReminderReceiptCard from "./ReminderReceiptCard";
import { forPeopleFenced, isReminderReceipt } from "./ProjectToolCards";

const SRC = fileURLToPath(new URL("../..", import.meta.url));
const read = (rel: string) => readFileSync(join(SRC, rel), "utf8").replace(/\r\n/g, "\n");

const RID = "e1f8fad5-d9cb-469f-a165-70867728950e";
const TASK = "1f8fad5b-d9cb-469f-a165-70867728950e";

function receiptText(over: { task?: boolean; undo?: string; fireAt?: string } = {}): string {
  return [
    "Reminder set: «Get a haircut» at Fri 31 Oct, 17:00 IST.",
    "  state scheduled · kind action · private, yours. A reminder, not a task.",
    `  reminder_id: ${RID}`,
    `  fire_at: ${over.fireAt ?? "2099-10-31T11:30:00+00:00"}`,
    "  zone: Asia/Kolkata",
    ...(over.task ? [`  task_id: ${TASK}`] : []),
    ...(over.undo === "" ? [] : [`  undo: ${over.undo ?? "drop"}`]),
    "«for the model» Tell the member this time and zone exactly as written.",
  ].join("\n");
}

const draw = (text: string, tool = "set_reminder") =>
  renderToStaticMarkup(
    createElement(ReminderReceiptCard, {
      id: "t1",
      label: "Reminder set",
      receipt: readReminderReceipt(tool, text)!,
      openTask: () => {},
    }),
  );

describe("the reminder receipt card", () => {
  it("shows the Bell, the title, the exact time and the relative time", () => {
    const html = draw(receiptText());
    expect(html).toContain("data-reminder-receipt");
    expect(html).toContain("Get a haircut");
    expect(html).toContain("data-reminder-exact");
    expect(html).toMatch(/data-reminder-exact[^>]*>[^<]*2099|data-reminder-exact[^>]*>[^<]*Oct/);
    expect(html).toContain("data-reminder-relative");
    expect(html).toMatch(/in \d+ days/);
    // A shared chat shows the receipt to each member, so the words fit each one.
    expect(html).toContain("private");
    // The Bell glyph sits in the round badge.
    expect(html).toContain("lucide-bell");
  });

  it("offers Undo, Change time and See in Calendar", () => {
    const html = draw(receiptText());
    const labels = [...html.matchAll(/<button[^>]*>(?:<svg[^>]*>.*?<\/svg>)?([^<]*)<\/button>/g)].map((m) => m[1].trim());
    expect(labels).toEqual(expect.arrayContaining(["Undo", "Change time", "See in Calendar"]));
  });

  // RX-7. Mutation: drop `mine &&` from the Undo, the act row or the picker in
  // the card, and this fails. Drop `mine={mine}` in `ReminderReceiptHost`, and
  // the wiring test below fails.
  it("offers no act to a member who did not run the turn (a shared chat)", () => {
    const html = renderToStaticMarkup(
      createElement(ReminderReceiptCard, {
        id: "t1",
        label: "Reminder set",
        receipt: readReminderReceipt("set_reminder", receiptText({ task: true }))!,
        openTask: () => {},
        mine: false,
      }),
    );
    expect(html).toContain("Get a haircut");
    expect(html).not.toContain(">Undo<");
    expect(html).not.toContain("Change time");
    expect(html).not.toContain("See in Calendar");
    // The linked task is the project's, and it stays.
    expect(html).toContain("Linked task");
  });

  it("offers no Undo when the receipt names none", () => {
    const html = draw(receiptText({ undo: "" }));
    expect(html).not.toContain(">Undo<");
    expect(html).toContain("Change time");
    expect(html).toContain("See in Calendar");
  });

  it("shows the linked task only when there is one", () => {
    expect(draw(receiptText({ task: true }))).toContain("Linked task");
    expect(draw(receiptText())).not.toContain("Linked task");
  });

  it("draws no machine line for the member", () => {
    const html = draw(receiptText({ task: true }));
    for (const word of ["fire_at", "reminder_id", "task_id", "undo:", "for the model"]) expect(html).not.toContain(word);
    const shown = forPeopleFenced(receiptText({ task: true }));
    for (const word of ["fire_at", "zone:", "undo:", "reminder_id", "task_id"]) expect(shown).not.toContain(word);
    expect(shown).toContain("Reminder set");
  });

  it("the picker is closed at first, so a receipt stays short", () => {
    expect(draw(receiptText())).not.toContain("data-reminder-picker");
  });
});

describe("which events draw the card", () => {
  const ev = (name: string, result: string, status: ToolEvent["status"] = "done"): ToolEvent =>
    ({ id: "e1", name, status, result } as ToolEvent);

  it("a repeat of a reminder draws the card, and it offers no Undo", () => {
    const text = receiptText({ undo: "" }).replace("Reminder set:", "Reminder already set:");
    expect(isReminderReceipt(ev("set_reminder", text))).toBe(true);
    expect(readReminderReceipt("set_reminder", text)!.already).toBe(true);
    expect(draw(text)).not.toContain(">Undo<");
  });

  it("a receipt of the three tools does, and nothing else does", () => {
    expect(isReminderReceipt(ev("set_reminder", receiptText()))).toBe(true);
    expect(isReminderReceipt(ev("update_reminder", receiptText()))).toBe(true);
    expect(isReminderReceipt(ev("reschedule_reminder", receiptText()))).toBe(true);
    expect(isReminderReceipt(ev("resolve_reminder", receiptText()))).toBe(false);
    expect(isReminderReceipt(ev("promote_reminder", receiptText()))).toBe(false);
    expect(isReminderReceipt(ev("create_task", receiptText()))).toBe(false);
  });

  it("a refusal, a cancel and a failure keep the plain receipt", () => {
    expect(isReminderReceipt(ev("set_reminder", "A reminder needs a title."))).toBe(false);
    expect(isReminderReceipt(ev("set_reminder", "Cancelled — nothing was changed."))).toBe(false);
    expect(isReminderReceipt(ev("set_reminder", receiptText(), "error"))).toBe(false);
  });
});

describe("the wiring", () => {
  const card = read("components/projects/ReminderReceiptCard.tsx");
  const cards = read("components/projects/ProjectToolCards.tsx");

  it("the entry point routes a reminder receipt to the card, before the generic receipt", () => {
    expect(cards).toContain("if (isReminderReceipt(e)) {");
    expect(cards.indexOf("if (isReminderReceipt(e)) {")).toBeLessThan(
      cards.indexOf("items.push(<ActionResultCard key={e.id} event={e} />);"),
    );
  });

  it("the host passes the run member's answer down to the card, and the bubble gives it", () => {
    expect(cards).toContain("mine={mine}");
    expect(read("components/MessageBubble.tsx")).toContain("mine={ranByViewer(message, viewerEmail)}");
  });

  it("a repeat of a reminder says so on the card", () => {
    expect(cards).toContain("receipt.already ? ALREADY_HEAD : meta.label");
  });

  it("See in Calendar opens /calendar?reminder=<id>, and Change time calls /reschedule", () => {
    expect(card).toContain("router.push(calendarReminderHref(receipt.id))");
    expect(card).toContain("moveReminder(receipt.id");
  });

  it("the card adds no colour of its own and no second button style", () => {
    expect(card).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(card).not.toMatch(/\b(?:bg|text|border)-(?:red|green|blue|amber|pink|purple|yellow)-\d/);
    expect(card).not.toContain("<button");
  });
});
