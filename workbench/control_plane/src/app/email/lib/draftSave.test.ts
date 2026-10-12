// WS-17 EM-G3a-f7 PR-C2 — a Gmail-web draft keeps its HTML in the DraftCard
// (§12.3.12.5 of `project-docs/specs/email_app_master_plan.md`).
//
// R7 fences named here (D-G3a-f7-9: each UI rule is a pure helper in
// `draftSave.ts`, and a source-text case proves that the card uses it):
//   * `keeps-html-when-the-gmail-text-is-unchanged` and
//     `drops-keep-html-after-a-text-edit` (acceptance 15)
//   * `the-pre-send-save-and-the-autosave-both-use-the-payload-helper`
//     (acceptance 16, source text)
//   * `an-outlook-draft-never-keeps-html` (acceptance 17)
//   * `a-gmail-html-draft-shows-the-notice` (acceptance 18)
//   * `a-lost-html-answer-shows-the-notice` (acceptance 19)
//   * `a-lost-pre-save-stops-the-send-until-the-confirm` (acceptance 20)
//   * `a-409-html-unavailable-asks-for-the-next-press` and
//     `the-second-press-allows-text-only` (acceptance 21)
//   * `a-gmail-draft-with-text-only-reads-its-html-once` (acceptance 22)
//   * `the-send-asks-send-after-pre-save` (acceptance 20, source text)
//   * `a-stopped-send-says-press-send-again` (acceptance 21a)
//
// Mutations M6, M7, M10 and M11 of the spec each turn one of these red.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { saveDraft, sendDraft } from "./api";
import {
  FORMATTING_NOTICE,
  SEND_STOPPED_NOTICE,
  draftSavePayload,
  draftText,
  isHtmlUnavailable,
  needsHtmlRead,
  noticeAfterSave,
  noticeWords,
  sendAfterPreSave,
  showsFormattingNotice,
} from "./draftSave";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

/** The DraftCard of ConversationView, as code with no comments. */
const card = (() => {
  const src = codeOnly(read("components/ConversationView.tsx"));
  const from = src.indexOf("export function DraftCard(");
  expect(from).toBeGreaterThanOrEqual(0);
  return src.slice(from);
})();

/** The part of `src` from `start` up to `end`. */
const between = (src: string, start: string, end: string) => {
  const from = src.indexOf(start);
  expect(from, `missing: ${start}`).toBeGreaterThanOrEqual(0);
  const to = src.indexOf(end, from + start.length);
  expect(to, `missing after ${start}: ${end}`).toBeGreaterThan(from);
  return src.slice(from, to);
};

/** Each call `name(…)` in `src`, from the name to its closing paren. */
const calls = (src: string, name: string): string[] => {
  const out: string[] = [];
  let at = src.indexOf(name);
  while (at >= 0) {
    let depth = 0;
    let end = at + name.length - 1;
    for (let i = end; i < src.length; i++) {
      if (src[i] === "(") depth++;
      else if (src[i] === ")" && --depth === 0) {
        end = i;
        break;
      }
    }
    out.push(src.slice(at, end + 1));
    at = src.indexOf(name, end + 1);
  }
  return out;
};

/** The JSON body of each request that a stubbed `fetch` got. */
const bodiesOf = (fetchMock: { mock: { calls: unknown[][] } }) =>
  fetchMock.mock.calls.map((c) => JSON.parse(String((c[1] as RequestInit).body)));

/** The fields of one DraftCard save, as the card passes them. */
const FIELDS = {
  accountId: "acc-1",
  draftId: "d-1",
  to: ["ravi@contoso.test"],
  cc: [],
  bcc: [],
  subject: "Re: BQ quote",
};
const OPENED = draftText("Thanks, see the plan.", "On Mon, Ravi wrote:\n> the quote");

describe("keeps-html-when-the-gmail-text-is-unchanged", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sets keepHtml for a Gmail draft whose text is as it opened", () => {
    const payload = draftSavePayload(FIELDS, OPENED, OPENED, "gmail");
    expect(payload).toEqual({ ...FIELDS, body: OPENED, keepHtml: true });
  });

  it("joins the text as the card does, so an unchanged card equals the text at open", () => {
    expect(draftText("Hi  \n", "> quote")).toBe("Hi\n\n> quote");
    expect(draftText("Hi  \n", "")).toBe("Hi  \n");
  });

  it("sends keep_html on the wire for a Gmail save with keepHtml", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "d-1", html: "kept" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const saved = await saveDraft(draftSavePayload(FIELDS, OPENED, OPENED, "gmail"));
    expect(bodiesOf(fetchMock)[0].keep_html).toBe(true);
    // `mapEmail` drops a field that it does not know, so `saveDraft` reads `html` itself.
    expect(saved.html).toBe("kept");
  });
});

describe("drops-keep-html-after-a-text-edit", () => {
  it("sends the text only after the member changes the text", () => {
    const edited = draftText("Thanks, see the new plan.", "On Mon, Ravi wrote:\n> the quote");
    const payload = draftSavePayload(FIELDS, OPENED, edited, "gmail");
    expect(payload).toEqual({ ...FIELDS, body: edited });
    expect("keepHtml" in payload).toBe(false);
  });

  it("does not keep the HTML while the text at open is not known yet", () => {
    expect("keepHtml" in draftSavePayload(FIELDS, null, "", "gmail")).toBe(false);
  });
});

describe("the-pre-send-save-and-the-autosave-both-use-the-payload-helper", () => {
  it("builds the body of each DraftCard save with draftSavePayload", () => {
    const saves = calls(card, "saveDraft(");
    // The autosave and the pre-send save. The destructure names no call.
    expect(saves.length).toBe(2);
    for (const save of saves) {
      expect(save).toMatch(/^saveDraft\(\s*draftSavePayload\(/);
    }
    const autosave = between(card, "autosave.schedule(", "const send = async");
    const send = between(card, "const send = async", "const discard = async");
    expect(calls(autosave, "saveDraft(").length).toBe(1);
    expect(calls(send, "saveDraft(").length).toBe(1);
  });

  it("compares with the text at open and sends the text of the card", () => {
    for (const save of calls(card, "saveDraft(")) {
      expect(save).toContain("openedText");
      expect(save).toContain("combinedBody()");
    }
    expect(card).toMatch(/const combinedBody = \(\) =>\s*draftText\(body, quote\);/);
  });
});

describe("an-outlook-draft-never-keeps-html", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("never sets keepHtml for an Outlook or an IMAP draft", () => {
    for (const provider of ["microsoft", "imap", undefined]) {
      const payload = draftSavePayload(FIELDS, OPENED, OPENED, provider);
      expect(payload, String(provider)).toEqual({ ...FIELDS, body: OPENED });
      expect("keepHtml" in payload, String(provider)).toBe(false);
    }
  });

  it("sends the bodies of main for an Outlook save and send", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ id: "d-1", sent: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await saveDraft(draftSavePayload(FIELDS, OPENED, OPENED, "microsoft"));
    await sendDraft("acc-1", "d-1", undefined);
    const bodies = bodiesOf(fetchMock);
    expect(Object.keys(bodies[0]).sort()).toEqual([
      "account_id", "artifacts", "attachments", "bcc", "body", "cc", "draft_id",
      "reply_to_message_id", "subject", "to",
    ]);
    expect(bodies[1]).toEqual({ account_id: "acc-1", draft_id: "d-1" });
  });

  it("asks the card to pass no allowTextOnly for a draft that is not Gmail", () => {
    const send = between(card, "const send = async", "const discard = async");
    expect(send).toMatch(/sendDraft\(accountId, draft\.id, gmail \? textOnlyConfirmed : undefined\)/);
  });
});

describe("a-gmail-html-draft-shows-the-notice", () => {
  it("shows for a Gmail draft with HTML, and for a failed read", () => {
    expect(showsFormattingNotice("gmail", "<p>Hi <img src=\"cid:ii_1\"></p>", false)).toBe(true);
    expect(showsFormattingNotice("gmail", null, true)).toBe(true);
  });

  it("does not show for an Outlook draft or a Gmail draft with text only", () => {
    expect(showsFormattingNotice("microsoft", "<p>Hi</p>", false)).toBe(false);
    expect(showsFormattingNotice("microsoft", null, true)).toBe(false);
    expect(showsFormattingNotice("gmail", null, false)).toBe(false);
    expect(showsFormattingNotice("gmail", "   ", false)).toBe(false);
  });

  it("has the words of the E-D3 table", () => {
    expect(FORMATTING_NOTICE).toBe(
      "This draft has formatting or images from Gmail. If you change the text here, " +
        "Metorite saves the text only, and the formatting and images go.",
    );
  });

  it("draws the notice above the text box with the warning tokens", () => {
    const notice = between(card, "{showNotice && (", "<textarea");
    expect(notice).toContain("{noticeWords(sendStopped)}");
    expect(notice).toMatch(/border-warning\/30 bg-warning\/10/);
    expect(notice).toContain("text-warning");
    expect(card).toMatch(/showsFormattingNotice\(\s*provider,/);
  });
});

describe("a-lost-html-answer-shows-the-notice", () => {
  it("is true for html: lost only", () => {
    expect(noticeAfterSave({ html: "lost" })).toBe(true);
    expect(noticeAfterSave({ html: "kept" })).toBe(false);
    expect(noticeAfterSave({ html: "none" })).toBe(false);
    expect(noticeAfterSave({})).toBe(false);
  });

  it("is read after the autosave and after the pre-send save", () => {
    const autosave = between(card, "autosave.schedule(", "const send = async");
    const send = between(card, "const send = async", "const discard = async");
    expect(autosave).toContain("if (noticeAfterSave(saved)) setHtmlLost(true);");
    expect(send).toContain("if (noticeAfterSave(saved)) setHtmlLost(true);");
  });
});

describe("a-lost-pre-save-stops-the-send-until-the-confirm", () => {
  it("stops on lost with no confirm, and sends with the confirm", () => {
    expect(sendAfterPreSave({ html: "lost" }, false)).toBe("stop");
    expect(sendAfterPreSave({ html: "lost" }, true)).toBe("send");
  });

  it("sends for kept, none and an answer with no html", () => {
    for (const html of ["kept", "none"] as const) {
      expect(sendAfterPreSave({ html }, false)).toBe("send");
    }
    expect(sendAfterPreSave({}, false)).toBe("send");
  });
});

describe("a-409-html-unavailable-asks-for-the-next-press", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is true for the 409 of the send, as gatewayFetch throws it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ detail: { reason: "html_unavailable" } }), { status: 409 })));
    const err = await sendDraft("acc-1", "d-1", false).catch((e: unknown) => e);
    expect(isHtmlUnavailable(err)).toBe(true);
  });

  it("is false for each other error", () => {
    const error = (status: number, detail: unknown) =>
      Object.assign(new Error(typeof detail === "string" ? detail : "[object Object]"), { status, detail });
    expect(isHtmlUnavailable(error(409, "Draft is being sent"))).toBe(false);
    expect(isHtmlUnavailable(error(409, { reason: "other" }))).toBe(false);
    expect(isHtmlUnavailable(error(500, { reason: "html_unavailable" }))).toBe(false);
    expect(isHtmlUnavailable(new Error("html_unavailable"))).toBe(false);
    expect(isHtmlUnavailable(null)).toBe(false);
    expect(isHtmlUnavailable("html_unavailable")).toBe(false);
  });

  it("shows the notice and arms the next press, with no send error", () => {
    const send = between(card, "const send = async", "const discard = async");
    const handler = between(send, "} catch (err) {", "} finally {");
    expect(handler).toMatch(/if \(isHtmlUnavailable\(err\)\) stopSend\(\);\s*else setSendError\(sendFailureText\(err\)\);/);
    expect(card).toMatch(/textOnlyConfirmed\s*\|\|\s*htmlLost|htmlLost\s*\|\|\s*textOnlyConfirmed/);
  });
});

describe("the-second-press-allows-text-only", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends allow_text_only false on the first press and true on the next", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ sent: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await sendDraft("acc-1", "d-1", false);
    await sendDraft("acc-1", "d-1", true);
    const bodies = bodiesOf(fetchMock);
    expect(bodies[0]).toEqual({ account_id: "acc-1", draft_id: "d-1", allow_text_only: false });
    expect(bodies[1]).toEqual({ account_id: "acc-1", draft_id: "d-1", allow_text_only: true });
  });

  it("starts false, and only a stop or the 409 sets it true", () => {
    expect(card).toContain("const [textOnlyConfirmed, setTextOnlyConfirmed] = useState(false);");
    // One setter, inside `stopSend`, and the send calls `stopSend` twice.
    expect(card.match(/setTextOnlyConfirmed\(true\)/g)?.length).toBe(1);
    expect(card).not.toMatch(/setTextOnlyConfirmed\((?!true\))/);
    expect(card).toMatch(/const stopSend = \(\) => \{\s*setTextOnlyConfirmed\(true\);\s*setSendStopped\(true\);\s*\};/);
    const send = between(card, "const send = async", "const discard = async");
    expect(send.match(/stopSend\(\)/g)?.length).toBe(2);
  });
});

describe("a-gmail-draft-with-text-only-reads-its-html-once", () => {
  it("reads for a Gmail draft with no HTML, once", () => {
    expect(needsHtmlRead("gmail", undefined, false)).toBe(true);
    expect(needsHtmlRead("gmail", "", false)).toBe(true);
    expect(needsHtmlRead("gmail", undefined, true)).toBe(false);
    expect(needsHtmlRead("gmail", "<p>Hi</p>", false)).toBe(false);
    expect(needsHtmlRead("microsoft", undefined, false)).toBe(false);
    expect(needsHtmlRead(undefined, undefined, false)).toBe(false);
  });

  it("reads in an effect of its own, not after the early return of the hydrate", () => {
    const effects = calls(card, "useEffect(");
    const reads = effects.filter((e) => e.includes("needsHtmlRead("));
    expect(reads.length).toBe(1);
    const read = reads[0];
    // The hydrate effect returns early when the draft has text, so a read
    // there never runs for a Gmail-web draft (B5).
    expect(read).not.toContain("hydratedBody");
    expect(read).not.toContain("draft.bodyText");
    expect(read).toContain("fetchFullBody(draft.id)");
    // The guard is set before the read starts, so a second run reads nothing.
    expect(read).toMatch(/needsHtmlRead\(provider, draft\.bodyHtml, htmlReadStarted\.current\)\) return;\s*htmlReadStarted\.current = true;/);
    expect(calls(card, "needsHtmlRead(").length).toBe(1);
  });
});

describe("the-send-asks-send-after-pre-save", () => {
  it("asks sendAfterPreSave between the pre-send save and sendDraft", () => {
    const send = between(card, "const send = async", "const discard = async");
    const save = send.indexOf("saveDraft(");
    const ask = send.indexOf("sendAfterPreSave(saved, textOnlyConfirmed)");
    const go = send.indexOf("await sendDraft(");
    expect(save).toBeGreaterThanOrEqual(0);
    expect(ask).toBeGreaterThan(save);
    expect(go).toBeGreaterThan(ask);
    expect(between(send, "sendAfterPreSave(", "await sendDraft(")).toMatch(
      /=== "stop"\)\s*\{\s*stopSend\(\);\s*return;\s*\}/,
    );
  });
});

describe("a-stopped-send-says-press-send-again", () => {
  it("has the words of the E-D3 row, the stopped send", () => {
    expect(SEND_STOPPED_NOTICE).toBe(
      "Metorite could not keep the formatting and images of this draft. Press Send again to send the text only.",
    );
  });

  it("says the stop after a stopped send, and the general words before it", () => {
    expect(noticeWords(false)).toBe(FORMATTING_NOTICE);
    expect(noticeWords(true)).toBe(SEND_STOPPED_NOTICE);
    expect(noticeWords(true)).not.toBe(noticeWords(false));
  });

  it("shows the stop as an alert, until the next send or a text edit", () => {
    const notice = between(card, "{showNotice && (", "<textarea");
    expect(notice).toContain('role={sendStopped ? "alert" : "status"}');
    expect(notice).toContain("{noticeWords(sendStopped)}");
    // The next press clears the stop before its pre-send save.
    const send = between(card, "const send = async", "const discard = async");
    expect(send.indexOf("setSendStopped(false);")).toBeGreaterThanOrEqual(0);
    expect(send.indexOf("setSendStopped(false);")).toBeLessThan(send.indexOf("saveDraft("));
    // A text edit of the member, or of the AI, clears it.
    expect(card).toMatch(/onChange=\{\(e\) => \{ dirty\.current = true; setSendStopped\(false\); setBody\(e\.target\.value\); \}\}/);
    expect(between(card, "onBody: (next) =>", "setBody(next)")).toContain("setSendStopped(false);");
  });
});
