// Task 2267 (WS-17 EM-G11c2): the Auto-run notice of an existing Gmail mailbox.
//
// vitest here runs in the node environment. The decisions run from
// `autoRunNotice.ts` with a fake API and a fake store, and the view is drawn
// with `createElement`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AutoRunNoticeView } from "../components/AutoRunNotice";
import {
  AUTO_RUN_NOT_NOW_KEY,
  autoRunNoticeText,
  loadAutoRunNotice,
  readAutoRunNotNow,
  rememberAutoRunNotNow,
  turnOnAutoRun,
  type AutoRunNoticeApi,
} from "./autoRunNotice";
import type { AssistantSettings, AutomationRule } from "./types";

const GMAIL = { id: "acc-1", provider: "gmail" as const };

function fake(settings: Partial<AssistantSettings>, rules: Array<Partial<AutomationRule>>) {
  const calls: Array<[string, unknown]> = [];
  const api: AutoRunNoticeApi = {
    getAssistantSettings: async (id) => {
      calls.push(["get", id]);
      return { account_id: id, auto_run: false, ...settings } as AssistantSettings;
    },
    listRules: async (id) => {
      calls.push(["rules", id]);
      return rules as AutomationRule[];
    },
    saveAssistantSettings: async (s) => {
      calls.push(["save", s]);
      return s;
    },
  };
  return { api, calls };
}

const NO_CHOICE = { auto_run: false, auto_run_chosen: false };
const TWO_ON = [{ enabled: true }, { enabled: true }, { enabled: false }];

function memoryStore(initial: string | null = null) {
  let value = initial;
  return {
    getItem: () => value,
    setItem: (_k: string, v: string) => {
      value = v;
    },
  };
}

describe("who sees the notice", () => {
  it("Gmail, an enabled rule and no stored choice: it shows the count of ENABLED rules", async () => {
    const { api } = fake(NO_CHOICE, TWO_ON);
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBe(2);
  });

  it("makes no write while it decides", async () => {
    const { api, calls } = fake(NO_CHOICE, TWO_ON);
    await loadAutoRunNotice(api, GMAIL, []);
    expect(calls.map((c) => c[0])).not.toContain("save");
  });

  it("no enabled rule: nothing", async () => {
    const { api } = fake(NO_CHOICE, [{ enabled: false }]);
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBeNull();
  });

  it("a stored OFF on purpose: nothing", async () => {
    const { api } = fake({ auto_run: false, auto_run_chosen: true }, TWO_ON);
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBeNull();
  });

  it("an older server that sends no auto_run_chosen: nothing", async () => {
    const { api } = fake({ auto_run: false }, TWO_ON);
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBeNull();
  });

  it("Outlook and IMAP: nothing, and no call at all", async () => {
    for (const provider of ["microsoft", "imap"] as const) {
      const { api, calls } = fake(NO_CHOICE, TWO_ON);
      expect(await loadAutoRunNotice(api, { id: "acc-2", provider }, [])).toBeNull();
      expect(calls).toEqual([]);
    }
  });

  it("a mailbox with Not now: nothing", async () => {
    const { api } = fake(NO_CHOICE, TWO_ON);
    expect(await loadAutoRunNotice(api, GMAIL, ["acc-1"])).toBeNull();
    expect(await loadAutoRunNotice(api, GMAIL, ["acc-9"])).toBe(2);
  });

  it("a failed read shows nothing", async () => {
    const { api } = fake(NO_CHOICE, TWO_ON);
    api.listRules = async () => {
      throw new Error("503");
    };
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBeNull();
  });
});

describe("Turn on", () => {
  it("saves auto_run: true through the settings save and changes no other field", async () => {
    const { api, calls } = fake({ ...NO_CHOICE, about: "Ravi" }, TWO_ON);
    await turnOnAutoRun(api, "acc-1");
    expect(calls.map((c) => c[0])).toEqual(["get", "save"]);
    expect(calls[1][1]).toMatchObject({ account_id: "acc-1", about: "Ravi", auto_run: true });
  });

  it("sends the marker of the member's choice, so the server stamps it and the notice goes (task 2305)", async () => {
    const { api, calls } = fake(NO_CHOICE, TWO_ON);
    await turnOnAutoRun(api, "acc-1");
    expect(calls[1][1]).toMatchObject({ auto_run: true, auto_run_choice: true });
  });
});

describe("Not now", () => {
  it("is remembered for each mailbox", () => {
    const store = memoryStore();
    rememberAutoRunNotNow("acc-1", store);
    rememberAutoRunNotNow("acc-2", store);
    expect(readAutoRunNotNow(store)).toEqual(["acc-1", "acc-2"]);
    expect(AUTO_RUN_NOT_NOW_KEY).toBe("metorite.email.autoRunNotNow");
  });

  it("storage that throws still gives the list and never throws", () => {
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    expect(readAutoRunNotNow(broken)).toEqual([]);
    expect(rememberAutoRunNotNow("acc-1", broken)).toEqual(["acc-1"]);
    expect(readAutoRunNotNow(null)).toEqual([]);
    expect(rememberAutoRunNotNow("acc-1", null)).toEqual(["acc-1"]);
  });

  it("text that is not a list reads as empty", () => {
    expect(readAutoRunNotNow(memoryStore("{oops"))).toEqual([]);
    expect(readAutoRunNotNow(memoryStore('{"a":1}'))).toEqual([]);
  });
});

describe("the notice draws", () => {
  const draw = (over: Partial<Parameters<typeof AutoRunNoticeView>[0]> = {}) =>
    renderToStaticMarkup(
      createElement(AutoRunNoticeView, {
        address: "ravi@contoso.test",
        count: 3,
        busy: false,
        error: null,
        onTurnOn: () => {},
        onNotNow: () => {},
        ...over,
      }),
    );

  it("says the count and the address, and offers Turn on and Not now", () => {
    const out = draw();
    expect(out).toContain("Your 3 rules are not sorting new mail in ravi@contoso.test.");
    expect(out).toMatch(/<button[^>]*>[\s\S]*?Turn on[\s\S]*?<\/button>/);
    expect(out).toMatch(/<button[^>]*>[\s\S]*?Not now[\s\S]*?<\/button>/);
  });

  it("one rule reads in the singular", () => {
    expect(autoRunNoticeText(1, "a@b.test")).toBe("Your rule is not sorting new mail in a@b.test.");
  });

  it("a failed save says what happened and the next step", () => {
    const out = draw({
      error: "Metorite could not turn on your rules for new mail, so nothing changed. Press Turn on to try again.",
    });
    expect(out).toContain('role="alert"');
    expect(out).toContain("nothing changed");
    expect(out).toContain("Press Turn on to try again");
  });

  it("uses the look of the storage notice: the card surface, no hand-rolled colour", () => {
    const out = draw();
    expect(out).toContain("border-b border-border bg-card");
    expect(out).not.toMatch(/#[0-9a-f]{3,6}\b|rgb\(/i);
  });
});

describe("the page wiring", () => {
  it("draws it for the Gmail mailbox in view, after the setup ends, in the notice stack", () => {
    const page = readFileSync(join(__dirname, "..", "page.tsx"), { encoding: "utf-8" });
    expect(page).toMatch(
      /selectedAccount && selectedAccount\.provider === "gmail" && setupStage !== "rules" && \(\s*<AutoRunNotice/,
    );
    expect(page.indexOf("<StorageNotice")).toBeLessThan(page.indexOf("<AutoRunNotice"));
  });
});

// Task 2451: the count follows the rules in the same session.
describe("the count follows a rule change", () => {
  it("a read after a rule is turned off gives the new number, and nothing at 0", async () => {
    const rules: Array<Partial<AutomationRule>> = [{ enabled: true }, { enabled: true }, { enabled: true }];
    const { api } = fake(NO_CHOICE, rules);
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBe(3);
    rules[2].enabled = false;
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBe(2);
    rules[0].enabled = false;
    rules[1].enabled = false;
    expect(await loadAutoRunNotice(api, GMAIL, [])).toBeNull();
  });

  it("the signal reaches a listener once per announce, and stops after unsubscribe", async () => {
    const { announceRulesChanged, onRulesChanged } = await import("./rulesChanged");
    const target = new EventTarget();
    let reads = 0;
    const stop = onRulesChanged(() => reads++, target);
    announceRulesChanged(target);
    announceRulesChanged(target);
    expect(reads).toBe(2);
    stop();
    announceRulesChanged(target);
    expect(reads).toBe(2);
  });

  it("the notice listens for the signal, and every rule writer announces it", () => {
    const view = readFileSync(join(__dirname, "..", "components", "AutoRunNotice.tsx"), { encoding: "utf-8" });
    expect(view).toMatch(/onRulesChanged\(read\)/);
    const api = readFileSync(join(__dirname, "api.ts"), { encoding: "utf-8" });
    const writers = ["createRule", "updateRule", "deleteRule", "generateRules", "installPresetRules", "copyRules", "resetRules"];
    for (const fn of writers) {
      const start = api.indexOf(`export async function ${fn}(`);
      const end = api.indexOf("\nexport ", start + 10);
      expect(api.slice(start, end), fn).toContain("announceRulesChanged()");
    }
  });
});
