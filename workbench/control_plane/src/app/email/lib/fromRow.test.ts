// WS-17 EM-T8c — the From row and the second connect (§11.4, §11.6 and
// §11.7.3 of `project-docs/specs/email_app_master_plan.md`, D-EM-20).
//
// R7 fences named here:
//   * `email-from-warning`: `fromWarning` gives one warning, in order: a reply
//     that leaves its conversation, a recipient last heard from another
//     mailbox, a recipient at the work domain of another mailbox. It never
//     offers a mailbox that needs a reconnect.
//   * `email-from-row`: the row shows only for two or more mailboxes, names
//     each mailbox by label and address, and marks a mailbox to reconnect.
//   * `email-from-composers`: both composers send, draft and sign from the
//     chosen mailbox. A reply from another mailbox goes as new mail, and the
//     draft of the old mailbox goes after the new one saved.
//   * `email-from-blocked`: a mailbox that needs a reconnect cannot send.
//   * `email-connect-return` (MB-9, §11.6 case 2): the callback selects the
//     mailbox of the connect, and names a mailbox that was already connected.
//   * `email-backfill-per-mailbox` (MB-10): "load older" keys its state by
//     mailbox and folder.
//   * `email-integrations-connect` (MB-16): Add on Integrations opens the
//     connect flow of Email, and Integrations offers no Gmail or IMAP leg.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FromRow } from "../components/FromRow";
import {
  rememberMailboxesBeforeConnect,
  wasConnectedBefore,
  withSelectedMailbox,
} from "./connect";
import { backfillKey } from "./emailStore";
import { fromWarning, needsReconnect, swapSignature } from "./mailbox";
import type { EmailAccount } from "./types";

const ROOT = join(__dirname, "..");
const read = (rel: string) =>
  readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const box = (over: Partial<EmailAccount>): EmailAccount => ({
  id: "x",
  provider: "microsoft",
  emailAddress: "x@x.test",
  label: "",
  unreadCount: 0,
  syncEnabled: true,
  ...over,
});

const work = box({ id: "work", emailAddress: "vj@fracktal.in", displayLabel: "Fracktal", workDomain: "fracktal.in" });
const home = box({ id: "home", emailAddress: "vj@outlook.com", displayLabel: "Personal", workDomain: null });
const side = box({ id: "side", emailAddress: "vj@constellation.io", displayLabel: "Constellation", workDomain: "constellation.io" });
const all = [work, home, side];

describe("the From warning (§11.4)", () => {
  it("says nothing for one mailbox", () => {
    expect(fromWarning({ fromId: "work", accounts: [work], recipients: ["a@b.test"], conversationAccountId: "home" })).toBeNull();
  });

  it("warns first when a reply leaves its conversation", () => {
    const w = fromWarning({
      fromId: "home", accounts: all, conversationAccountId: "work",
      recipients: ["kim@fracktal.in"], usualSender: { "kim@fracktal.in": "side" },
    });
    expect(w?.kind).toBe("conversation");
    expect(w?.switchTo).toBe("work");
    expect(w?.text).toContain("This conversation is in Fracktal.");
  });

  it("then names the mailbox that last wrote to a recipient, in any case", () => {
    const w = fromWarning({
      fromId: "home", accounts: all, recipients: [" Ravi@Contoso.test "],
      usualSender: { "ravi@contoso.test": "side" },
    });
    expect(w).toMatchObject({ kind: "usual", switchTo: "side", switchLabel: "Constellation" });
  });

  it("then warns about a work domain of another mailbox", () => {
    const w = fromWarning({ fromId: "home", accounts: all, recipients: ["kim@fracktal.in"] });
    expect(w).toMatchObject({ kind: "domain", switchTo: "work" });
    expect(w?.text).toBe("You are writing to a fracktal.in address from Personal.");
  });

  it("says nothing when the From mailbox fits", () => {
    expect(fromWarning({
      fromId: "work", accounts: all, conversationAccountId: "work",
      recipients: ["kim@fracktal.in"], usualSender: { "kim@fracktal.in": "work" },
    })).toBeNull();
    // A consumer domain has no work domain, so it never warns.
    expect(fromWarning({ fromId: "work", accounts: all, recipients: ["bob@outlook.com"] })).toBeNull();
  });

  it("never offers a mailbox that needs a reconnect", () => {
    const broken = { ...work, syncStatus: "error" };
    expect(fromWarning({
      fromId: "home", accounts: [broken, home, side], conversationAccountId: "work",
      recipients: ["kim@fracktal.in"],
    })).toBeNull();
    expect(fromWarning({
      fromId: "home", accounts: all, recipients: ["kim@fracktal.in"],
      authErrors: { work: "401" },
    })).toBeNull();
  });
});

describe("a mailbox that needs a reconnect", () => {
  it("follows the rule of the reconnect banner", () => {
    expect(needsReconnect({ id: "a", syncStatus: "error" }, {})).toBe(true);
    expect(needsReconnect({ id: "a", syncStatus: "idle" }, { a: "401" })).toBe(true);
    expect(needsReconnect({ id: "a", syncStatus: "idle" }, {})).toBe(false);
    expect(needsReconnect(null, {})).toBe(false);
  });
});

describe("the signature follows the From mailbox", () => {
  it("swaps an unchanged signature and keeps a body without it", () => {
    expect(swapSignature("Hi\n\n-- VJ, Fracktal", "-- VJ, Fracktal", "-- VJ")).toBe("Hi\n\n-- VJ");
    expect(swapSignature("Hi\n\n-- VJ, Fracktal", "-- VJ, Fracktal", "")).toBe("Hi");
    expect(swapSignature("Hi, edited", "-- VJ, Fracktal", "-- VJ")).toBe("Hi, edited");
  });
});

describe("the From row", () => {
  const render = (accounts: EmailAccount[], authErrors: Record<string, string> = {}) =>
    renderToStaticMarkup(createElement(FromRow, {
      accounts, value: "home", onChange: () => {}, warning: null, authErrors,
    }));

  it("draws nothing for one mailbox", () => {
    expect(render([home])).toBe("");
  });

  it("names each mailbox by label and address", () => {
    const html = render(all);
    expect(html).toContain(">From<");
    expect(html).toContain("Fracktal · vj@fracktal.in");
    expect(html).toContain('aria-label="Send from"');
  });

  it("marks a mailbox to reconnect, and blocks it when it sends", () => {
    const html = render(all, { work: "401" });
    expect(html).toContain("Fracktal · vj@fracktal.in (reconnect to send)");
    const blocked = render(all, { home: "401" });
    expect(blocked).toContain("Reconnect Personal to send from it");
  });
});

describe("both composers act in the chosen mailbox (D-EM-20)", () => {
  const compose = codeOnly(read("components/ComposePanel.tsx"));
  const detail = codeOnly(read("components/EmailDetail.tsx"));

  it("draws the From row", () => {
    expect(compose).toContain("<FromRow");
    expect(detail).toContain("<FromRow");
  });

  it("drops the reply target when the From leaves the conversation", () => {
    expect(compose).toContain("const replyTarget = sameConversation ? replyToMessageId : undefined;");
    expect(detail).toMatch(/replyToMessageId: isForward \|\| !sameConversation \? undefined : target\.id/);
    expect(detail).toMatch(/replyToMessageId: isForward \|\| !sameConversation \? undefined : target\.providerMessageId/);
  });

  it("sends, drafts and signs from the chosen mailbox", () => {
    expect(compose).toContain("accountId: fromId,");
    expect(compose).toContain("await sendDraft(fromId, saved.id);");
    expect(compose).toContain("void getSignatureText(fromId)");
    expect(detail).toContain("await sendDraft(fromId, saved.id);");
    expect(detail).toContain("void getSignatureText(fromId)");
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain("await sendEmail({ ...params, accountId: sender });");
  });

  it("deletes the draft of the old mailbox once the new one saved", () => {
    for (const src of [compose, detail]) {
      expect(src).toContain("staleDraftRef.current = draftIdRef.current;");
      expect(src).toContain("void deleteEmail(staleDraftRef.current);");
    }
  });

  it("refuses a send from a mailbox that needs a reconnect", () => {
    expect(compose).toContain("if (fromBlocked) {");
    expect(detail).toContain("if (needsReconnect(fromAccount, authErrors)) {");
  });
});

describe("the return after a connect (MB-9)", () => {
  const memory = () => {
    const m = new Map<string, string>();
    return {
      getItem: (k: string) => m.get(k) ?? null,
      setItem: (k: string, v: string) => void m.set(k, v),
    };
  };

  it("selects the mailbox of the connect, over an old selection", () => {
    expect(withSelectedMailbox("/email?account=old&folder=inbox", "new")).toBe(
      "/email?account=new&folder=inbox");
    expect(withSelectedMailbox("/email", "new")).toBe("/email?account=new");
    expect(withSelectedMailbox("/email", null)).toBe("/email");
  });

  it("tells a reconnect from a new mailbox", () => {
    const store = memory();
    rememberMailboxesBeforeConnect(["a", "b"], store);
    expect(wasConnectedBefore("a", store)).toBe(true);
    expect(wasConnectedBefore("c", store)).toBe(false);
    expect(wasConnectedBefore(null, store)).toBe(false);
    expect(wasConnectedBefore("a", null)).toBe(false);
  });

  it("is wired into the connect and the callback page", () => {
    const page = codeOnly(read("page.tsx"));
    expect(page).toContain(
      "rememberMailboxesBeforeConnect(useEmailStore.getState().accounts.map((a) => a.id));");
    const callback = codeOnly(read("oauth/callback/page.tsx"));
    expect(callback).toContain("withSelectedMailbox(safeRedirectTarget(redirectAfter), accountId)");
    expect(callback).toContain("wasConnectedBefore(accountId)");
  });
});

describe("load older keeps its state per mailbox (MB-10)", () => {
  it("keys by mailbox and folder", () => {
    expect(backfillKey("a", "inbox")).not.toBe(backfillKey("b", "inbox"));
    expect(backfillKey(null, "inbox")).toBe(":inbox");
  });

  it("reads and writes the state through the key", () => {
    const store = codeOnly(read("lib/emailStore.ts"));
    expect(store).toContain("const key = backfillKey(selectedAccountId, selectedFolder);");
    expect(store).not.toMatch(/backfillToken\[selectedFolder\]|\[selectedFolder\]: res\./);
    expect(codeOnly(read("page.tsx"))).toContain(
      "!backfillExhausted[backfillKey(selectedAccountId, selectedFolder)]");
  });
});

describe("Add on Integrations opens the connect flow of Email (MB-16)", () => {
  const integrations = codeOnly(
    readFileSync(join(ROOT, "..", "integrations", "page.tsx"), "utf-8").replace(/\r\n/g, "\n"),
  );

  it("routes every Add button to /email?connect=1", () => {
    expect(integrations).toContain('window.location.assign("/email?connect=1");');
    expect((integrations.match(/onClick=\{connectInEmail\}/g) ?? []).length).toBe(3);
  });

  it("offers no Gmail or IMAP leg of its own (D-EM-5)", () => {
    expect(integrations).not.toContain("AddEmailModal");
    expect(integrations).not.toContain("AddIMAPModal");
    expect(integrations).not.toContain('onConnect("imap")');
  });
});
