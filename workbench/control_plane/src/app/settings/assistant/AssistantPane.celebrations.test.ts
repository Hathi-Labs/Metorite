// The "Celebrations" group of the Assistant pane (WS-63 PZ-43, `personalization.md` §16.6).
// A pure view, so `renderToStaticMarkup` is enough: vitest runs in node with no DOM.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantPaneView, FESTIVALS_LATER, NO_HANDLERS, type AssistantPaneViewProps } from "./AssistantPane";
import {
  EMPTY_PROFILE,
  celebrationsGate,
  daysInMonth,
  mergeCelebrations,
  normalize,
  type Profile,
} from "./lib/profile";

const base: AssistantPaneViewProps = {
  ...NO_HANDLERS,
  status: "ready",
  profile: EMPTY_PROFILE,
  busy: {},
  errors: {},
  confirmForget: false,
  onCancelForget: () => {},
  onConfirmForget: () => {},
  confirmResetInterview: false,
  onCancelResetInterview: () => {},
  onConfirmResetInterview: () => {},
};
const draw = (over: Partial<AssistantPaneViewProps>) =>
  renderToStaticMarkup(createElement(AssistantPaneView, { ...base, ...over }));

const withBirthday: Profile = {
  ...EMPTY_PROFILE,
  celebrations: { birthday: { month: 3, day: 12 }, festivals: [], work_anniversary: true },
};

describe("Celebrations group", () => {
  it("does not draw with the flag off", () => {
    const html = draw({});
    expect(html).not.toContain("assistant-celebrations");
    expect(html).not.toContain("Birthday month");
    expect(html).not.toContain("Celebrations");
  });

  it("draws a month, a day, a switch, and no year field", () => {
    const html = draw({ celebrations: { enabled: true, festivals: [] } });
    expect(html).toContain("assistant-celebrations");
    expect(html).toContain("Birthday month");
    expect(html).toContain("Birthday day");
    expect(html).toContain("Greet me on my work anniversary");
    expect(html.toLowerCase()).not.toContain("birthday year");
    expect(html.toLowerCase()).not.toContain('type="date"');
    expect(html).toContain("I do not keep a year");
    // A jump link joins the row.
    expect(html).toContain(">Celebrations<");
  });

  it("hides the festival picker while the list is empty, and says the list comes later", () => {
    const html = draw({ celebrations: { enabled: true, festivals: [] } });
    expect(html).toContain("celebrations-festivals-later");
    expect(html).toContain(FESTIVALS_LATER);
    expect(html).not.toContain("Festivals to greet me on");
  });

  it("draws the picker when the server lists a festival, with none picked", () => {
    const html = draw({
      celebrations: { enabled: true, festivals: [{ id: "test-festival", name: "Test Festival" }] },
    });
    expect(html).toContain("Festivals to greet me on");
    expect(html).toContain("Test Festival");
    expect(html).not.toContain("celebrations-festivals-later");
    expect(html).not.toMatch(/<input[^>]*checked[^>]*aria-label="Test Festival"/);
  });

  it("shows the stored birthday and the switch", () => {
    const html = draw({ profile: withBirthday, celebrations: { enabled: true, festivals: [] } });
    expect(html).toContain("March");
    expect(html).toContain("12");
  });
});

describe("celebrations logic", () => {
  it("reads the gate from the profile answer", () => {
    expect(celebrationsGate({})).toEqual({ enabled: false, festivals: [] });
    expect(
      celebrationsGate({ celebrations_enabled: true, festival_catalogue: [{ id: "a", name: "A" }, 5, {}] }),
    ).toEqual({ enabled: true, festivals: [{ id: "a", name: "A" }] });
  });

  it("keeps no year, and drops a part of the wrong shape", () => {
    const p = normalize({ celebrations: { birthday: { month: 3, day: 12, year: 1990 }, festivals: ["x", 4], work_anniversary: "yes" } } as never);
    expect(p.celebrations).toEqual({ birthday: { month: 3, day: 12 }, festivals: ["x"], work_anniversary: false });
    expect(JSON.stringify(p)).not.toContain("1990");
    expect(normalize({}).celebrations).toBeUndefined();
  });

  it("merges one edit, and a null birthday clears it", () => {
    const set = mergeCelebrations(undefined, { birthday: { month: 2, day: 29 } });
    expect(set).toEqual({ birthday: { month: 2, day: 29 }, festivals: [], work_anniversary: false });
    expect(mergeCelebrations(set, { work_anniversary: true }).birthday).toEqual({ month: 2, day: 29 });
    expect("birthday" in mergeCelebrations(set, { birthday: null })).toBe(false);
  });

  it("allows 29 February and no other impossible day", () => {
    expect(daysInMonth(2)).toBe(29);
    expect(daysInMonth(4)).toBe(30);
    expect(daysInMonth(12)).toBe(31);
  });
});
