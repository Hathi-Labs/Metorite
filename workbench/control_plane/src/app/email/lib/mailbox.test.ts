// WS-17 EM-T8a — send from the right mailbox (§11.7.1 of
// `project-docs/specs/email_app_master_plan.md`).
//
// R7 fences named here:
//   * `email-mailbox-of-mail`: an act on a mail takes the mailbox of the mail,
//     and the selection only fills in for a mail with no account id.
//   * `email-reply-own-addresses`: reply and reply-all leave out EVERY address
//     of the member, not only the selected one (D-EM-27, MB-7).
//   * `email-detail-no-selected-sender`: `EmailDetail.tsx` passes no bare
//     `selectedAccountId` to a send, a draft, a signature, a thread or a
//     recipient call (MB-2). The source fence fails when one comes back.
//   * `email-open-by-id-switches`: `openEmailById` selects the mailbox of a mail
//     from another mailbox before it shows it (MB-3).
//   * `email-compose-carries-mailbox`: the toolbar replies and the pop-out pass
//     the mailbox of the mail to the composer, and the page sends from it.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { mailboxOf, ownAddresses, replyRecipients } from "./mailbox";

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8");
/** Source with line comments and block comments removed. */
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

describe("the mailbox of a mail (D-EM-19)", () => {
  it("takes the account of the mail over the selection", () => {
    expect(mailboxOf({ accountId: "box-b" }, "box-a")).toBe("box-b");
  });
  it("falls back to the selection only when the mail has no account", () => {
    expect(mailboxOf({ accountId: "" }, "box-a")).toBe("box-a");
    expect(mailboxOf(null, "box-a")).toBe("box-a");
    expect(mailboxOf(undefined, null)).toBeNull();
  });
});

describe("the own addresses of the member (D-EM-27)", () => {
  it("lists every mailbox in lower case and skips a blank one", () => {
    const own = ownAddresses([
      { emailAddress: "Dana@Fracktal.in" },
      { emailAddress: " dana@outlook.com " },
      { emailAddress: "" },
      {},
    ]);
    expect([...own].sort()).toEqual(["dana@fracktal.in", "dana@outlook.com"]);
  });
});

describe("the recipients of a reply (MB-7)", () => {
  const own = ownAddresses([
    { emailAddress: "dana@fracktal.in" },
    { emailAddress: "dana@outlook.com" },
  ]);
  const mail = {
    from: { email: "ravi@contoso.test" },
    to: [{ email: "Dana@Fracktal.in" }, { email: "lee@contoso.test" }],
    cc: [
      { email: "dana@outlook.com" },
      { email: "LEE@contoso.test" },
      { email: "kim@contoso.test" },
    ],
  };

  it("answers the sender only on a reply", () => {
    expect(replyRecipients(mail, "reply", own)).toEqual({
      to: ["ravi@contoso.test"],
      cc: [],
    });
  });

  it("leaves out each address of the member on a reply-all, in any case", () => {
    const { to, cc } = replyRecipients(mail, "reply-all", own);
    expect(to).toEqual(["ravi@contoso.test", "lee@contoso.test"]);
    // The second mailbox of the member on Cc leaves too, and a duplicate of a
    // To address does not come back on Cc.
    expect(cc).toEqual(["kim@contoso.test"]);
  });

  it("answers the recipients of mail that the member sent", () => {
    const sent = {
      from: { email: "dana@outlook.com" },
      to: [{ email: "ravi@contoso.test" }, { email: "dana@fracktal.in" }],
      cc: [{ email: "kim@contoso.test" }],
    };
    expect(replyRecipients(sent, "reply", own)).toEqual({
      to: ["ravi@contoso.test"],
      cc: [],
    });
    expect(replyRecipients(sent, "reply-all", own)).toEqual({
      to: ["ravi@contoso.test"],
      cc: ["kim@contoso.test"],
    });
  });
});

describe("EmailDetail acts in the mailbox of the mail (MB-2)", () => {
  const src = codeOnly(read("components/EmailDetail.tsx"));

  it("derives the mailbox from the mail", () => {
    expect(src).toContain("const mailboxId = mailboxOf(email, selectedAccountId);");
  });

  it("passes no bare selectedAccountId to an account call", () => {
    // The one allowed read is the fallback inside `mailboxOf(...)`.
    const uses = src.match(/selectedAccountId/g) ?? [];
    expect(uses.length).toBe(2); // the store destructure and mailboxOf(...)
    for (const call of [
      "getSignatureText(selectedAccountId",
      "listThread(selectedAccountId",
      "sendDraft(selectedAccountId",
      "accountId: selectedAccountId",
      "accountId={selectedAccountId}",
    ]) {
      expect(src).not.toContain(call);
    }
  });

  it("builds reply recipients from every own address", () => {
    expect(src).toContain("const own = ownAddresses(accounts);");
    expect(src).not.toMatch(/ownEmail\b/);
    expect((src.match(/replyRecipients\(src, mode, own\)/g) ?? []).length).toBe(2);
  });

  it("hands the mailbox to the pop-out composer", () => {
    expect(src).toMatch(/openCompose\(\{\s*accountId: mailboxId \?\? undefined,/);
  });
});

describe("Open in inbox switches to the mailbox of the mail (MB-3)", () => {
  const src = codeOnly(read("lib/emailStore.ts"));
  const body = src.slice(src.indexOf("openEmailById: async"));
  const fn = body.slice(0, body.indexOf("toggleEmailSelected"));

  it("selects the mailbox of a mail from another mailbox, then the mail", () => {
    expect(fn).toContain("email.accountId !== get().selectedAccountId");
    const select = fn.indexOf("get().selectAccount(email.accountId)");
    const reselect = fn.indexOf("set({ selectedEmailId: id, viewerCommand: null })");
    expect(select).toBeGreaterThan(-1);
    expect(reselect).toBeGreaterThan(select);
  });

  it("switches only to a mailbox that the member has", () => {
    expect(fn).toContain("get().accounts.some((a) => a.id === email.accountId)");
  });
});

describe("the composer sends from the mailbox it was given (D-EM-20)", () => {
  const page = codeOnly(read("page.tsx"));

  it("sends from composeDefaults.accountId before the selection", () => {
    expect(page).toContain(
      "const composeAccountId = composeDefaults?.accountId || selectedAccountId;",
    );
    expect(page).toContain("accountId={composeAccountId ?? \"\"}");
    expect(page).toMatch(/await sendEmail\(\{\s*accountId: composeAccountId,/);
  });

  it("gives each toolbar reply and the forward the mailbox of the mail", () => {
    const n = (page.match(/accountId: email\.accountId \|\| undefined,/g) ?? []).length;
    expect(n).toBe(3);
  });

  it("restores the mailbox on an undo of a send", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    const undo = store.slice(store.indexOf("undoSend: () =>"));
    expect(undo.slice(0, undo.indexOf("replyToMessageId: p.replyToMessageId"))).toContain("accountId: p.accountId,");
  });
});
