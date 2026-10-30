// The Assistant pane view draws every state (WS-63 PZ-7). A pure view, so
// `renderToStaticMarkup` is enough: vitest runs in node with no DOM.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AssistantPaneView, NO_HANDLERS, type AssistantPaneViewProps } from "./AssistantPane";
import { CAPS, EMPTY_PROFILE, type Profile } from "./lib/profile";

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

const full: Profile = {
  v: 1,
  learning: false,
  address: { name: "Vijay", formality: "casual" },
  style: { length: "short", notes: ["Answer first"] },
  aliases: [{ id: "a1", alias: "Mum", email: "mum@example.com" }],
  facts: [
    { id: "f1", text: "I work in Bengaluru", kind: "work", source: "chat", at: "2026-10-23T11:00:00Z" },
    { id: "f2", text: "I run on Mondays", kind: "routine", source: "settings", at: "2026-10-20T08:00:00Z" },
  ],
};

describe("AssistantPaneView", () => {
  it("draws a skeleton while loading", () => {
    const html = draw({ status: "loading" });
    expect(html).toContain("assistant-loading");
    expect(html).not.toContain("Forget everything");
  });

  it("draws an error with Retry, and never the empty form", () => {
    const html = draw({ status: "error", loadError: "The assistant profile is not available now." });
    expect(html).toContain("Retry");
    expect(html).toContain("not available now");
    expect(html).not.toContain("Add fact");
  });

  it("draws friendly empty lines", () => {
    const html = draw({});
    expect(html).toContain("Nothing yet.");
    expect(html).toContain("No names yet.");
    expect(html).toContain("No notes yet.");
    // A counter far from its cap is noise, so it does not show.
    expect(html).not.toContain(`0 of ${CAPS.facts} facts`);
  });

  it("draws facts with kind, source and date, and aliases with no source", () => {
    const html = draw({ profile: full });
    expect(html).toContain("I work in Bengaluru");
    expect(html).toContain("Work");
    expect(html).toContain("Learned in chat");
    expect(html).toContain("2026-10-23");
    expect(html).toContain("Added in Settings");
    expect(html).toContain("Mum");
    expect(html).toContain("mum@example.com");
    expect(html).toContain("Answer first");
    expect(html).not.toContain("2 of 40 facts");
    const crowded = Array.from({ length: 33 }, (_, i) => ({ id: `c${i}`, text: `fact ${i}`, kind: "work" as const }));
    expect(draw({ profile: { ...EMPTY_PROFILE, facts: crowded } })).toContain("33 of 40 facts");
  });

  it("says that off stops writing and the assistant still reads", () => {
    const html = draw({ profile: full });
    expect(html).toContain("stops writing");
    expect(html).toContain("still reads");
  });

  it("shows a row error and a per-row busy state", () => {
    const html = draw({ profile: full, errors: { f1: "That item is gone." }, busy: { f2: true } });
    expect(html).toContain("That item is gone.");
    expect(html).toContain('role="alert"');
  });

  it("disables add at the caps", () => {
    const facts = Array.from({ length: CAPS.facts }, (_, i) => ({
      id: `f${i}`, text: `fact ${i}`, kind: "about" as const,
    }));
    const html = draw({ profile: { ...EMPTY_PROFILE, facts } });
    expect(html).toContain("You have 40 facts. Delete one to add another.");
    const aliases = Array.from({ length: CAPS.aliases }, (_, i) => ({
      id: `a${i}`, alias: `n${i}`, email: `n${i}@example.com`,
    }));
    expect(draw({ profile: { ...EMPTY_PROFILE, aliases } })).toContain("You have 50 names.");
  });

  it("offers the download as JSON and as text, with its own error (PZ-9)", () => {
    const html = draw({ profile: full });
    expect(html).toContain("Download my profile");
    expect(html).toContain("Download as JSON");
    expect(html).toContain("Download as text");
    expect(html).toContain("nothing about anybody else");
    const failed = draw({ errors: { "export-text": "The assistant profile is not available now." } });
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("not available now");
  });

  it("offers the download for an empty profile too", () => {
    expect(draw({})).toContain("Download as JSON");
    expect(draw({ status: "loading" })).not.toContain("Download as JSON");
  });

  it("has no Undo in the markup (the chat Undo card needs PZ-6)", () => {
    expect(draw({ profile: full })).not.toContain("Undo");
  });
});

describe("source scan", () => {
  const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const files = ["AssistantPane.tsx", "page.tsx", "lib/profile.ts"].map((f) =>
    strip(readFileSync(new URL(`./${f}`, import.meta.url), "utf-8")),
  );
  it("uses no lucide-react, no inline style and no colour literal", () => {
    for (const src of files) {
      expect(src).not.toMatch(/lucide-react/);
      expect(src).not.toMatch(/style=\{?/);
      expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(src).not.toMatch(/\b(?:rgb|hsl)a?\(/);
      expect(src).not.toMatch(/\[#|-\[var\(/);
    }
  });
  it("hand-rolls no select and no switch", () => {
    for (const src of files) {
      expect(src).not.toMatch(new RegExp("<sel" + "ect\\b"));
      expect(src).not.toMatch(/role="switch"/);
    }
  });
});

// ── PZ-27: the interview list, the button and the resets ──────────────────

const openAssistant = vi.fn();
vi.mock("@/lib/shell/assistant", () => ({ openAssistant: (o: unknown) => openAssistant(o) }));

describe("the interview section (PZ-27)", () => {
  const withInterview: Profile = {
    ...EMPTY_PROFILE,
    interview: {
      started_at: "2026-10-25T08:00:00Z",
      areas: {
        role: { state: "answered" },
        goals: { state: "asked" },
        good_day: { state: "skipped" },
        decisions: { state: "never" },
      },
    },
    facts: [{ id: "f1", text: "I lead sales", kind: "work", area: "role" }],
  };

  it("lists every area with its state, and open for an area with no entry", () => {
    const html = draw({ profile: withInterview });
    for (const label of ["Your role", "Your goals", "A good day", "How you decide", "People you work with"]) {
      expect(html).toContain(label);
    }
    for (const word of ["Answered", "Asked", "Skipped", "Stopped asking", "Not asked yet"]) expect(html).toContain(word);
    expect(html).toContain("bg-success/10");
    expect(html).toContain("bg-warning/10");
  });

  it("draws Reset only for an area that has a state", () => {
    expect(draw({ profile: withInterview }).match(/aria-label="Reset /g)).toHaveLength(4);
    expect(draw({}).match(/aria-label="Reset /g)).toBeNull();
  });

  it("offers the Get to know me button, and says that a reset keeps the facts", () => {
    const html = draw({});
    expect(html).toContain("Get to know me");
    expect(html).toContain("Reset the interview");
    expect(html).toContain("Resetting keeps the facts");
  });

  it("a fact that answers an area shows the area as a Badge", () => {
    const html = draw({ profile: withInterview });
    expect(html).toMatch(/text-\[9px\][^>]*>Your role<\/span>/);
  });

  // Mutation: send `draft` instead of `text`, and this goes red.
  it("the button sends the request at once, in a fresh chat", async () => {
    const { GET_TO_KNOW_ME_MESSAGE, startGetToKnowMe } = await import("@/lib/shell/interviewMessage");
    startGetToKnowMe();
    expect(openAssistant).toHaveBeenCalledWith({ fresh: true, text: GET_TO_KNOW_ME_MESSAGE });
    expect(GET_TO_KNOW_ME_MESSAGE).toBe("Get to know me. Ask me a few questions.");
    expect(GET_TO_KNOW_ME_MESSAGE).not.toMatch(/profile_|_/);
  });

  const src = readFileSync(new URL("./AssistantPane.tsx", import.meta.url), "utf-8");
  it("wires the button to startGetToKnowMe, and the dialog to Reset the interview", () => {
    expect(src).toMatch(/onGetToKnow:\s*startGetToKnowMe/);
    expect(src).toMatch(/<ConfirmDialog\s+open=\{v\.confirmResetInterview\}/);
    expect(src).toMatch(/onResetInterview:\s*\(\) => setConfirmResetInterview\(true\)/);
  });
  it("resets one area with no confirm", () => {
    const m = /onResetArea: \(id\) =>[\s\S]*?\}\),\n/.exec(src);
    expect(m?.[0]).toContain("resetArea(id)");
    expect(m?.[0]).not.toMatch(/Confirm/);
  });
});

// ── PZ-11 step 4: the look ────────────────────────────────────────────────
describe("Forget everything leaves no old draft behind", () => {
  it("draws an empty Name input with Save disabled once the profile is empty", async () => {
    const { reconcileDraft } = await import("./lib/profile");
    // The member saw Name = Vijay, then forgot everything: the epoch moved on.
    expect(reconcileDraft("Vijay", { server: "Vijay", epoch: 0 }, "", 1)).toBe("");
    // A dirty draft is thrown away on a forget, and kept on an unrelated save.
    expect(reconcileDraft("Vi", { server: "Vijay", epoch: 0 }, "", 1)).toBe("");
    expect(reconcileDraft("Vi", { server: "Vijay", epoch: 0 }, "Vijay", 0)).toBeNull();
    const html = draw({ profile: EMPTY_PROFILE, epoch: 1 });
    const name = html.match(/<input[^>]*placeholder="What to call you"[^>]*>/)?.[0] ?? "";
    expect(name).toContain('value=""');
    // The Save button that follows the Name input is disabled.
    expect(html).toMatch(/placeholder="What to call you"[^>]*\/><button[^>]*disabled=""[^>]*>[\s\S]*?Save/);
  });

  it("every draft field reads its value through the one hook", () => {
    const src = readFileSync(new URL("./AssistantPane.tsx", import.meta.url), "utf-8");
    expect(src).not.toMatch(/useState\((p\.address|s\.language|f\.text|a\.alias|a\.email)/);
    expect(src).toMatch(/setEpoch\(\(n\) => n \+ 1\)/);
  });
});

describe("the page says what the assistant does with this data", () => {
  it("says when the assistant reads it, who sees it, and the learning state", () => {
    const on = draw({});
    expect(on).toContain("before each of your chats, on the web and on WhatsApp");
    expect(on).toContain("Other members never see it");
    expect(on).toContain("Learning is on");
    expect(draw({ profile: full })).toContain("Learning is off");
  });

  it("has jump links to the sections", () => {
    const html = draw({});
    expect(html).toContain('aria-label="Sections on this page"');
    for (const id of ["memory", "address", "style", "facts", "interview", "names", "download", "forget"]) {
      expect(html).toContain(`id="assistant-${id}"`);
    }
  });
});

describe("the name the assistant uses", () => {
  it("names the fallback and where it comes from, with a link to My Profile", () => {
    const html = draw({ fallbackName: { name: "Vijay", from: "preferred" } });
    expect(html).toContain('placeholder="Empty: Vijay"');
    expect(html).toContain("Empty: I call you Vijay, your preferred name in");
    expect(html).toContain('href="/people/me"');
  });

  it("states the plain rule when no read gave the value", () => {
    const html = draw({});
    expect(html).toContain("Empty: I use your preferred name");
    expect(html).toContain('href="/people/me"');
  });
});

describe("language, facts and interview, in plain words", () => {
  it("offers the language as a named list, and no codes in the label", () => {
    const html = draw({ profile: { ...EMPTY_PROFILE, style: { language: "kn" } } });
    expect(html).not.toContain("for example en or hi");
    expect(html).toContain("Kannada");
  });

  it("shows the question of each interview area under its label", () => {
    const html = draw({});
    expect(html).toContain("What is your role, and what takes most of your week?");
  });

  it("a fact row stacks its controls under the text on a phone", () => {
    const html = draw({ profile: full });
    expect(html).toContain("flex flex-col gap-2 sm:flex-row");
  });

  it("deletes a fact with an Undo toast that adds it back", () => {
    const src = readFileSync(new URL("./AssistantPane.tsx", import.meta.url), "utf-8");
    expect(src).toMatch(/label: "Undo"[\s\S]{0,120}onAddFact\(gone\.text, gone\.kind, gone\.area\)/);
    expect(src).toMatch(/title: "Name deleted"[\s\S]{0,260}onAddAlias\(gone\.alias, gone\.email\)/);
    expect(src).toContain("addFact(text, kind, area)");
  });
});
