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
//   * `email-draftcard-own-addresses`: the in-thread draft card builds its
//     recipients with `replyRecipients` and every own address (MB-7).
//   * `email-open-by-id-mobile`: on a phone, a mail that "Open in inbox"
//     opened in its own mailbox keeps the detail view.
//   * `email-integrations-reconnect-hint`: Reconnect on Integrations sends the
//     mailbox as `login_hint`, so the member gets no account picker (MB-1).
//   * `email-draftcard-start` (WS-17 EM-T10): a draft card starts with the
//     To, Cc and Bcc of its draft, and with the toggle that matches them.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import {
  draftRecipients,
  mailboxOf,
  mailboxToOpen,
  ownAddresses,
  replyRecipients,
} from "./mailbox";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
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

  it("answers the other mailbox when it sent the mail (A reads mail from B)", () => {
    const fromB = {
      from: { email: "dana@outlook.com" },
      to: [{ email: "dana@fracktal.in" }],
      cc: [{ email: "kim@contoso.test" }],
    };
    expect(replyRecipients(fromB, "reply", own, "dana@fracktal.in")).toEqual({
      to: ["dana@outlook.com"],
      cc: [],
    });
    expect(replyRecipients(fromB, "reply-all", own, "dana@fracktal.in")).toEqual({
      to: ["dana@outlook.com"],
      cc: ["kim@contoso.test"],
    });
  });

  it("answers the other own mailbox of mail that A sent to B", () => {
    const aToB = {
      from: { email: "dana@fracktal.in" },
      to: [{ email: "dana@outlook.com" }],
    };
    for (const mode of ["reply", "reply-all"] as const) {
      expect(replyRecipients(aToB, mode, own, "dana@fracktal.in").to).toEqual([
        "dana@outlook.com",
      ]);
    }
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
    expect(
      (src.match(/replyRecipients\(src, mode, own, sendingAddress\)/g) ?? []).length,
    ).toBe(2);
  });

  it("hands the mailbox to the pop-out composer", () => {
    expect(src).toMatch(/openCompose\(\{\s*accountId: mailboxId \?\? undefined,/);
  });
});

describe("the in-thread draft card leaves out every own address (MB-7)", () => {
  const src = codeOnly(read("components/ConversationView.tsx"));

  it("builds both recipient rows with replyRecipients and every own address", () => {
    expect(src).toContain("const own = ownAddresses(accounts);");
    expect(src).toContain(
      'replyRecipients(replyTo, "reply-all", own, sendingAddress)',
    );
    expect(src).toContain('replyRecipients(replyTo, "reply", own, sendingAddress)');
    expect(src).not.toMatch(/ownEmail\b/);
  });
});

describe("email-draftcard-start: a draft card starts with the recipients of its draft (EM-T10)", () => {
  const own = ownAddresses([{ emailAddress: "dana@fracktal.in" }]);
  const party = (email: string, name = "") => ({ name, email });
  /** The lists that a card computes from its reply target, as the card does. */
  const target = (src: Parameters<typeof replyRecipients>[0]) => {
    const all = replyRecipients(src, "reply-all", own, "dana@fracktal.in");
    const only = replyRecipients(src, "reply", own, "dana@fracktal.in");
    return [all, only.to] as const;
  };
  // Ravi wrote to the member and Asha, with Kiran on Cc.
  const group = target({
    from: party("ravi@acme.com", "Ravi"),
    to: [party("dana@fracktal.in"), party("asha@acme.com")],
    cc: [party("kiran@acme.com")],
  });

  it("keeps a reply that the member narrowed to the sender, on Reply", () => {
    const draft = { to: [party("ravi@acme.com", "Ravi")], cc: [], bcc: [] };
    expect(draftRecipients(draft, ...group)).toEqual({
      to: ["ravi@acme.com"], cc: [], bcc: [], replyAll: false, showCc: false,
    });
  });

  it("keeps the Bcc of a reply, and shows its row", () => {
    const draft = { to: [party("ravi@acme.com")], bcc: [party("boss@fracktal.in")] };
    expect(draftRecipients(draft, ...group)).toEqual({
      to: ["ravi@acme.com"], cc: [], bcc: ["boss@fracktal.in"], replyAll: false, showCc: true,
    });
  });

  it("marks neither button for a reply to the sender that also has a Cc", () => {
    // Reply needs an empty Cc (§10.4.11 item 2). A Cc that the member added to
    // a narrowed reply is no Reply and no Reply All (EM-T10 review round 1).
    const draft = { to: [party("ravi@acme.com")], cc: [party("kiran@acme.com")], bcc: [] };
    expect(draftRecipients(draft, ...group)).toEqual({
      to: ["ravi@acme.com"], cc: ["kiran@acme.com"], bcc: [], replyAll: null, showCc: true,
    });
  });

  it("keeps a draft with only a Bcc, and marks neither button", () => {
    const draft = { to: [], cc: [], bcc: [party("boss@fracktal.in")] };
    expect(draftRecipients(draft, ...group)).toEqual({
      to: [], cc: [], bcc: ["boss@fracktal.in"], replyAll: null, showCc: true,
    });
  });

  it("starts a draft with no recipient on the reply-all lists, or empty with no target", () => {
    const draft = { to: [], cc: [], bcc: [] };
    expect(draftRecipients(draft, ...group)).toEqual({
      to: ["ravi@acme.com", "asha@acme.com"], cc: ["kiran@acme.com"], bcc: [],
      replyAll: true, showCc: true,
    });
    expect(draftRecipients(draft, null, null)).toEqual({
      to: [], cc: [], bcc: [], replyAll: null, showCc: false,
    });
  });

  it("keeps the Cc of a new mail, which has no reply target", () => {
    const draft = { to: [party("asha@acme.com")], cc: [party("kiran@acme.com")] };
    expect(draftRecipients(draft, null, null)).toEqual({
      to: ["asha@acme.com"], cc: ["kiran@acme.com"], bcc: [], replyAll: null, showCc: true,
    });
  });

  it("opens an AI draft, which addresses the sender only, on Reply", () => {
    // The gateway saves an AI draft with the sender as the one To (C9).
    const draft = { to: [party("ravi@acme.com")], cc: null, bcc: null };
    const start = draftRecipients(draft, ...group);
    expect(start.to).toEqual(["ravi@acme.com"]);
    expect(start.replyAll).toBe(false);
  });

  it("starts a thread of two people on Reply All, as before", () => {
    const pair = target({ from: party("ravi@acme.com"), to: [party("dana@fracktal.in")] });
    const draft = { to: [party("ravi@acme.com")] };
    expect(draftRecipients(draft, ...pair)).toEqual({
      to: ["ravi@acme.com"], cc: [], bcc: [], replyAll: true, showCc: true,
    });
  });

  it("keeps the lists of a draft whose reply target changed, and marks neither", () => {
    // A newer mail from Dev is the target now. The draft answered Ravi's.
    const newer = target({ from: party("dev@acme.com"), to: [party("dana@fracktal.in")] });
    const draft = {
      to: [party("ravi@acme.com"), party("asha@acme.com")],
      cc: [party("kiran@acme.com")],
    };
    expect(draftRecipients(draft, ...newer)).toEqual({
      to: ["ravi@acme.com", "asha@acme.com"], cc: ["kiran@acme.com"], bcc: [],
      replyAll: null, showCc: true,
    });
  });

  it("ignores the case, the spaces, the order and the names of the addresses", () => {
    const draft = {
      to: [party(" Asha@ACME.com", "Asha"), party("RAVI@acme.com ")],
      cc: [party("Kiran@Acme.Com", "K")],
    };
    const start = draftRecipients(draft, ...group);
    expect(start.replyAll).toBe(true);
    // The card shows the addresses as the row holds them.
    expect(start.to).toEqual(["Asha@ACME.com", "RAVI@acme.com"]);
    expect(start.cc).toEqual(["Kiran@Acme.Com"]);
  });

  it("marks neither button for a forward, or for a To that the member edited", () => {
    const forward = { to: [party("lee@other.org")] };
    expect(draftRecipients(forward, ...group).replyAll).toBeNull();
    const edited = { to: [party("ravi@acme.com"), party("lee@other.org")] };
    const start = draftRecipients(edited, ...group);
    expect(start.replyAll).toBeNull();
    // A reply that does not start on Reply shows its Cc row.
    expect(start.showCc).toBe(true);
  });
});

describe("Open in inbox switches to the mailbox of the mail (MB-3)", () => {
  const src = codeOnly(read("lib/emailStore.ts"));
  const body = src.slice(src.indexOf("openEmailById: async"));
  const fn = body.slice(0, body.indexOf("toggleEmailSelected"));

  // EM-T8g-2 moved the rule into `mailboxToOpen`, a pure function in
  // `lib/mailbox.ts`. These cases test it by behaviour, and the scan proves
  // that `openEmailById` reads it.
  const one = { viewAll: false, selectedAccountId: "a", accounts: [{ id: "a" }, { id: "b" }] };

  it("selects the mailbox of a mail from another mailbox, then the mail", () => {
    expect(mailboxToOpen(one, "b")).toBe("b");
    expect(mailboxToOpen(one, "a")).toBeNull();
    expect(fn).toContain("const target = mailboxToOpen(get(), email.accountId);");
    const select = fn.indexOf("get().selectAccount(target)");
    const reselect = fn.indexOf("set({ selectedEmailId: id, viewerCommand: null })");
    expect(select).toBeGreaterThan(-1);
    expect(reselect).toBeGreaterThan(select);
  });

  it("switches only to a mailbox that the member has", () => {
    expect(mailboxToOpen(one, "gone")).toBeNull();
    expect(mailboxToOpen(one, null)).toBeNull();
    expect(mailboxToOpen({ ...one, viewAll: true }, "gone")).toBeNull();
  });

  it("drops an opened mail when its mailbox is removed", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toContain(
      "set({ selectedAccountId: next, selectedEmailId: null, selectedEmailOverride: null });",
    );
  });

  it("keeps the detail view on a phone after the switch", () => {
    const page = codeOnly(read("page.tsx"));
    // EM-T8d adds the scope (viewAll) to the deps of the same effect.
    const at = page.indexOf("setMobileView(\"inbox\");\n  }, [selectedFolder, selectedAccountId, viewAll]);");
    expect(at).toBeGreaterThan(-1);
    const effect = page.slice(page.lastIndexOf("useEffect(() => {", at), at);
    expect(effect).toContain("if (useEmailStore.getState().selectedEmailOverride) return;");
  });
});

describe("Reconnect on Integrations names the mailbox (MB-1)", () => {
  const integrations = codeOnly(
    readFileSync(join(ROOT, "..", "integrations", "page.tsx"), "utf-8").replace(/\r\n/g, "\n"),
  );

  it("sends the mailbox address as login_hint", () => {
    expect(integrations).toContain('if (loginHint) params.set("login_hint", loginHint);');
    expect(integrations).toContain("handleConnect(provider, account.emailAddress);");
    expect(integrations).not.toContain("handleReconnect(account.provider)");
  });
});

describe("the composer sends from the mailbox it was given (D-EM-20)", () => {
  const page = codeOnly(read("page.tsx"));

  it("sends from composeDefaults.accountId before the selection", () => {
    // EM-T8d: new mail in All inboxes goes from the default mailbox.
    expect(page).toMatch(
      /const composeAccountId =\s*composeDefaults\?\.accountId \|\|\s*\(viewAll \? defaultAccountId \|\| selectedAccountId : selectedAccountId\);/,
    );
    expect(page).toContain("accountId={composeAccountId ?? \"\"}");
    // EM-T8c: the From row of the composer may choose another mailbox, and
    // its choice wins. The composer mailbox is only the fallback.
    expect(page).toContain("const sender = params.accountId || composeAccountId;");
    expect(page).toContain("await sendEmail({ ...params, accountId: sender });");
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
